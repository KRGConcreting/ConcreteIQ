"""Date and time utilities. ALL dates in Sydney timezone."""

import platform
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

# Windows uses '#' for no-pad, Linux/macOS uses '-'
_NO_PAD = "#" if platform.system() == "Windows" else "-"

# Sydney timezone - ALWAYS use this
SYDNEY_TZ = ZoneInfo("Australia/Sydney")


def sydney_now() -> datetime:
    """Get current datetime in Sydney timezone."""
    return datetime.now(tz=SYDNEY_TZ)


def sydney_today() -> date:
    """Get current date in Sydney timezone."""
    return sydney_now().date()


def format_date(d: date | datetime | None) -> str:
    """
    Format date for display.
    Example: '27 January 2026'
    """
    if d is None:
        return ""
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime(f"%{_NO_PAD}d %B %Y")


def format_date_short(d: date | datetime | None) -> str:
    """
    Format date for display (short).
    Example: '27 Jan 2026'
    """
    if d is None:
        return ""
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime(f"%{_NO_PAD}d %b %Y")


def format_datetime(dt: datetime | None) -> str:
    """
    Format datetime for display.
    Example: '27 Jan 2026, 2:34 PM'
    """
    if dt is None:
        return ""
    # Ensure Sydney timezone
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SYDNEY_TZ)
    else:
        dt = dt.astimezone(SYDNEY_TZ)
    return dt.strftime(f"%{_NO_PAD}d %b %Y, %{_NO_PAD}I:%M %p")


def format_time(t: time | datetime | None) -> str:
    """
    Format time for display.
    Example: '2:34 PM'
    """
    if t is None:
        return ""
    if isinstance(t, datetime):
        t = t.time()
    return t.strftime(f"%{_NO_PAD}I:%M %p")


def parse_date(s: str) -> date | None:
    """Parse date from ISO format (YYYY-MM-DD)."""
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def days_until(d: date) -> int:
    """Calculate days until a date."""
    return (d - sydney_today()).days


def is_expired(expiry_date: date | None) -> bool:
    """Check if a date has expired."""
    if expiry_date is None:
        return False
    return expiry_date < sydney_today()


def add_business_days(start: date, days: int) -> date:
    """Add business days (Mon-Fri) to a date."""
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:  # Mon-Fri
            added += 1
    return current


def relative_time(dt: datetime | None) -> str:
    """
    Format datetime as relative time (e.g., '2 days ago', 'just now').
    """
    if dt is None:
        return ""

    now = sydney_now()

    # Ensure timezone-aware comparison
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SYDNEY_TZ)
    else:
        dt = dt.astimezone(SYDNEY_TZ)

    diff = now - dt

    seconds = diff.total_seconds()
    if seconds < 0:
        return "just now"

    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    elif seconds < 604800:  # 7 days
        days = int(seconds / 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"
    elif seconds < 2592000:  # 30 days
        weeks = int(seconds / 604800)
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    elif seconds < 31536000:  # 365 days
        months = int(seconds / 2592000)
        return f"{months} month{'s' if months != 1 else ''} ago"
    else:
        years = int(seconds / 31536000)
        return f"{years} year{'s' if years != 1 else ''} ago"
