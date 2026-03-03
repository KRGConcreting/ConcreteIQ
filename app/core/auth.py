"""
Authentication for single-user system.

No user table needed - just verify against ADMIN_PASSWORD.
Session stored in signed cookie.
"""

from datetime import datetime, timedelta
from typing import Optional
import hmac
import secrets

import bcrypt

from fastapi import Request, HTTPException, Depends, Response
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.config import settings
from app.core.dates import sydney_now


# Session serializer
_serializer = URLSafeTimedSerializer(settings.secret_key)

# Cookie settings
SESSION_COOKIE_NAME = "ciq_session"
SESSION_MAX_AGE = settings.session_expire_hours * 3600  # Convert to seconds


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against bcrypt hash."""
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_session(response: Response, session_version: int = 1) -> None:
    """Create a new session cookie with embedded session version."""
    session_data = {
        "authenticated": True,
        "created_at": sydney_now().isoformat(),
        "session_id": secrets.token_hex(16),
        "session_version": session_version,
    }

    token = _serializer.dumps(session_data)

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
    )


def clear_session(response: Response) -> None:
    """Clear the session cookie."""
    response.delete_cookie(SESSION_COOKIE_NAME)


def get_session(request: Request) -> Optional[dict]:
    """Get session data from cookie."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return data
    except (BadSignature, SignatureExpired):
        return None


def is_authenticated(request: Request) -> bool:
    """Check if request is authenticated (basic cookie check, no version validation)."""
    session = get_session(request)
    return session is not None and session.get("authenticated", False)


async def _check_session_version(request: Request) -> bool:
    """Check if session version matches the current DB version.

    Gracefully returns True if the settings table is unreachable,
    so login isn't blocked by a missing/broken settings table.
    """
    session = get_session(request)
    if not session:
        return False

    session_ver = session.get("session_version", 1)

    try:
        # Lazy import to avoid circular dependencies
        from app.database import get_async_session
        from app.settings import service as settings_service

        async with get_async_session() as db:
            db_ver = await settings_service.get_setting(db, "security", "session_version")
            current_ver = int(db_ver) if db_ver else 1
    except Exception:
        # Settings table missing or DB error — allow session through
        return True

    return session_ver >= current_ver


def _is_ajax_request(request: Request) -> bool:
    """Detect AJAX/fetch requests (not browser page navigation)."""
    # API paths
    if request.url.path.startswith("/api/"):
        return True
    # Fetch requests include CSRF header or accept JSON
    if request.headers.get("X-CSRF-Token"):
        return True
    if "application/json" in request.headers.get("Accept", ""):
        return True
    # POST/PUT/DELETE with content-type are typically AJAX
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        ct = request.headers.get("Content-Type", "")
        if "multipart/form-data" in ct or "application/json" in ct:
            return True
    return False


async def require_login(request: Request) -> None:
    """
    Dependency that requires authentication.
    Raises HTTPException if not authenticated.
    Validates session version to support forced logout.
    """
    if not is_authenticated(request):
        if _is_ajax_request(request):
            raise HTTPException(status_code=401, detail="Session expired — please log in again")
        # For page navigation, redirect to login (302 changes POST to GET)
        raise HTTPException(
            status_code=302,
            headers={"Location": f"/login?next={request.url.path}"}
        )

    # Check session version (for forced logout / session revocation)
    if not await _check_session_version(request):
        if _is_ajax_request(request):
            raise HTTPException(status_code=401, detail="Session expired — please log in again")
        raise HTTPException(
            status_code=302,
            headers={"Location": "/login?next=/dashboard"}
        )


async def get_current_user(request: Request) -> dict:
    """
    Dependency that returns current user info.
    For single-user system, just returns a dict with basic info.
    """
    await require_login(request)
    return {
        "name": settings.business_name,
        "email": settings.business_email,
    }


# CSRF Protection
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_FORM_FIELD = "csrf_token"


def generate_csrf_token() -> str:
    """Generate a new CSRF token."""
    return secrets.token_hex(32)


def get_csrf_token(request: Request) -> str:
    """Get or create CSRF token for request."""
    token = request.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        token = generate_csrf_token()
    return token


def set_csrf_cookie(response: Response, token: str) -> None:
    """Set CSRF token cookie."""
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,  # Needs to be readable by JS
        secure=settings.environment == "production",
        samesite="lax",
    )


async def verify_csrf(request: Request) -> None:
    """
    Dependency that verifies CSRF token for state-changing requests.
    Token can be in header or form data.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not cookie_token:
        raise HTTPException(status_code=403, detail="CSRF token missing")
    
    # Check header first (constant-time comparison to prevent timing attacks)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if header_token and hmac.compare_digest(header_token, cookie_token):
        return

    # Check form data (constant-time comparison)
    try:
        form = await request.form()
        form_token = form.get(CSRF_FORM_FIELD)
        if form_token and hmac.compare_digest(form_token, cookie_token):
            return
    except Exception:
        pass
    
    raise HTTPException(status_code=403, detail="CSRF token invalid")


# Rate Limiting (simple in-memory)
_login_attempts: dict[str, list[datetime]] = {}
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def check_login_rate_limit(ip: str) -> bool:
    """Check if IP is rate limited. Returns True if allowed."""
    now = sydney_now()
    cutoff = now - timedelta(minutes=LOCKOUT_MINUTES)
    
    # Get attempts for this IP
    attempts = _login_attempts.get(ip, [])
    
    # Filter to recent attempts
    recent = [a for a in attempts if a > cutoff]
    _login_attempts[ip] = recent
    
    return len(recent) < MAX_LOGIN_ATTEMPTS


def record_login_attempt(ip: str) -> None:
    """Record a failed login attempt."""
    if ip not in _login_attempts:
        _login_attempts[ip] = []
    _login_attempts[ip].append(sydney_now())


def clear_login_attempts(ip: str) -> None:
    """Clear login attempts after successful login."""
    if ip in _login_attempts:
        del _login_attempts[ip]


def get_client_ip(request: Request) -> str:
    """
    Get client IP from request, handling proxies.

    Only trusts proxy headers when TRUST_PROXY_HEADERS is set in config
    (e.g. when running behind Railway, nginx, or a load balancer).
    This prevents IP spoofing via X-Forwarded-For in direct connections.
    """
    if getattr(settings, 'trust_proxy_headers', False):
        # Check X-Forwarded-For header (trusted proxy environment)
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()

        # Check X-Real-IP header
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

    # Fall back to direct client
    return request.client.host if request.client else "unknown"
