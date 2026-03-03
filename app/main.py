"""
ConcreteIQ — Main Application

Single-user business management system for KRG Concreting.
Built with FastAPI, SQLite/PostgreSQL, Alpine.js, Tailwind CSS.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.database import init_db, get_db
from app.core.templates import templates, set_flash_cookie
from app.core.auth import is_authenticated, require_login, get_csrf_token, set_csrf_cookie
from app.middleware import RequestIDMiddleware, SecurityHeadersMiddleware, PortalRateLimitMiddleware, IPWhitelistMiddleware

# Import routers
from app.core.auth_routes import router as auth_router
from app.customers.routes import router as customers_router
from app.quotes.routes import router as quotes_router
from app.invoices.routes import router as invoices_router
from app.payments.routes import router as payments_router
from app.portal.routes import router as portal_router
from app.notifications.routes import router as notifications_router
from app.settings.routes import router as settings_router
from app.reports.routes import router as reports_router
from app.pour_planner.routes import router as pour_planner_router
from app.workers.routes import router as workers_router
from app.photos.routes import router as photos_router
from app.integrations.webhooks import router as webhooks_router
from app.integrations.routes import router as integrations_router
from app.schedule.routes import router as schedule_router
from app.costing.routes import router as costing_router
from app.sms_inbox.routes import router as sms_inbox_router
from app.security.routes import router as security_router
from app.documents.routes import router as documents_router
# Suppliers module removed per user request
# from app.suppliers.routes import router as suppliers_router

import logging

if settings.environment == "production":
    try:
        from pythonjsonlogger import jsonlogger
        handler = logging.StreamHandler()
        handler.setFormatter(jsonlogger.JsonFormatter(
            fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        ))
        logging.root.handlers = [handler]
        logging.root.setLevel(logging.INFO)
    except ImportError:
        pass  # python-json-logger not installed, use default logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    # Startup
    await init_db()
    yield
    # Shutdown — dispose database connections cleanly
    from app.database import engine, sync_engine
    await engine.dispose()
    sync_engine.dispose()


# Disable OpenAPI docs in production (no /docs or /redoc exposure)
_is_prod = settings.environment == "production"

app = FastAPI(
    title="ConcreteIQ",
    description="Business management for KRG Concreting",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if _is_prod else "/docs",
    redoc_url=None if _is_prod else "/redoc",
    openapi_url=None if _is_prod else "/openapi.json",
)


# Global exception handler to log tracebacks in development
import traceback

logger = logging.getLogger("concreteiq")


def _home_url_for_request(request: Request) -> str:
    """Return the appropriate home URL based on request path context."""
    path = request.url.path
    if path.startswith("/p/"):
        # Portal user — send them back to the portal page they came from, or just "/"
        # Extract token from path like /p/{token}/pdf or /p/invoice/{token}/pdf
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "p":
            if parts[1] == "invoice" and len(parts) >= 3:
                return f"/p/invoice/{parts[2]}"
            elif parts[1] == "dashboard" and len(parts) >= 3:
                return f"/p/dashboard/{parts[2]}"
            else:
                return f"/p/{parts[1]}"
    return "/dashboard"


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: StarletteHTTPException):
    # For portal routes, show a friendlier message with the specific detail
    detail = getattr(exc, "detail", None)
    is_portal = request.url.path.startswith("/p/")
    if is_portal and detail and detail != "Not Found":
        message = detail
        title = "Not Found"
    else:
        message = "The page you're looking for doesn't exist or has been moved."
        title = "Page Not Found"
    return templates.TemplateResponse("error.html", {
        "request": request,
        "status_code": 404,
        "title": title,
        "message": message,
        "home_url": _home_url_for_request(request),
    }, status_code=404)


@app.exception_handler(500)
async def server_error_handler(request: Request, exc: StarletteHTTPException):
    return templates.TemplateResponse("error.html", {
        "request": request,
        "status_code": 500,
        "title": "Server Error",
        "message": "Something went wrong on our end. Please try again.",
        "home_url": _home_url_for_request(request),
    }, status_code=500)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    logger.error(f"Unhandled exception on {request.method} {request.url.path}:\n{''.join(tb)}")
    if settings.environment == "development":
        from starlette.responses import PlainTextResponse
        return PlainTextResponse(f"Internal Server Error:\n{''.join(tb)}", status_code=500)
    return templates.TemplateResponse("error.html", {
        "request": request,
        "status_code": 500,
        "title": "Server Error",
        "message": "Something went wrong on our end. Please try again.",
        "home_url": _home_url_for_request(request),
    }, status_code=500)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# CORS (if needed)
if settings.environment == "development":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Session middleware (required for Xero OAuth state)
from starlette.middleware.sessions import SessionMiddleware
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    https_only=settings.environment == "production",
    same_site="lax",
)

# Security, tracing, and rate limiting middleware
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(PortalRateLimitMiddleware)
app.add_middleware(IPWhitelistMiddleware)


# Middleware to add CSRF token and flash messages to all responses
@app.middleware("http")
async def csrf_and_flash_middleware(request: Request, call_next):
    response = await call_next(request)

    # Set CSRF token if not present
    if "csrf_token" not in request.cookies:
        token = get_csrf_token(request)
        set_csrf_cookie(response, token)

    # Write flash messages cookie
    set_flash_cookie(response, request)

    return response


# Include routers
app.include_router(auth_router)
app.include_router(customers_router, prefix="/customers", tags=["Customers"])
app.include_router(quotes_router, prefix="/quotes", tags=["Quotes"])
app.include_router(invoices_router, prefix="/invoices", tags=["Invoices"])
app.include_router(payments_router, prefix="/payments", tags=["Payments"])
app.include_router(portal_router, prefix="/p", tags=["Portal"])
app.include_router(notifications_router, prefix="/notifications", tags=["Notifications"])
app.include_router(settings_router, prefix="/settings", tags=["Settings"])
app.include_router(reports_router, prefix="/reports", tags=["Reports"])
app.include_router(pour_planner_router, prefix="/pour-planner", tags=["Pour Planner"])
app.include_router(workers_router, prefix="/workers", tags=["Workers"])
app.include_router(schedule_router, prefix="/schedule", tags=["Schedule"])
app.include_router(photos_router, prefix="/photos", tags=["Photos"])
app.include_router(webhooks_router, prefix="/webhooks", tags=["Webhooks"])
app.include_router(integrations_router, prefix="/integrations", tags=["Integrations"])
app.include_router(costing_router, prefix="/costing", tags=["Job Costing"])
app.include_router(sms_inbox_router, prefix="/sms-inbox", tags=["SMS Inbox"])
app.include_router(security_router, prefix="/security", tags=["Security"])
app.include_router(documents_router, prefix="/documents", tags=["Documents"])
# app.include_router(suppliers_router, prefix="/suppliers", tags=["Suppliers"])  # Removed


# Root redirect
@app.get("/")
async def root(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


# Dashboard
@app.get("/dashboard", dependencies=[Depends(require_login)])
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):

    # Import here to avoid circular imports
    from app.reports.service import (
        get_dashboard_stats,
        get_upcoming_jobs,
        get_recent_activity,
    )
    from app.reports.take_home import get_goal_progress, get_annual_projection
    from app.invoices.service import get_payment_summary_stats
    from app.models import Notification, Quote, Customer
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    # Fetch dashboard data
    stats = await get_dashboard_stats(db, period="month")
    upcoming_jobs = await get_upcoming_jobs(db, limit=5)
    recent_activity = await get_recent_activity(db, limit=10)

    # Fetch recent quotes
    recent_quotes_result = await db.execute(
        select(Quote)
        .options(selectinload(Quote.customer))
        .order_by(Quote.created_at.desc())
        .limit(5)
    )
    recent_quotes_raw = recent_quotes_result.scalars().all()
    recent_quotes = [
        {
            "id": q.id,
            "quote_number": q.quote_number,
            "customer_name": q.customer.name if q.customer else "Unknown",
            "total_cents": q.total_cents,
            "status": q.status,
        }
        for q in recent_quotes_raw
    ]

    # Fetch goal progress and annual projection
    goal = await get_goal_progress(db)
    projection = await get_annual_projection(db)

    # Fetch payment summary stats for progress payments
    payment_stats = await get_payment_summary_stats(db)

    # Fetch unread notifications
    notifications_result = await db.execute(
        select(Notification)
        .where(Notification.is_read == False)
        .order_by(Notification.created_at.desc())
        .limit(5)
    )
    notifications = notifications_result.scalars().all()

    # Fetch pending payment reminder alerts (unread, payment_reminder_* type)
    reminder_alerts_result = await db.execute(
        select(Notification)
        .where(
            Notification.is_read == False,
            Notification.type.like("payment_reminder_%"),
        )
        .order_by(Notification.created_at.desc())
        .limit(10)
    )
    reminder_alerts = reminder_alerts_result.scalars().all()

    # Fetch weather for dashboard widget
    weather = None
    try:
        from app.pour_planner.service import get_weather_forecast
        from app.core.dates import sydney_now
        from datetime import timedelta
        now = sydney_now()
        today_str = now.strftime("%Y-%m-%d")
        tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        today_data = await get_weather_forecast(date=today_str)
        tomorrow_data = await get_weather_forecast(date=tomorrow_str)

        if today_data:
            mid_hour = None
            for wd in today_data:
                if wd.datetime.hour == 12:
                    mid_hour = wd
                    break
            if not mid_hour:
                mid_hour = today_data[len(today_data) // 2] if today_data else None

            if mid_hour:
                risk = "good"
                if mid_hour.rain_probability > 60 or mid_hour.wind_speed > 40:
                    risk = "poor"
                elif mid_hour.rain_probability > 30 or mid_hour.wind_speed > 25 or mid_hour.temperature > 38 or mid_hour.temperature < 5:
                    risk = "marginal"

                weather = {
                    "temp_c": round(mid_hour.temperature),
                    "day": "Today",
                    "rain_chance": round(mid_hour.rain_probability),
                    "wind_kph": round(mid_hour.wind_speed),
                    "risk": risk,
                    "location": "Local",
                }

                if tomorrow_data:
                    tmid = None
                    for wd in tomorrow_data:
                        if wd.datetime.hour == 12:
                            tmid = wd
                            break
                    if not tmid:
                        tmid = tomorrow_data[len(tomorrow_data) // 2] if tomorrow_data else None
                    if tmid:
                        weather["tomorrow"] = {
                            "temp_c": round(tmid.temperature),
                            "rain_chance": round(tmid.rain_probability),
                            "is_no_go": tmid.rain_probability > 70 or tmid.wind_speed > 45,
                        }
    except Exception:
        pass  # Weather is non-critical, fail silently

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "notifications": notifications,
        "upcoming_jobs": upcoming_jobs,
        "recent_activity": recent_activity,
        "recent_quotes": recent_quotes,
        "goal": goal,
        "projection": projection,
        "payment_stats": payment_stats,
        "reminder_alerts": reminder_alerts,
        "weather": weather,
    })


# Health check
@app.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ok", "version": "1.0.0"}
    except Exception:
        return JSONResponse({"status": "unhealthy"}, status_code=503)


# API config (for frontend)
@app.get("/api/config")
async def api_config(request: Request):
    if not is_authenticated(request):
        return {"authenticated": False}
    
    return {
        "authenticated": True,
        "business": {
            "name": settings.business_name,
            "trading_as": settings.trading_as,
            "abn": settings.abn,
            "phone": settings.business_phone,
            "email": settings.business_email,
        },
        "integrations": {
            "xero": bool(settings.xero_client_id),
            "gcal": bool(settings.google_client_id),
            "stripe": bool(settings.stripe_secret_key),
            "resend": bool(settings.resend_api_key),
        }
    }
