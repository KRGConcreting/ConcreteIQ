"""
Reminders Task — Process due reminders from the scheduler.

This task processes reminders that have been scheduled via the reminders service.
It's an alternative/complement to the specific invoice/job reminder tasks.
"""

import logging

from sqlalchemy import select, and_

from app.celery_app import celery_app
from app.core.security import decrypt_customer_pii
from app.database import get_sync_session
from app.models import Reminder, Invoice, Quote, Customer
from app.core.dates import sydney_now
from app.config import settings

logger = logging.getLogger(__name__)


@celery_app.task
def process_due_reminders():
    """
    Process all due reminders.

    Finds reminders where:
    - scheduled_for <= now
    - sent_at is null
    - cancelled_at is null

    Runs every 30 minutes.
    """
    with get_sync_session() as db:
        now = sydney_now()

        # Find due reminders
        result = db.execute(
            select(Reminder).where(
                and_(
                    Reminder.scheduled_for <= now,
                    Reminder.sent_at == None,
                    Reminder.cancelled_at == None,
                )
            ).order_by(Reminder.scheduled_for)
        )

        processed = 0
        for reminder in result.scalars():
            try:
                if reminder.entity_type == "invoice":
                    success = _process_invoice_reminder(db, reminder)
                elif reminder.entity_type == "quote":
                    success = _process_job_reminder(db, reminder)
                else:
                    logger.warning(f"Unknown reminder entity type: {reminder.entity_type}")
                    success = False

                if success:
                    reminder.sent_at = now
                    processed += 1

            except Exception as e:
                logger.error(f"Error processing reminder {reminder.id}: {e}")

        db.commit()
        return f"Processed {processed} due reminders"


def _process_invoice_reminder(db, reminder: Reminder) -> bool:
    """Process a payment reminder."""
    invoice = db.get(Invoice, reminder.entity_id)
    if not invoice:
        logger.warning(f"Invoice {reminder.entity_id} not found for reminder")
        return False

    # Skip if already paid
    if invoice.status == "paid":
        logger.info(f"Invoice {invoice.invoice_number} already paid, skipping reminder")
        return True  # Mark as processed

    # Skip if voided
    if invoice.status == "voided":
        logger.info(f"Invoice {invoice.invoice_number} voided, skipping reminder")
        return True

    customer = db.get(Customer, invoice.customer_id)
    if not customer:
        logger.warning(f"Customer {invoice.customer_id} not found, skipping reminder")
        return True

    # Decrypt PII so email/phone fields are available
    decrypt_customer_pii(customer)

    if not customer.email:
        logger.warning(f"Customer {invoice.customer_id} has no email, skipping reminder")
        return True

    if not customer.notify_email:
        logger.info(f"Customer {customer.id} has email notifications disabled")
        return True

    try:
        from app.notifications.email import send_payment_reminder_email_sync
        from app.core.dates import sydney_today
        from app.invoices.service import generate_portal_token

        today = sydney_today()
        is_overdue = reminder.reminder_type == "payment_overdue"
        days_overdue = (today - invoice.due_date).days if invoice.due_date and is_overdue else 0

        # Generate fresh raw token for the portal URL; store hash in DB
        raw_token, hashed_token = generate_portal_token()
        invoice.portal_token = hashed_token
        db.flush()
        portal_url = f"{settings.app_url}/p/invoice/{raw_token}"

        send_payment_reminder_email_sync(
            invoice=invoice,
            customer=customer,
            is_overdue=is_overdue,
            days_overdue=days_overdue,
            portal_url=portal_url,
        )

        logger.info(f"Sent {reminder.reminder_type} reminder for invoice {invoice.invoice_number}")
        return True

    except Exception as e:
        logger.error(f"Failed to send payment reminder for {invoice.invoice_number}: {e}")
        return False


def _process_job_reminder(db, reminder: Reminder) -> bool:
    """Process a job reminder."""
    quote = db.get(Quote, reminder.entity_id)
    if not quote:
        logger.warning(f"Quote {reminder.entity_id} not found for reminder")
        return False

    # Skip if not confirmed
    if quote.status not in ("accepted", "confirmed"):
        logger.info(f"Quote {quote.quote_number} not confirmed (status={quote.status}), skipping reminder")
        return True

    customer = db.get(Customer, quote.customer_id)
    if not customer:
        logger.warning(f"Customer {quote.customer_id} not found, skipping reminder")
        return True

    # Decrypt PII so email/phone fields are available
    decrypt_customer_pii(customer)

    if not customer.email:
        logger.warning(f"Customer {quote.customer_id} has no email, skipping reminder")
        return True

    if not customer.notify_email:
        logger.info(f"Customer {customer.id} has email notifications disabled")
        return True

    try:
        from app.notifications.email import send_job_reminder_email_sync

        is_week_reminder = reminder.reminder_type == "job_week"

        send_job_reminder_email_sync(
            quote=quote,
            customer=customer,
            job_date=quote.confirmed_start_date,
            is_week_reminder=is_week_reminder,
        )

        logger.info(f"Sent {reminder.reminder_type} reminder for quote {quote.quote_number}")
        return True

    except Exception as e:
        logger.error(f"Failed to send job reminder for {quote.quote_number}: {e}")
        return False
