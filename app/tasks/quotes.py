"""
Quote Tasks — Automatic quote lifecycle management.

Handles:
- Expiring quotes past their expiry date
"""

import logging

from sqlalchemy import select

from app.celery_app import celery_app
from app.database import get_sync_session
from app.models import Quote, ActivityLog
from app.core.dates import sydney_now, sydney_today

logger = logging.getLogger(__name__)


@celery_app.task
def expire_old_quotes():
    """
    Mark quotes past their expiry_date as expired.

    Finds quotes where:
    - expiry_date < today (Sydney time)
    - status is still draft, sent, or viewed

    Runs daily at 12:30 AM Sydney time.
    """
    with get_sync_session() as db:
        today = sydney_today()
        now = sydney_now()

        # Find quotes that are past expiry and still in active pre-acceptance statuses
        result = db.execute(
            select(Quote).where(
                Quote.expiry_date < today,
                Quote.status.in_(["draft", "sent", "viewed"]),
            )
        )
        expired_quotes = result.scalars().all()

        for quote in expired_quotes:
            quote.status = "expired"
            quote.updated_at = now

        if expired_quotes:
            # Log activity for the batch expiry
            activity = ActivityLog(
                action="quotes_auto_expired",
                description=f"Auto-expired {len(expired_quotes)} quotes past their expiry date",
                entity_type="quote",
                extra_data={
                    "quote_ids": [q.id for q in expired_quotes],
                    "quote_numbers": [q.quote_number for q in expired_quotes],
                },
            )
            db.add(activity)

        db.commit()

        count = len(expired_quotes)
        if count:
            logger.info(f"Auto-expired {count} quotes past expiry date")
        return f"Expired {count} quotes"
