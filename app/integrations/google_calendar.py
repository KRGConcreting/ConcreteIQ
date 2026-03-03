"""
Google Calendar Integration — Create/update/delete job events, NSW holiday sync,
and ATO compliance deadline sync.

Uses service account authentication for headless operation.
Fails gracefully - calendar errors should not block bookings.
"""

import base64
import json
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from app.config import settings
from app.core.dates import sydney_now
from app.models import Quote

logger = logging.getLogger(__name__)


# =============================================================================
# DB-FIRST CREDENTIAL HELPERS
# =============================================================================

# Module-level cache for DB credentials (refreshed each call to async helper)
_cached_credentials_json: Optional[str] = None
_cached_calendar_id: Optional[str] = None


async def _load_google_credentials_from_db():
    """Load Google Calendar credentials from DB, cache for sync functions."""
    global _cached_credentials_json, _cached_calendar_id
    try:
        from app.database import get_async_session
        from app.settings import service as settings_service
        async with get_async_session() as db:
            db_settings = await settings_service.get_settings_by_category(db, "integrations")
            _cached_credentials_json = db_settings.get("google_credentials_json") or settings.google_credentials_json or None
            _cached_calendar_id = db_settings.get("google_calendar_id") or settings.google_calendar_id or None
    except Exception:
        _cached_credentials_json = settings.google_credentials_json or None
        _cached_calendar_id = settings.google_calendar_id or None


def _get_effective_credentials_json() -> Optional[str]:
    """Get Google credentials JSON — DB cache first, then env var."""
    return _cached_credentials_json or settings.google_credentials_json or None


def _get_effective_calendar_id() -> Optional[str]:
    """Get Google Calendar ID — DB cache first, then env var."""
    return _cached_calendar_id or settings.google_calendar_id or None


def _get_credentials():
    """
    Get Google service account credentials from config.

    Returns None if not configured.
    """
    creds_json = _get_effective_credentials_json()
    calendar_id = _get_effective_calendar_id()

    if not creds_json or not calendar_id:
        logger.debug("Google Calendar not configured")
        return None

    try:
        # Decode base64 credentials JSON
        credentials_json = base64.b64decode(creds_json)
        credentials_info = json.loads(credentials_json)

        from google.oauth2.service_account import Credentials

        credentials = Credentials.from_service_account_info(
            credentials_info,
            scopes=['https://www.googleapis.com/auth/calendar']
        )
        return credentials
    except Exception as e:
        logger.error(f"Failed to load Google credentials: {e}")
        return None


def _get_calendar_service():
    """
    Get Google Calendar API service.

    Returns None if not configured or on error.
    """
    credentials = _get_credentials()
    if not credentials:
        return None

    try:
        from googleapiclient.discovery import build

        service = build('calendar', 'v3', credentials=credentials)
        return service
    except Exception as e:
        logger.error(f"Failed to create Google Calendar service: {e}")
        return None


def _build_event_body(
    quote: Quote,
    worker_names: list[str] | None = None,
    customer_name: str | None = None,
    customer_phone: str | None = None,
) -> dict:
    """
    Build the event body for Google Calendar.

    Args:
        quote: The quote with confirmed booking
        worker_names: Optional list of worker names to include
        customer_name: Pre-fetched customer name (avoids lazy loading in async)
        customer_phone: Pre-fetched customer phone (avoids lazy loading in async)

    Returns:
        Event body dict for Google Calendar API
    """
    # Build title - use passed-in values to avoid lazy loading in async context
    _customer_name = customer_name or "Unknown Customer"
    job_location = quote.job_address.split(",")[0] if quote.job_address else "Job Site"
    summary = f"{_customer_name} - {job_location}"

    # Build description
    description_parts = [
        f"Quote: {quote.quote_number}",
        f"Customer: {_customer_name}",
    ]

    if customer_phone:
        description_parts.append(f"Phone: {customer_phone}")

    if quote.job_address:
        description_parts.append(f"Address: {quote.job_address}")

    if quote.total_cents:
        description_parts.append(f"Total: ${quote.total_cents / 100:,.2f}")

    if worker_names:
        description_parts.append("")
        description_parts.append("Assigned Workers:")
        for name in worker_names:
            description_parts.append(f"  - {name}")

    if quote.notes:
        description_parts.append("")
        description_parts.append(f"Notes: {quote.notes}")

    description = "\n".join(description_parts)

    # Build location
    location = quote.job_address or ""

    # Build date (all-day event)
    start_date = quote.confirmed_start_date
    end_date = start_date + timedelta(days=1)

    return {
        "summary": summary,
        "description": description,
        "location": location,
        "start": {
            "date": start_date.isoformat(),
            "timeZone": "Australia/Sydney",
        },
        "end": {
            "date": end_date.isoformat(),
            "timeZone": "Australia/Sydney",
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 1440},  # 1 day before
                {"method": "popup", "minutes": 60},    # 1 hour before
            ],
        },
    }


async def create_job_event(
    quote: Quote,
    worker_names: list[str] | None = None,
    customer_name: str | None = None,
    customer_phone: str | None = None,
) -> Optional[str]:
    """
    Create calendar event for confirmed job.

    Args:
        quote: The quote with confirmed booking
        worker_names: Optional list of assigned worker names
        customer_name: Pre-fetched customer name (avoids lazy loading in async)
        customer_phone: Pre-fetched customer phone (avoids lazy loading in async)

    Returns:
        Google event ID if successful, None on failure
    """
    await _load_google_credentials_from_db()
    service = _get_calendar_service()
    if not service:
        logger.info("Google Calendar not configured, skipping event creation")
        return None

    if not quote.confirmed_start_date:
        logger.warning(f"Quote {quote.quote_number} has no confirmed start date")
        return None

    try:
        event_body = _build_event_body(quote, worker_names, customer_name, customer_phone)

        event = service.events().insert(
            calendarId=_get_effective_calendar_id(),
            body=event_body,
        ).execute()

        event_id = event.get("id")
        logger.info(f"Created calendar event {event_id} for quote {quote.quote_number}")
        return event_id

    except Exception as e:
        logger.error(f"Failed to create calendar event for quote {quote.quote_number}: {e}")
        return None


async def update_job_event(
    quote: Quote,
    worker_names: list[str] | None = None,
) -> bool:
    """
    Update existing calendar event if date or details change.

    Args:
        quote: The quote with updated booking info
        worker_names: Optional list of assigned worker names

    Returns:
        True if successful, False on failure
    """
    await _load_google_credentials_from_db()
    if not quote.gcal_event_id:
        logger.warning(f"Quote {quote.quote_number} has no calendar event to update")
        return False

    service = _get_calendar_service()
    if not service:
        logger.info("Google Calendar not configured, skipping event update")
        return False

    try:
        event_body = _build_event_body(quote, worker_names)

        service.events().update(
            calendarId=_get_effective_calendar_id(),
            eventId=quote.gcal_event_id,
            body=event_body,
        ).execute()

        logger.info(f"Updated calendar event {quote.gcal_event_id} for quote {quote.quote_number}")
        return True

    except Exception as e:
        logger.error(f"Failed to update calendar event for quote {quote.quote_number}: {e}")
        return False


async def delete_job_event(quote: Quote) -> bool:
    """
    Delete calendar event if job cancelled.

    Args:
        quote: The quote whose event should be deleted

    Returns:
        True if successful, False on failure
    """
    await _load_google_credentials_from_db()
    if not quote.gcal_event_id:
        logger.warning(f"Quote {quote.quote_number} has no calendar event to delete")
        return False

    service = _get_calendar_service()
    if not service:
        logger.info("Google Calendar not configured, skipping event deletion")
        return False

    try:
        service.events().delete(
            calendarId=_get_effective_calendar_id(),
            eventId=quote.gcal_event_id,
        ).execute()

        logger.info(f"Deleted calendar event {quote.gcal_event_id} for quote {quote.quote_number}")
        return True

    except Exception as e:
        logger.error(f"Failed to delete calendar event for quote {quote.quote_number}: {e}")
        return False


# =============================================================================
# NSW PUBLIC HOLIDAYS — hardcoded for reliability (no external API dependency)
# =============================================================================

NSW_PUBLIC_HOLIDAYS = {
    2025: [
        ("2025-01-01", "New Year's Day"),
        ("2025-01-27", "Australia Day"),
        ("2025-04-18", "Good Friday"),
        ("2025-04-19", "Saturday before Easter Sunday"),
        ("2025-04-21", "Easter Monday"),
        ("2025-04-25", "ANZAC Day"),
        ("2025-06-09", "King's Birthday"),
        ("2025-08-04", "Bank Holiday"),
        ("2025-10-06", "Labour Day"),
        ("2025-12-25", "Christmas Day"),
        ("2025-12-26", "Boxing Day"),
    ],
    2026: [
        ("2026-01-01", "New Year's Day"),
        ("2026-01-26", "Australia Day"),
        ("2026-04-03", "Good Friday"),
        ("2026-04-04", "Saturday before Easter Sunday"),
        ("2026-04-06", "Easter Monday"),
        ("2026-04-25", "ANZAC Day"),
        ("2026-06-08", "King's Birthday"),
        ("2026-08-03", "Bank Holiday"),
        ("2026-10-05", "Labour Day"),
        ("2026-12-25", "Christmas Day"),
        ("2026-12-26", "Boxing Day"),
    ],
    2027: [
        ("2027-01-01", "New Year's Day"),
        ("2027-01-26", "Australia Day"),
        ("2027-03-26", "Good Friday"),
        ("2027-03-27", "Saturday before Easter Sunday"),
        ("2027-03-29", "Easter Monday"),
        ("2027-04-26", "ANZAC Day (observed Monday)"),
        ("2027-06-14", "King's Birthday"),
        ("2027-08-02", "Bank Holiday"),
        ("2027-10-04", "Labour Day"),
        ("2027-12-25", "Christmas Day"),
        ("2027-12-27", "Boxing Day (observed Monday)"),
    ],
}

# Prefix used for holiday events so we can identify them
HOLIDAY_EVENT_PREFIX = "KRG - HOLIDAY: "

# Google Calendar color ID for red (Tomato)
HOLIDAY_COLOR_ID = "11"


def _get_holiday_summary(name: str) -> str:
    """Build the calendar event summary for a holiday."""
    return f"{HOLIDAY_EVENT_PREFIX}{name}"


async def _list_holiday_events(service, year: int) -> dict[str, str]:
    """
    List all KRG holiday events already in the calendar for a given year.

    Returns:
        Dict mapping date string (YYYY-MM-DD) to event ID
    """
    time_min = f"{year}-01-01T00:00:00+11:00"
    time_max = f"{year}-12-31T23:59:59+11:00"

    existing = {}
    try:
        page_token = None
        while True:
            events_result = service.events().list(
                calendarId=_get_effective_calendar_id(),
                timeMin=time_min,
                timeMax=time_max,
                q=HOLIDAY_EVENT_PREFIX,
                singleEvents=True,
                maxResults=50,
                pageToken=page_token,
            ).execute()

            for event in events_result.get("items", []):
                summary = event.get("summary", "")
                if summary.startswith(HOLIDAY_EVENT_PREFIX):
                    event_date = event.get("start", {}).get("date", "")
                    if event_date:
                        existing[event_date] = event["id"]

            page_token = events_result.get("nextPageToken")
            if not page_token:
                break

    except Exception as e:
        logger.error(f"Failed to list holiday events for {year}: {e}")

    return existing


async def sync_nsw_holidays(year: int | None = None) -> dict:
    """
    Sync NSW public holidays to Google Calendar as all-day events.

    - Creates events only if they don't already exist (checks by summary + date)
    - Marks them as "KRG - HOLIDAY: <name>" with red color
    - Returns: {"synced": 3, "skipped": 8, "errors": 0, "year": 2026}
    """
    await _load_google_credentials_from_db()
    if year is None:
        year = sydney_now().year

    holidays = NSW_PUBLIC_HOLIDAYS.get(year)
    if not holidays:
        return {
            "synced": 0,
            "skipped": 0,
            "errors": 0,
            "year": year,
            "message": f"No holiday data available for {year}",
        }

    service = _get_calendar_service()
    if not service:
        return {
            "synced": 0,
            "skipped": 0,
            "errors": 0,
            "year": year,
            "message": "Google Calendar not configured",
        }

    # Find which holidays are already synced
    existing = await _list_holiday_events(service, year)

    synced = 0
    skipped = 0
    errors = 0

    for date_str, name in holidays:
        # Already in the calendar for this date
        if date_str in existing:
            skipped += 1
            logger.debug(f"Holiday already synced: {name} ({date_str})")
            continue

        # Create the all-day event
        try:
            event_date = date.fromisoformat(date_str)
            end_date = event_date + timedelta(days=1)

            event_body = {
                "summary": _get_holiday_summary(name),
                "description": (
                    f"NSW Public Holiday\n"
                    f"{name}\n\n"
                    f"No work scheduled — public holiday."
                ),
                "start": {
                    "date": date_str,
                    "timeZone": "Australia/Sydney",
                },
                "end": {
                    "date": end_date.isoformat(),
                    "timeZone": "Australia/Sydney",
                },
                "colorId": HOLIDAY_COLOR_ID,
                "transparency": "transparent",  # Show as free
                "reminders": {
                    "useDefault": False,
                    "overrides": [],
                },
            }

            service.events().insert(
                calendarId=_get_effective_calendar_id(),
                body=event_body,
            ).execute()

            synced += 1
            logger.info(f"Synced holiday to calendar: {name} ({date_str})")

        except Exception as e:
            errors += 1
            logger.error(f"Failed to sync holiday {name} ({date_str}): {e}")

    total = synced + skipped + errors
    logger.info(
        f"Holiday sync complete for {year}: "
        f"{synced} synced, {skipped} skipped, {errors} errors out of {total}"
    )

    return {
        "synced": synced,
        "skipped": skipped,
        "errors": errors,
        "year": year,
    }


async def get_synced_holidays(year: int | None = None) -> list[dict]:
    """
    Check which holidays are already in the calendar for the given year.

    Returns:
        List of {"date": "2026-01-01", "name": "New Year's Day", "synced": True/False}
    """
    await _load_google_credentials_from_db()
    if year is None:
        year = sydney_now().year

    holidays = NSW_PUBLIC_HOLIDAYS.get(year)
    if not holidays:
        return []

    # Try to get existing events from the calendar
    existing: dict[str, str] = {}
    service = _get_calendar_service()
    if service:
        existing = await _list_holiday_events(service, year)

    result = []
    for date_str, name in holidays:
        result.append({
            "date": date_str,
            "name": name,
            "synced": date_str in existing,
        })

    return result


# =============================================================================
# ATO COMPLIANCE DEADLINE SYNC
# =============================================================================

# Prefix used for compliance events so we can identify them
COMPLIANCE_EVENT_PREFIX = "KRG - ATO: "

# Google Calendar color IDs
COMPLIANCE_COLORS = {
    "bas": "9",       # Blueberry (dark blue)
    "super": "10",    # Basil (dark green)
    "tpar": "3",      # Grape (purple)
    "tax": "5",       # Banana (yellow)
    "insurance": "6", # Tangerine (orange)
}


async def _list_compliance_events(service, fy: int) -> dict[str, str]:
    """
    List all KRG ATO compliance events already in the calendar for a FY.

    Searches July of previous year through October of FY year to cover
    all possible deadline dates.

    Returns:
        Dict mapping "YYYY-MM-DD|event_name" to event ID
    """
    # FY2026 runs Jul 2025 - Jun 2026, but deadlines extend to Oct (tax return)
    time_min = f"{fy - 1}-07-01T00:00:00+10:00"
    time_max = f"{fy}-12-31T23:59:59+11:00"

    existing = {}
    try:
        page_token = None
        while True:
            events_result = service.events().list(
                calendarId=_get_effective_calendar_id(),
                timeMin=time_min,
                timeMax=time_max,
                q=COMPLIANCE_EVENT_PREFIX,
                singleEvents=True,
                maxResults=50,
                pageToken=page_token,
            ).execute()

            for event in events_result.get("items", []):
                summary = event.get("summary", "")
                if summary.startswith(COMPLIANCE_EVENT_PREFIX):
                    event_date = event.get("start", {}).get("date", "")
                    if event_date:
                        key = f"{event_date}|{summary}"
                        existing[key] = event["id"]

            page_token = events_result.get("nextPageToken")
            if not page_token:
                break

    except Exception as e:
        logger.error(f"Failed to list compliance events for FY{fy}: {e}")

    return existing


async def sync_compliance_deadlines(fy: int | None = None) -> dict:
    """
    Sync ATO compliance deadlines to Google Calendar for a financial year.

    Creates all-day reminder events for: BAS lodgement (x4), Super Guarantee (x4),
    TPAR, Tax Return, Workers Comp, Public Liability.

    Each event gets:
    - 1 week reminder (popup)
    - 1 day reminder (popup)
    - Colour-coded by category

    Skips events that already exist (checks by date + summary).

    Returns: {"synced": N, "skipped": N, "errors": N, "fy": YYYY}
    """
    await _load_google_credentials_from_db()
    from app.core.bas import get_compliance_deadlines

    if fy is None:
        from app.core.bas import get_current_fy
        fy = get_current_fy()

    service = _get_calendar_service()
    if not service:
        return {
            "synced": 0, "skipped": 0, "errors": 0, "fy": fy,
            "message": "Google Calendar not configured",
        }

    deadlines = get_compliance_deadlines(fy)

    # Find which deadlines are already synced
    existing = await _list_compliance_events(service, fy)

    synced = 0
    skipped = 0
    errors = 0

    for d in deadlines:
        event_summary = f"{COMPLIANCE_EVENT_PREFIX}{d['name']}"
        date_str = d["due_date"].isoformat()
        lookup_key = f"{date_str}|{event_summary}"

        # Already in calendar
        if lookup_key in existing:
            skipped += 1
            logger.debug(f"Compliance event already synced: {d['name']} ({date_str})")
            continue

        # Create the event
        try:
            end_date = d["due_date"] + timedelta(days=1)
            color_id = COMPLIANCE_COLORS.get(d["category"], "9")

            event_body = {
                "summary": event_summary,
                "description": (
                    f"ATO / Business Compliance Deadline\n\n"
                    f"{d['description']}\n\n"
                    f"FY{fy} — {d['name']}\n"
                    f"Due: {d['due_date'].strftime('%d %B %Y')}"
                ),
                "start": {
                    "date": date_str,
                    "timeZone": "Australia/Sydney",
                },
                "end": {
                    "date": end_date.isoformat(),
                    "timeZone": "Australia/Sydney",
                },
                "colorId": color_id,
                "reminders": {
                    "useDefault": False,
                    "overrides": [
                        {"method": "popup", "minutes": 10080},  # 1 week before
                        {"method": "popup", "minutes": 1440},   # 1 day before
                    ],
                },
            }

            service.events().insert(
                calendarId=_get_effective_calendar_id(),
                body=event_body,
            ).execute()

            synced += 1
            logger.info(f"Synced compliance deadline to calendar: {d['name']} ({date_str})")

        except Exception as e:
            errors += 1
            logger.error(f"Failed to sync compliance deadline {d['name']} ({date_str}): {e}")

    total = synced + skipped + errors
    logger.info(
        f"Compliance deadline sync complete for FY{fy}: "
        f"{synced} synced, {skipped} skipped, {errors} errors out of {total}"
    )

    return {
        "synced": synced,
        "skipped": skipped,
        "errors": errors,
        "fy": fy,
        "total": total,
    }


async def get_synced_compliance_deadlines(fy: int | None = None) -> list[dict]:
    """
    Check which compliance deadlines are already in the calendar.

    Returns:
        List of {"name": str, "due_date": str, "category": str, "synced": bool}
    """
    await _load_google_credentials_from_db()
    from app.core.bas import get_compliance_deadlines, get_current_fy

    if fy is None:
        fy = get_current_fy()

    deadlines = get_compliance_deadlines(fy)

    existing: dict[str, str] = {}
    service = _get_calendar_service()
    if service:
        existing = await _list_compliance_events(service, fy)

    result = []
    for d in deadlines:
        event_summary = f"{COMPLIANCE_EVENT_PREFIX}{d['name']}"
        date_str = d["due_date"].isoformat()
        lookup_key = f"{date_str}|{event_summary}"

        result.append({
            "name": d["name"],
            "due_date": date_str,
            "category": d["category"],
            "synced": lookup_key in existing,
        })

    return result
