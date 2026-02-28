"""
Invoice Tasks — Background tasks for invoice management.

Handles:
- Marking overdue invoices
- Sending payment reminders
"""

import logging
from datetime import timedelta

from sqlalchemy import select, and_

from app.celery_app import celery_app
from app.core.dates import sydney_today
from app.core.security import decrypt_customer_pii
from app.database import get_sync_session
from app.models import Invoice, Customer
from app.config import settings

logger = logging.getLogger(__name__)


@celery_app.task
def check_overdue_invoices():
    """
    Mark invoices as overdue.

    Finds unpaid invoices past their due date and updates status.
    Runs hourly.
    """
    with get_sync_session() as db:
        today = sydney_today()

        # Find unpaid invoices past due date
        result = db.execute(
            select(Invoice).where(
                and_(
                    Invoice.status.in_(["sent", "viewed", "partial"]),
                    Invoice.due_date < today,
                )
            )
        )

        count = 0
        for invoice in result.scalars():
            if invoice.status != "overdue":
                invoice.status = "overdue"
                count += 1
                logger.info(f"Marked invoice {invoice.invoice_number} as overdue")

        db.commit()
        return f"Marked {count} invoices as overdue"


@celery_app.task
def send_payment_reminders():
    """
    Send reminders for invoices due soon or overdue.

    Sends:
    - Reminder 3 days before due date
    - Reminder on due date
    - Weekly reminders for overdue invoices

    Runs daily at 9 AM.
    """
    with get_sync_session() as db:
        today = sydney_today()
        three_days_from_now = today + timedelta(days=3)

        # Invoices due in 3 days
        due_soon_result = db.execute(
            select(Invoice).where(
                and_(
                    Invoice.status.in_(["sent", "viewed"]),
                    Invoice.due_date == three_days_from_now,
                )
            )
        )
        due_soon = due_soon_result.scalars().all()

        # Invoices due today
        due_today_result = db.execute(
            select(Invoice).where(
                and_(
                    Invoice.status.in_(["sent", "viewed"]),
                    Invoice.due_date == today,
                )
            )
        )
        due_today = due_today_result.scalars().all()

        # Overdue invoices (remind every 7 days)
        overdue_result = db.execute(
            select(Invoice).where(Invoice.status == "overdue")
        )
        overdue_all = overdue_result.scalars().all()

        # Filter overdue to only those where days_overdue % 7 == 0
        overdue = []
        for inv in overdue_all:
            if inv.due_date:
                days_overdue = (today - inv.due_date).days
                if days_overdue > 0 and days_overdue % 7 == 0:
                    overdue.append(inv)

        sent = 0
        for invoice in [*due_soon, *due_today, *overdue]:
            customer = db.get(Customer, invoice.customer_id)
            if not customer:
                continue

            # Decrypt PII so email/phone fields are available
            decrypt_customer_pii(customer)

            if not customer.email:
                continue

            if not customer.notify_email:
                continue

            try:
                # Import email function
                from app.notifications.email import send_payment_reminder_email_sync
                from app.invoices.service import generate_portal_token

                is_overdue = invoice.status == "overdue"
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
                sent += 1
                logger.info(f"Sent payment reminder for invoice {invoice.invoice_number}")
            except Exception as e:
                logger.error(f"Failed to send payment reminder for {invoice.invoice_number}: {e}")

        db.commit()
        return f"Sent {sent} payment reminders"
