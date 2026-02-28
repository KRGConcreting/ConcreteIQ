"""
Review Tasks — Send review requests after job completion.

Sends Google review requests 3 days after job completion.
"""

import logging
from datetime import timedelta

from sqlalchemy import select, and_

from app.celery_app import celery_app
from app.core.dates import sydney_today
from app.core.security import decrypt_customer_pii
from app.database import get_sync_session
from app.models import Quote, Customer
from app.config import settings

logger = logging.getLogger(__name__)


@celery_app.task
def send_review_requests():
    """
    Send review request 3 days after job completion.

    Only sends if:
    - Job is marked as completed
    - Completed 3 days ago
    - Review request hasn't been sent yet

    Runs daily at 2 PM.
    """
    with get_sync_session() as db:
        target_date = sydney_today() - timedelta(days=3)

        # Find jobs completed 3 days ago that haven't had review request sent
        result = db.execute(
            select(Quote).where(
                and_(
                    Quote.status == "completed",
                    Quote.completed_date == target_date,
                    Quote.review_requested == False,
                )
            )
        )

        sent = 0
        for job in result.scalars():
            customer = db.get(Customer, job.customer_id)
            if not customer:
                continue

            # Decrypt PII so email/phone fields are available
            decrypt_customer_pii(customer)

            if customer.email and customer.notify_email:
                try:
                    from app.notifications.email import send_review_request_email_sync

                    send_review_request_email_sync(
                        customer=customer,
                        quote=job,
                    )

                    # Mark as sent
                    job.review_requested = True
                    sent += 1
                    logger.info(f"Sent review request for quote {job.quote_number}")
                except Exception as e:
                    logger.error(f"Failed to send review request for {job.quote_number}: {e}")

        db.commit()
        return f"Sent {sent} review requests"
