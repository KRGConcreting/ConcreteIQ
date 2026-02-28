"""
Follow-up Tasks — Auto-check for quotes needing follow-up reminders.

Creates in-app notifications when quotes have been sent but not responded to
after 3, 7, and 14 days.
"""

import asyncio
import logging

from app.celery_app import celery_app
from app.database import async_session_maker

logger = logging.getLogger(__name__)


async def _check_followups_async() -> int:
    """Async helper to run check_quote_followups with async DB session."""
    from app.notifications.service import check_quote_followups

    async with async_session_maker() as db:
        count = await check_quote_followups(db)
        await db.commit()
        return count


@celery_app.task
def check_quote_followups():
    """
    Check for quotes needing follow-up and create reminder notifications.

    Runs daily at 9:15 AM Sydney time.
    """
    try:
        count = asyncio.run(_check_followups_async())
        logger.info(f"Quote follow-up check: {count} reminders created")
        return f"Created {count} follow-up reminders"
    except Exception as e:
        logger.error(f"Quote follow-up check failed: {e}")
        return f"Error: {e}"
