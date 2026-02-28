"""
Job Tasks — Background tasks for job reminders.

Sends reminder emails/SMS for upcoming jobs.
"""

import asyncio
import logging
from datetime import date, timedelta

from app.core.dates import sydney_today

from sqlalchemy import select, and_

from app.celery_app import celery_app
from app.database import get_sync_session, async_session_maker
from app.models import Quote, Customer
from app.core.security import decrypt_customer_pii
from app.config import settings

logger = logging.getLogger(__name__)


async def _send_sms_reminder(quote_id: int, customer_id: int) -> dict:
    """Async helper to send job reminder SMS using async DB session."""
    from app.notifications.sms import send_job_reminder_sms

    async with async_session_maker() as db:
        quote = await db.get(Quote, quote_id)
        customer = await db.get(Customer, customer_id)
        if quote and customer:
            result = await send_job_reminder_sms(db, quote, customer)
            await db.commit()
            return result
    return {"success": False, "error": "Quote or customer not found"}


@celery_app.task
def send_job_reminders():
    """
    Send reminder for jobs scheduled tomorrow.

    Runs daily at 7 AM.
    """
    with get_sync_session() as db:
        tomorrow = sydney_today() + timedelta(days=1)

        # Find jobs scheduled for tomorrow
        result = db.execute(
            select(Quote).where(
                and_(
                    Quote.status.in_(["accepted", "confirmed", "pour_stage"]),
                    Quote.confirmed_start_date == tomorrow,
                )
            )
        )

        email_sent = 0
        sms_sent = 0
        for job in result.scalars():
            customer = db.get(Customer, job.customer_id)
            if not customer:
                continue

            # Decrypt PII so email/phone fields are available
            decrypt_customer_pii(customer)

            # Send email reminder
            if customer.email and customer.notify_email:
                try:
                    from app.notifications.email import send_job_reminder_email_sync

                    send_job_reminder_email_sync(
                        quote=job,
                        customer=customer,
                        job_date=tomorrow,
                    )
                    email_sent += 1
                    logger.info(f"Sent job reminder email for quote {job.quote_number}")
                except Exception as e:
                    logger.error(f"Failed to send job reminder email for {job.quote_number}: {e}")

            # Send SMS reminder
            if customer.notify_sms and customer.phone:
                try:
                    sms_result = asyncio.run(_send_sms_reminder(job.id, customer.id))
                    if sms_result.get("success"):
                        sms_sent += 1
                        logger.info(f"Sent job reminder SMS for quote {job.quote_number}")
                    else:
                        logger.warning(f"SMS reminder skipped for {job.quote_number}: {sms_result.get('error')}")
                except Exception as e:
                    logger.error(f"Failed to send job reminder SMS for {job.quote_number}: {e}")

        db.commit()
        return f"Sent {email_sent} email + {sms_sent} SMS job reminders"


@celery_app.task
def send_week_job_reminders():
    """
    Send reminder for jobs scheduled next week.

    Runs daily at 7 AM (same schedule as daily).
    """
    with get_sync_session() as db:
        one_week_from_now = sydney_today() + timedelta(days=7)

        # Find jobs scheduled for one week from now
        result = db.execute(
            select(Quote).where(
                and_(
                    Quote.status.in_(["accepted", "confirmed", "pour_stage"]),
                    Quote.confirmed_start_date == one_week_from_now,
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
                    from app.notifications.email import send_job_reminder_email_sync

                    send_job_reminder_email_sync(
                        quote=job,
                        customer=customer,
                        job_date=one_week_from_now,
                        is_week_reminder=True,
                    )
                    sent += 1
                    logger.info(f"Sent week-ahead job reminder for quote {job.quote_number}")
                except Exception as e:
                    logger.error(f"Failed to send week-ahead reminder for {job.quote_number}: {e}")

        db.commit()
        return f"Sent {sent} week-ahead job reminders"
