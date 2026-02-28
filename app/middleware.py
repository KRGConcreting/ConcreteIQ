"""
Middleware — Request ID, Security Headers, Rate Limiting, and IP Whitelist.

Provides:
- RequestIDMiddleware: Adds X-Request-ID header for tracing
- SecurityHeadersMiddleware: Adds standard security headers
- PortalRateLimitMiddleware: Rate limits public portal endpoints
- IPWhitelistMiddleware: Blocks admin routes from non-whitelisted IPs
"""

import logging
import time
import uuid
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse, PlainTextResponse
from app.config import settings


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Add unique request ID to each request for tracing."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Use existing header or generate new ID
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        # Store in request state for logging
        request.state.request_id = request_id

        response = await call_next(request)

        # Add to response headers
        response.headers["X-Request-ID"] = request_id

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add standard security headers to all responses."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Allow same-origin iframes for internal previews (e.g. email template gallery)
        is_preview = request.url.path.startswith("/settings/api/email/preview/")
        response.headers["X-Frame-Options"] = "SAMEORIGIN" if is_preview else "DENY"

        # XSS protection (legacy but still useful)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Control referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Content Security Policy
        frame_ancestors = "'self'" if is_preview else "'none'"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "https://cdn.jsdelivr.net https://unpkg.com https://js.stripe.com https://maps.googleapis.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
            "img-src 'self' data: https:; "
            "font-src 'self' data: https://fonts.gstatic.com; "
            "connect-src 'self' https://api.stripe.com https://maps.googleapis.com "
            "https://api.open-meteo.com; "
            "frame-src https://js.stripe.com; "
            f"frame-ancestors {frame_ancestors};"
        )

        # HSTS for production (enforce HTTPS)
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        return response


class PortalRateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limit public portal endpoints (/p/) to prevent brute-force token guessing.

    Uses a sliding window counter per IP address.
    GET requests: 30 requests per minute (viewing pages)
    POST requests: 10 requests per minute (signing, payments)
    """

    def __init__(self, app, get_limit: int = 30, post_limit: int = 10, window_seconds: int = 60):
        super().__init__(app)
        self.get_limit = get_limit
        self.post_limit = post_limit
        self.window_seconds = window_seconds
        # {ip: [(timestamp, method), ...]}
        self._requests: dict[str, list[tuple[float, str]]] = defaultdict(list)
        self._last_cleanup = time.time()

    def _get_client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _cleanup_old_entries(self, now: float):
        """Periodically purge stale IP entries to prevent memory leak."""
        if now - self._last_cleanup < 300:  # Every 5 minutes
            return
        self._last_cleanup = now
        cutoff = now - self.window_seconds
        stale_ips = [ip for ip, reqs in self._requests.items() if all(t < cutoff for t, _ in reqs)]
        for ip in stale_ips:
            del self._requests[ip]

    async def dispatch(self, request: Request, call_next) -> Response:
        # Only rate limit portal routes
        if not request.url.path.startswith("/p/"):
            return await call_next(request)

        now = time.time()
        self._cleanup_old_entries(now)

        ip = self._get_client_ip(request)
        cutoff = now - self.window_seconds

        # Filter to recent requests
        recent = [(t, m) for t, m in self._requests[ip] if t > cutoff]
        self._requests[ip] = recent

        # Check limits
        method = request.method.upper()
        if method == "POST":
            post_count = sum(1 for _, m in recent if m == "POST")
            if post_count >= self.post_limit:
                return JSONResponse(
                    {"detail": "Too many requests. Please try again later."},
                    status_code=429,
                    headers={"Retry-After": str(self.window_seconds)},
                )
        else:
            get_count = sum(1 for _, m in recent if m != "POST")
            if get_count >= self.get_limit:
                return JSONResponse(
                    {"detail": "Too many requests. Please try again later."},
                    status_code=429,
                    headers={"Retry-After": str(self.window_seconds)},
                )

        # Record this request
        self._requests[ip].append((now, method))

        return await call_next(request)


class IPWhitelistMiddleware(BaseHTTPMiddleware):
    """
    Block admin routes from non-whitelisted IPs (when whitelist is configured).

    Skips: portal (/p/), booking (/book), health check (/health),
           static files (/static), login (/login), and favicon.
    Only blocks authenticated admin routes when a whitelist is active.
    Logs blocked attempts as security events.
    """

    # Paths that are always allowed regardless of IP whitelist
    SKIP_PREFIXES = (
        "/p/",
        "/book",
        "/health",
        "/static/",
        "/login",
        "/favicon",
        "/webhooks/",
    )

    def _get_client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Skip public/unauthenticated routes
        if any(path.startswith(prefix) for prefix in self.SKIP_PREFIXES):
            return await call_next(request)

        ip = self._get_client_ip(request)

        # Localhost is always allowed (fast path before DB call)
        if ip in ("127.0.0.1", "::1"):
            return await call_next(request)

        # Check whitelist via database
        try:
            from app.database import async_session_maker
            from app.security.service import is_ip_allowed, log_security_event

            async with async_session_maker() as db:
                allowed = await is_ip_allowed(db, ip)

                if not allowed:
                    logger = logging.getLogger("concreteiq.middleware")
                    logger.warning(f"IP whitelist blocked: {ip} -> {path}")

                    # Log the blocked attempt
                    await log_security_event(
                        db,
                        event_type="ip_blocked",
                        description=f"Blocked request to {path} from non-whitelisted IP",
                        ip_address=ip,
                        user_agent=request.headers.get("User-Agent", ""),
                        extra_data={"path": path, "method": request.method},
                    )
                    await db.commit()

                    return PlainTextResponse(
                        "Access denied. Your IP address is not whitelisted.",
                        status_code=403,
                    )
        except Exception:
            # If there's any error checking whitelist (e.g. DB not ready),
            # fail open to avoid locking out the admin
            pass

        return await call_next(request)
