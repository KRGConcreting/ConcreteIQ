"""Security service — Audit log and IP whitelist management."""

import json
from datetime import timedelta
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivityLog
from app.core.dates import sydney_now
from app.settings.service import get_setting, set_setting


# Valid security event types
SECURITY_EVENT_TYPES = (
    "login_success",
    "login_failed",
    "login_blocked",
    "ip_blocked",
    "settings_changed",
    "backup_downloaded",
    "backup_restored",
)


async def log_security_event(
    db: AsyncSession,
    event_type: str,
    description: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    extra_data: Optional[dict] = None,
) -> ActivityLog:
    """
    Create an ActivityLog entry with entity_type='security'.

    event_type should be one of SECURITY_EVENT_TYPES.
    """
    entry = ActivityLog(
        action=event_type,
        description=description,
        entity_type="security",
        entity_id=None,
        ip_address=ip_address,
        user_agent=user_agent,
        extra_data=extra_data,
        created_at=sydney_now(),
    )
    db.add(entry)
    await db.flush()
    return entry


async def get_security_events(
    db: AsyncSession,
    limit: int = 50,
    event_type: Optional[str] = None,
    offset: int = 0,
) -> list[ActivityLog]:
    """
    Query security events (ActivityLog where entity_type='security').

    Results are ordered by created_at descending (newest first).
    Optionally filter by event_type (the 'action' column).
    """
    query = (
        select(ActivityLog)
        .where(ActivityLog.entity_type == "security")
    )

    if event_type:
        query = query.where(ActivityLog.action == event_type)

    query = query.order_by(ActivityLog.created_at.desc()).offset(offset).limit(limit)

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_security_events_count(
    db: AsyncSession,
    event_type: Optional[str] = None,
) -> int:
    """Count total security events, optionally filtered by type."""
    query = (
        select(func.count(ActivityLog.id))
        .where(ActivityLog.entity_type == "security")
    )

    if event_type:
        query = query.where(ActivityLog.action == event_type)

    result = await db.execute(query)
    return result.scalar() or 0


async def get_ip_whitelist(db: AsyncSession) -> list[str]:
    """
    Get the IP whitelist from settings.

    Stored under category='security', key='ip_whitelist' as a JSON list.
    Returns an empty list if not configured.
    """
    value = await get_setting(db, "security", "ip_whitelist", default=[])
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(value, list):
        return value
    return []


async def set_ip_whitelist(db: AsyncSession, ips: list[str]) -> None:
    """
    Save the IP whitelist to settings.

    Stores under category='security', key='ip_whitelist'.
    """
    # Clean and deduplicate
    clean_ips = list(dict.fromkeys(ip.strip() for ip in ips if ip.strip()))
    await set_setting(db, "security", "ip_whitelist", clean_ips)
    await db.flush()


async def is_ip_allowed(db: AsyncSession, ip: str) -> bool:
    """
    Check if an IP address is allowed by the whitelist.

    Rules:
    - Empty whitelist means ALL IPs are allowed.
    - 127.0.0.1 and ::1 (localhost) are ALWAYS allowed.
    - Otherwise the IP must be in the whitelist.
    """
    # Localhost is always allowed
    if ip in ("127.0.0.1", "::1"):
        return True

    whitelist = await get_ip_whitelist(db)

    # Empty whitelist = all allowed
    if not whitelist:
        return True

    return ip in whitelist


async def get_security_stats(db: AsyncSession) -> dict:
    """
    Return summary security statistics.

    Returns:
        {
            "failed_logins_24h": int,
            "blocked_ips_24h": int,
            "total_events_7d": int,
        }
    """
    now = sydney_now()
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)

    # Failed logins in last 24 hours
    failed_result = await db.execute(
        select(func.count(ActivityLog.id))
        .where(
            ActivityLog.entity_type == "security",
            ActivityLog.action == "login_failed",
            ActivityLog.created_at >= cutoff_24h,
        )
    )
    failed_logins_24h = failed_result.scalar() or 0

    # Blocked IPs in last 24 hours (login_blocked + ip_blocked)
    blocked_result = await db.execute(
        select(func.count(ActivityLog.id))
        .where(
            ActivityLog.entity_type == "security",
            ActivityLog.action.in_(["login_blocked", "ip_blocked"]),
            ActivityLog.created_at >= cutoff_24h,
        )
    )
    blocked_ips_24h = blocked_result.scalar() or 0

    # Total events in last 7 days
    total_result = await db.execute(
        select(func.count(ActivityLog.id))
        .where(
            ActivityLog.entity_type == "security",
            ActivityLog.created_at >= cutoff_7d,
        )
    )
    total_events_7d = total_result.scalar() or 0

    return {
        "failed_logins_24h": failed_logins_24h,
        "blocked_ips_24h": blocked_ips_24h,
        "total_events_7d": total_events_7d,
    }
