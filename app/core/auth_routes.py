"""Authentication routes — login, logout, 2FA verification."""

import secrets
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.core.auth import (
    verify_password, create_session, clear_session,
    check_login_rate_limit, record_login_attempt, clear_login_attempts,
    get_client_ip
)
from app.core.templates import templates

router = APIRouter()


def _safe_redirect_url(next_url: str) -> str:
    """Validate redirect URL to prevent open redirect attacks."""
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        return "/dashboard"
    # Block protocol-relative URLs and other schemes
    if ":" in next_url.split("/")[1] if "/" in next_url[1:] else False:
        return "/dashboard"
    return next_url


# Temporary storage for pending 2FA logins (maps challenge_token -> next_url)
# In-memory is fine — single-user app, tokens are short-lived
_pending_2fa: dict[str, str] = {}


@router.get("/login")
async def login_page(request: Request, next: str = "/dashboard"):
    """Login page."""
    return templates.TemplateResponse("auth/login.html", {
        "request": request,
        "next": next,
        "error": None,
    })


@router.post("/login")
async def login_submit(
    request: Request,
    password: str = Form(...),
    next: str = Form("/dashboard"),
    db: AsyncSession = Depends(get_db),
):
    """Process login — password step."""
    ip = get_client_ip(request)

    # Import security logging (deferred to avoid circular imports)
    from app.security.service import log_security_event

    user_agent = request.headers.get("User-Agent", "")

    # Rate limiting
    if not check_login_rate_limit(ip):
        await log_security_event(
            db, "login_blocked",
            f"Login blocked (rate limited) from {ip}",
            ip_address=ip, user_agent=user_agent,
        )
        return templates.TemplateResponse("auth/login.html", {
            "request": request,
            "next": next,
            "error": "Too many attempts. Try again in 15 minutes.",
        })

    # Get effective password hash (DB override takes precedence over env var)
    from app.settings import service as settings_service
    db_hash = await settings_service.get_setting(db, "security", "admin_password_hash")
    effective_hash = db_hash if db_hash else settings.admin_password

    # Verify password
    if not verify_password(password, effective_hash):
        record_login_attempt(ip)
        await log_security_event(
            db, "login_failed",
            f"Failed login attempt from {ip}",
            ip_address=ip, user_agent=user_agent,
        )
        return templates.TemplateResponse("auth/login.html", {
            "request": request,
            "next": next,
            "error": "Invalid password.",
        })

    # Password correct — check if 2FA is enabled
    from app.core.totp import is_totp_enabled

    if await is_totp_enabled(db):
        # Generate a challenge token to link password step to 2FA step
        challenge = secrets.token_hex(32)
        _pending_2fa[challenge] = next

        # Limit pending challenges to prevent memory leak
        if len(_pending_2fa) > 50:
            oldest_keys = list(_pending_2fa.keys())[:25]
            for k in oldest_keys:
                del _pending_2fa[k]

        return templates.TemplateResponse("auth/totp_verify.html", {
            "request": request,
            "challenge": challenge,
            "error": None,
        })

    # No 2FA — create session directly
    clear_login_attempts(ip)
    await log_security_event(
        db, "login_success",
        f"Successful login from {ip}",
        ip_address=ip, user_agent=user_agent,
    )

    # Get session version for session cookie
    session_ver = await settings_service.get_setting(db, "security", "session_version")
    session_version = int(session_ver) if session_ver else 1

    response = RedirectResponse(url=_safe_redirect_url(next), status_code=302)
    create_session(response, session_version=session_version)
    return response


@router.post("/login/2fa")
async def login_2fa_submit(
    request: Request,
    challenge: str = Form(...),
    totp_code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Process 2FA verification step."""
    from app.security.service import log_security_event

    ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent", "")

    # Rate limiting (shared with password attempts)
    if not check_login_rate_limit(ip):
        await log_security_event(
            db, "login_blocked",
            f"2FA verification blocked (rate limited) from {ip}",
            ip_address=ip, user_agent=user_agent,
        )
        return templates.TemplateResponse("auth/login.html", {
            "request": request,
            "next": "/dashboard",
            "error": "Too many attempts. Try again in 15 minutes.",
        })

    # Validate challenge token
    next_url = _pending_2fa.pop(challenge, None)
    if not next_url:
        return templates.TemplateResponse("auth/login.html", {
            "request": request,
            "next": "/dashboard",
            "error": "Session expired. Please log in again.",
        })

    # Verify TOTP code (try normal TOTP first, then recovery codes)
    from app.core.totp import verify_login_totp, verify_recovery_code

    code = totp_code.strip()
    totp_valid = await verify_login_totp(db, code)
    recovery_used = False

    if not totp_valid:
        # Try as recovery code (format: XXXX-XXXX or XXXXXXXX)
        recovery_valid = await verify_recovery_code(db, code)
        if recovery_valid:
            recovery_used = True
        else:
            record_login_attempt(ip)
            await log_security_event(
                db, "login_failed",
                f"Failed 2FA verification from {ip}",
                ip_address=ip, user_agent=user_agent,
            )
            # Generate new challenge for retry
            new_challenge = secrets.token_hex(32)
            _pending_2fa[new_challenge] = next_url
            return templates.TemplateResponse("auth/totp_verify.html", {
                "request": request,
                "challenge": new_challenge,
                "error": "Invalid verification code. Please try again.",
            })

    # 2FA verified — create session
    clear_login_attempts(ip)
    method = "recovery code" if recovery_used else "2FA"
    await log_security_event(
        db, "login_success",
        f"Successful login (with {method}) from {ip}",
        ip_address=ip, user_agent=user_agent,
    )

    # Get session version for session cookie
    from app.settings import service as settings_service
    session_ver = await settings_service.get_setting(db, "security", "session_version")
    session_version = int(session_ver) if session_ver else 1

    response = RedirectResponse(url=_safe_redirect_url(next_url), status_code=302)
    create_session(response, session_version=session_version)
    return response


@router.get("/logout")
async def logout(request: Request):
    """Logout."""
    response = RedirectResponse(url="/login", status_code=302)
    clear_session(response)
    return response
