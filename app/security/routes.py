"""Security routes — Audit log viewer and IP whitelist management."""

import ipaddress

from fastapi import APIRouter, Depends, Request, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.auth import require_login, verify_csrf, get_client_ip
from app.core.templates import templates
from app.security.service import (
    get_security_events,
    get_security_events_count,
    get_security_stats,
    get_ip_whitelist,
    set_ip_whitelist,
    log_security_event,
    SECURITY_EVENT_TYPES,
)

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


# =============================================================================
# AUDIT LOG PAGE
# =============================================================================

@router.get("/audit-log", name="security:audit_log")
async def audit_log_page(
    request: Request,
    event_type: str = Query(None, description="Filter by event type"),
    db: AsyncSession = Depends(get_db),
):
    """Security audit log page — shows recent security events with filters."""
    events = await get_security_events(db, limit=100, event_type=event_type or None)
    stats = await get_security_stats(db)

    return templates.TemplateResponse("settings/audit_log.html", {
        "request": request,
        "events": events,
        "stats": stats,
        "event_types": SECURITY_EVENT_TYPES,
        "current_filter": event_type or "",
        "active_section": "audit_log",
    })


# =============================================================================
# IP WHITELIST PAGE
# =============================================================================

@router.get("/ip-whitelist", name="security:ip_whitelist")
async def ip_whitelist_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """IP whitelist settings page — manage allowed admin IPs."""
    whitelist = await get_ip_whitelist(db)
    current_ip = get_client_ip(request)

    return templates.TemplateResponse("settings/ip_whitelist.html", {
        "request": request,
        "whitelist": whitelist,
        "current_ip": current_ip,
        "active_section": "ip_whitelist",
    })


# =============================================================================
# API ENDPOINTS
# =============================================================================

@router.post("/api/ip-whitelist", name="security:api:ip_whitelist")
async def save_ip_whitelist(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Save IP whitelist (JSON API).

    Accepts: {"ips": ["1.2.3.4", "5.6.7.8"]}
    Validates each IP, logs the change as a security event.
    """
    data = await request.json()
    raw_ips = data.get("ips", [])

    if not isinstance(raw_ips, list):
        raise HTTPException(status_code=400, detail="'ips' must be a list")

    # Validate each IP address
    validated_ips = []
    invalid_ips = []
    for ip_str in raw_ips:
        ip_str = str(ip_str).strip()
        if not ip_str:
            continue
        try:
            # Validate as an IP address (v4 or v6)
            ipaddress.ip_address(ip_str)
            validated_ips.append(ip_str)
        except ValueError:
            # Try as a network (e.g. 192.168.1.0/24)
            try:
                ipaddress.ip_network(ip_str, strict=False)
                validated_ips.append(ip_str)
            except ValueError:
                invalid_ips.append(ip_str)

    if invalid_ips:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid IP address(es): {', '.join(invalid_ips)}"
        )

    # Get old whitelist for comparison
    old_whitelist = await get_ip_whitelist(db)

    # Save new whitelist
    await set_ip_whitelist(db, validated_ips)

    # Log the change as a security event
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent", "")

    await log_security_event(
        db,
        event_type="settings_changed",
        description=f"IP whitelist updated: {len(validated_ips)} IP(s) configured",
        ip_address=client_ip,
        user_agent=user_agent,
        extra_data={
            "old_whitelist": old_whitelist,
            "new_whitelist": validated_ips,
        },
    )

    return {
        "status": "ok",
        "message": f"IP whitelist saved with {len(validated_ips)} address(es)",
        "ips": validated_ips,
    }


@router.get("/api/audit-log", name="security:api:audit_log")
async def api_audit_log(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    event_type: str = Query(None, description="Filter by event type"),
    db: AsyncSession = Depends(get_db),
):
    """
    JSON API for audit log with pagination.

    Returns a list of security events with total count.
    """
    events = await get_security_events(
        db,
        limit=limit,
        event_type=event_type or None,
        offset=offset,
    )
    total = await get_security_events_count(db, event_type=event_type or None)

    return {
        "events": [
            {
                "id": e.id,
                "action": e.action,
                "description": e.description,
                "ip_address": e.ip_address,
                "user_agent": e.user_agent,
                "extra_data": e.extra_data,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
