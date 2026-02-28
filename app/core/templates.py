"""
Jinja2 template setup with custom filters and flash messages.
"""

import json
from datetime import timedelta
from urllib.parse import quote as url_quote, unquote as url_unquote

from fastapi import Request, Response
from fastapi.templating import Jinja2Templates
from app.core.money import format_money
from app.core.dates import format_date, format_datetime, format_date_short, relative_time

# Initialize templates
templates = Jinja2Templates(directory="templates")

# Currency filters for PDF templates (cents to dollars)
def currency_filter(cents):
    """Format cents as currency (e.g., 125000 -> $1,250.00)"""
    if cents is None:
        return "$0.00"
    return f"${cents/100:,.2f}"

def currency_int_filter(cents):
    """Format cents as currency without decimals (e.g., 125000 -> $1,250)"""
    if cents is None:
        return "$0"
    return f"${cents/100:,.0f}"

# Add custom filters
templates.env.filters["money"] = format_money
templates.env.filters["date"] = format_date
templates.env.filters["date_short"] = format_date_short
templates.env.filters["datetime"] = format_datetime
templates.env.filters["currency"] = currency_filter
templates.env.filters["currency_int"] = currency_int_filter
templates.env.filters["relative_time"] = relative_time


# =============================================================================
# FLASH MESSAGES (cookie-based, no session middleware required)
# =============================================================================

FLASH_COOKIE_NAME = "_flash"


def flash(request: Request, message: str, category: str = "info"):
    """
    Queue a flash message to be shown on the next page load.

    Messages are stored temporarily in request.state and written to a cookie
    by consume_flash() when building the response.

    Categories: info, success, warning, error
    """
    if not hasattr(request.state, "_flash_messages"):
        request.state._flash_messages = []
    request.state._flash_messages.append({"message": message, "category": category})


def get_flashed_messages(request: Request) -> list[dict]:
    """
    Read flash messages from the incoming cookie.

    Returns list of {"message": str, "category": str} dicts.
    The cookie is cleared after reading (one-shot).
    """
    raw = request.cookies.get(FLASH_COOKIE_NAME)
    if not raw:
        return []
    try:
        return json.loads(url_unquote(raw))
    except (json.JSONDecodeError, ValueError):
        return []


def set_flash_cookie(response: Response, request: Request):
    """
    Write any queued flash messages to a cookie on the response,
    and clear old flash messages that were already displayed.
    """
    # Write new flash messages to cookie
    messages = getattr(request.state, "_flash_messages", [])
    if messages:
        value = url_quote(json.dumps(messages))
        response.set_cookie(
            FLASH_COOKIE_NAME,
            value,
            max_age=60,
            httponly=True,
            samesite="lax",
        )
    else:
        # Clear the cookie after messages have been read
        if FLASH_COOKIE_NAME in request.cookies:
            response.delete_cookie(FLASH_COOKIE_NAME)


# Make get_flashed_messages available as a Jinja2 global
templates.env.globals["get_flashed_messages"] = get_flashed_messages

# Make timedelta available in templates (for date arithmetic in run sheet, etc.)
templates.env.globals["timedelta"] = timedelta
