"""
Reminders service — Automated reminders for payments and jobs.

Reminders are scheduled when actions occur (invoice sent, booking confirmed)
and processed by a background job (cron) that calls process_due_reminders().
"""

import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dates import sydney_now, sydney_today, SYDNEY_TZ
from app.core.templates import templates
from app.models import Reminder, Invoice, Quote, Customer, EmailLog, Notification
from app.notifications.email import send_email, _load_email_customizations, _render_subject
from app.core.security import decrypt_customer_pii
from app.settings import service as settings_service

logger = logging.getLogger(__name__)


# =============================================================================
# REMINDER SCHEDULING
# =============================================================================

async def schedule_payment_reminders(
    db: AsyncSession,
    invoice: Invoice,
) -> list[Reminder]:
    """
    Schedule payment reminders for an invoice.

    3-tier escalating reminder schedule:
    - Tier 1 (Friendly): 3 days before + on due date
    - Tier 2 (Firm):     3 days overdue
    - Tier 3 (Final):    7 days overdue + 14 days overdue

    Args:
        db: Database session
        invoice: Invoice to schedule reminders for

    Returns:
        List of created Reminder records
    """
    if not invoice.due_date:
        logger.warning(f"Invoice {invoice.invoice_number} has no due date, skipping reminders")
        return []

    now = sydney_now()
    due_date = invoice.due_date

    # Define 3-tier escalating reminder schedule
    reminder_schedule = [
        ("payment_friendly", timedelta(days=-3)),  # Tier 1: 3 days before due
        ("payment_friendly", timedelta(days=0)),   # Tier 1: On due date
        ("payment_firm",     timedelta(days=3)),   # Tier 2: 3 days overdue
        ("payment_final",    timedelta(days=7)),   # Tier 3: 7 days overdue
        ("payment_final",    timedelta(days=14)),  # Tier 3: 14 days overdue (last warning)
    ]

    reminders = []
    for reminder_type, offset in reminder_schedule:
        # Calculate scheduled date
        scheduled_date = due_date + offset

        # Convert to timezone-aware datetime at 9 AM Sydney time
        from datetime import datetime as _dt
        from app.core.dates import SYDNEY_TZ
        if hasattr(scheduled_date, 'hour'):
            # Already a datetime — make timezone-aware if needed, set to 9 AM
            if scheduled_date.tzinfo is None:
                scheduled_for = scheduled_date.replace(hour=9, minute=0, second=0, microsecond=0, tzinfo=SYDNEY_TZ)
            else:
                scheduled_for = scheduled_date.replace(hour=9, minute=0, second=0, microsecond=0)
        else:
            # It's a date object — combine with time
            scheduled_for = _dt.combine(scheduled_date, _dt.min.time().replace(hour=9), tzinfo=SYDNEY_TZ)

        # Skip if already in the past
        if scheduled_for <= now:
            continue

        # Create reminder
        reminder = Reminder(
            reminder_type=reminder_type,
            entity_type="invoice",
            entity_id=invoice.id,
            scheduled_for=scheduled_for,
        )
        db.add(reminder)
        reminders.append(reminder)

    logger.info(f"Scheduled {len(reminders)} payment reminders for invoice {invoice.invoice_number}")
    return reminders


async def schedule_job_reminders(
    db: AsyncSession,
    quote: Quote,
) -> list[Reminder]:
    """
    Schedule job reminders for a confirmed booking.

    Schedules:
    - 1 week before: job_week
    - 1 day before: job_tomorrow

    Args:
        db: Database session
        quote: Quote with confirmed booking

    Returns:
        List of created Reminder records
    """
    if not quote.confirmed_start_date:
        logger.warning(f"Quote {quote.quote_number} has no confirmed start date, skipping reminders")
        return []

    now = sydney_now()
    job_date = quote.confirmed_start_date

    # Define reminder schedule
    reminder_schedule = [
        ("job_week", timedelta(days=-7)),     # 1 week before
        ("job_tomorrow", timedelta(days=-1)),  # 1 day before
    ]

    reminders = []
    for reminder_type, offset in reminder_schedule:
        # Calculate scheduled date
        scheduled_date = job_date + offset

        # Convert to timezone-aware datetime at 9 AM Sydney time
        from datetime import datetime as _dt
        from app.core.dates import SYDNEY_TZ
        if hasattr(scheduled_date, 'hour'):
            # Already a datetime — make timezone-aware if needed, set to 9 AM
            if scheduled_date.tzinfo is None:
                scheduled_for = scheduled_date.replace(hour=9, minute=0, second=0, microsecond=0, tzinfo=SYDNEY_TZ)
            else:
                scheduled_for = scheduled_date.replace(hour=9, minute=0, second=0, microsecond=0)
        else:
            # It's a date object — combine with time
            scheduled_for = _dt.combine(scheduled_date, _dt.min.time().replace(hour=9), tzinfo=SYDNEY_TZ)

        # Skip if already in the past
        if scheduled_for <= now:
            continue

        # Create reminder
        reminder = Reminder(
            reminder_type=reminder_type,
            entity_type="quote",
            entity_id=quote.id,
            scheduled_for=scheduled_for,
        )
        db.add(reminder)
        reminders.append(reminder)

    logger.info(f"Scheduled {len(reminders)} job reminders for quote {quote.quote_number}")
    return reminders


async def cancel_reminders(
    db: AsyncSession,
    entity_type: str,
    entity_id: int,
) -> int:
    """
    Cancel pending reminders for an entity.

    Args:
        db: Database session
        entity_type: Entity type (invoice, quote)
        entity_id: Entity ID

    Returns:
        Number of reminders cancelled
    """
    # Find pending reminders (not sent, not cancelled)
    result = await db.execute(
        select(Reminder).where(
            and_(
                Reminder.entity_type == entity_type,
                Reminder.entity_id == entity_id,
                Reminder.sent_at == None,
                Reminder.cancelled_at == None,
            )
        )
    )
    reminders = result.scalars().all()

    now = sydney_now()
    count = 0
    for reminder in reminders:
        reminder.cancelled_at = now
        count += 1

    if count > 0:
        logger.info(f"Cancelled {count} reminders for {entity_type} {entity_id}")

    return count


# =============================================================================
# REMINDER PROCESSING
# =============================================================================

async def process_due_reminders(db: AsyncSession) -> int:
    """
    Process all due reminders.

    Called by background job (cron). Finds reminders where:
    - scheduled_for <= now
    - sent_at is null
    - cancelled_at is null

    Payment reminders respect the `payment_mode` setting:
    - "manual": Creates in-app notifications prompting Kyle to send them
    - "auto": Sends emails directly to customers (3-tier escalating)

    Job reminders (day-before, week-before) are always auto-sent.

    Args:
        db: Database session

    Returns:
        Number of reminders processed
    """
    now = sydney_now()

    # Find due reminders
    result = await db.execute(
        select(Reminder).where(
            and_(
                Reminder.scheduled_for <= now,
                Reminder.sent_at == None,
                Reminder.cancelled_at == None,
            )
        ).order_by(Reminder.scheduled_for)
    )
    reminders = result.scalars().all()

    processed = 0
    for reminder in reminders:
        success = await _process_reminder(db, reminder)
        if success:
            reminder.sent_at = now
            processed += 1

    if processed > 0:
        logger.info(f"Processed {processed} due reminders")

    return processed


async def _process_reminder(
    db: AsyncSession,
    reminder: Reminder,
) -> bool:
    """
    Process a single reminder.

    Payment reminders -> mode depends on settings:
      - "manual": create in-app notification for Kyle to decide
      - "auto": send email/SMS directly to customer
    Job reminders -> always auto-send email/SMS.

    Args:
        db: Database session
        reminder: Reminder to process

    Returns:
        True if reminder was processed successfully
    """
    if reminder.entity_type == "invoice":
        # Check payment_mode setting
        reminder_settings = await settings_service.get_settings_by_category(db, 'reminders')
        payment_mode = reminder_settings.get('payment_mode', 'manual')

        if payment_mode == "auto":
            return await _send_payment_reminder(db, reminder)
        else:
            return await _create_payment_reminder_notification(db, reminder)
    elif reminder.entity_type == "quote":
        return await _send_job_reminder(db, reminder)
    else:
        logger.warning(f"Unknown reminder entity type: {reminder.entity_type}")
        return False


# =============================================================================
# PAYMENT REMINDER NOTIFICATIONS (manual system)
# =============================================================================

# Map reminder types to human-readable tier labels and suggested actions
REMINDER_TIER_INFO = {
    "payment_friendly": {"tier": "friendly", "label": "Friendly Reminder", "priority": "normal"},
    "payment_firm":     {"tier": "firm",     "label": "Firm Reminder",     "priority": "high"},
    "payment_final":    {"tier": "final",    "label": "Final Notice",      "priority": "critical"},
    "payment_due":      {"tier": "friendly", "label": "Payment Due",       "priority": "normal"},
    "payment_overdue":  {"tier": "firm",     "label": "Payment Overdue",   "priority": "high"},
}


async def _create_payment_reminder_notification(
    db: AsyncSession,
    reminder: Reminder,
) -> bool:
    """
    Create an in-app notification for a payment reminder instead of auto-sending.

    Kyle sees these on the dashboard and decides whether/when to send.

    Args:
        db: Database session
        reminder: Payment reminder that is due

    Returns:
        True if notification created successfully
    """
    # Get invoice
    invoice = await db.get(Invoice, reminder.entity_id)
    if not invoice:
        logger.warning(f"Invoice {reminder.entity_id} not found for reminder")
        return False

    # Skip if invoice is already paid or voided
    if invoice.status in ("paid", "voided"):
        logger.info(f"Invoice {invoice.invoice_number} {invoice.status}, skipping reminder notification")
        return True  # Mark as processed

    # Get customer for name
    customer = await db.get(Customer, invoice.customer_id)
    if not customer:
        logger.warning(f"Customer {invoice.customer_id} not found for reminder notification")
        return True  # Mark as processed

    decrypt_customer_pii(customer)
    customer_name = customer.name or "Customer"

    # Get tier info
    tier_info = REMINDER_TIER_INFO.get(reminder.reminder_type, REMINDER_TIER_INFO["payment_due"])

    # Calculate days overdue
    days_overdue = 0
    if invoice.due_date:
        today = sydney_today()
        due = invoice.due_date if not hasattr(invoice.due_date, 'date') else invoice.due_date.date()
        delta = (today - due).days
        if delta > 0:
            days_overdue = delta

    # Format balance
    balance_cents = invoice.total_cents - invoice.paid_cents
    balance_formatted = f"${balance_cents / 100:,.2f}"

    # Build notification message based on tier
    rtype = reminder.reminder_type
    if rtype in ("payment_friendly", "payment_due"):
        if days_overdue > 0:
            message = f"{invoice.invoice_number} ({balance_formatted}) for {customer_name} is {days_overdue} day{'s' if days_overdue != 1 else ''} overdue. Send a friendly reminder?"
        else:
            message = f"{invoice.invoice_number} ({balance_formatted}) for {customer_name} is due soon. Send a friendly reminder?"
        title = f"💬 Payment reminder due — {invoice.invoice_number}"
    elif rtype in ("payment_firm", "payment_overdue"):
        message = f"{invoice.invoice_number} ({balance_formatted}) for {customer_name} is {days_overdue} days overdue. Time for a firm reminder?"
        title = f"⚠️ Overdue — {invoice.invoice_number}"
    elif rtype == "payment_final":
        message = f"{invoice.invoice_number} ({balance_formatted}) for {customer_name} is {days_overdue} days overdue. Send final notice?"
        title = f"🚨 Final notice due — {invoice.invoice_number}"
    else:
        message = f"{invoice.invoice_number} ({balance_formatted}) for {customer_name} — payment reminder due"
        title = f"Payment reminder — {invoice.invoice_number}"

    # Create in-app notification
    notification = Notification(
        type=f"payment_reminder_{tier_info['tier']}",
        title=title,
        message=message,
        priority=tier_info["priority"],
        customer_id=customer.id,
        invoice_id=invoice.id,
    )
    db.add(notification)

    logger.info(f"Created {tier_info['label']} notification for invoice {invoice.invoice_number}")
    return True


# =============================================================================
# PAYMENT REMINDER SENDING (called manually from UI)
# =============================================================================

async def _send_payment_reminder(
    db: AsyncSession,
    reminder: Reminder,
) -> bool:
    """
    Send a payment reminder email using the 3-tier system.

    Tier 1 (payment_friendly): Polite tone, "just a heads up"
    Tier 2 (payment_firm):     Firm tone, mentions late fees
    Tier 3 (payment_final):    Final notice, mentions debt collection

    Fallback: Old payment_due/payment_overdue types use the original template.

    Args:
        db: Database session
        reminder: Payment reminder

    Returns:
        True if email sent successfully
    """
    # Get invoice
    invoice = await db.get(Invoice, reminder.entity_id)
    if not invoice:
        logger.warning(f"Invoice {reminder.entity_id} not found for reminder")
        return False

    # Skip if invoice is already paid
    if invoice.status == "paid":
        logger.info(f"Invoice {invoice.invoice_number} already paid, skipping reminder")
        return True  # Mark as processed

    # Skip if invoice is voided
    if invoice.status == "voided":
        logger.info(f"Invoice {invoice.invoice_number} voided, skipping reminder")
        return True  # Mark as processed

    # Get customer
    customer = await db.get(Customer, invoice.customer_id)
    if not customer or not customer.email:
        logger.warning(f"Customer {invoice.customer_id} has no email, skipping reminder")
        return True  # Mark as processed to avoid retry

    decrypt_customer_pii(customer)

    if not customer.notify_email:
        logger.info(f"Customer {customer.id} has email notifications disabled")
        return True  # Mark as processed

    # Build portal URL — must generate a fresh token each time because the DB
    # stores a one-way SHA-256 hash (raw token can't be recovered from hash).
    # This invalidates previous portal links, but the customer always uses the
    # latest link from their most recent email.
    from app.invoices.service import generate_portal_token
    raw_token, hashed_token = generate_portal_token()
    invoice.portal_token = hashed_token
    await db.flush()
    portal_url = f"{settings.app_url}/p/invoice/{raw_token}"

    # Format amounts
    total_formatted = f"${invoice.total_cents / 100:,.2f}"
    paid_formatted = f"${invoice.paid_cents / 100:,.2f}"
    balance_cents = invoice.total_cents - invoice.paid_cents
    balance_formatted = f"${balance_cents / 100:,.2f}"
    due_date_formatted = invoice.due_date.strftime("%d %B %Y") if invoice.due_date else "On receipt"

    # Calculate days overdue
    days_overdue = 0
    if invoice.due_date:
        today = sydney_today()
        due = invoice.due_date if not hasattr(invoice.due_date, 'date') else invoice.due_date.date()
        delta = (today - due).days
        if delta > 0:
            days_overdue = delta

    # Load email template customizations
    customs = await _load_email_customizations(db)

    # Fetch bank details from database settings
    bank = await settings_service.get_bank_details(db)

    # Common template context
    template_ctx = dict(
        invoice=invoice,
        customer=customer,
        portal_url=portal_url,
        total_formatted=total_formatted,
        paid_formatted=paid_formatted,
        balance_formatted=balance_formatted,
        balance_cents=balance_cents,
        due_date_formatted=due_date_formatted,
        days_overdue=days_overdue,
        business_name=settings.trading_as,
        business_phone=settings.business_phone,
        business_email=settings.business_email,
        business_abn=settings.abn,
        business_address=settings.business_address,
        bank_name=bank["bank_name"],
        bank_account_name=bank["bank_account_name"],
        bank_bsb=bank["bank_bsb"],
        bank_account=bank["bank_account"],
    )

    # Select template, subject, and tone based on reminder type
    rtype = reminder.reminder_type
    inv_vars = {"invoice_number": invoice.invoice_number, "days_overdue": str(days_overdue)}

    if rtype == "payment_friendly":
        template_name = "emails/payment_reminder_friendly.html"
        subject = _render_subject(
            customs.get('payment_reminder_friendly_subject', '').strip() or "Friendly Reminder — Invoice {invoice_number}",
            inv_vars
        )
        template_ctx["custom_intro"] = _render_subject(
            customs.get('payment_reminder_friendly_intro', '').strip() or "Just a heads up — your payment for invoice {invoice_number} is coming up soon. We're sure it's just slipped through!",
            inv_vars
        )
        template_ctx["custom_cta"] = customs.get('payment_reminder_friendly_cta', '').strip() or "View Invoice & Payment Details"
        text_content = f"""
Hi {customer.name},

Just a heads up — your payment for invoice {invoice.invoice_number} is coming up soon.

Invoice: {invoice.invoice_number}
Amount Due: {balance_formatted}
Due Date: {due_date_formatted}

Pay online: {portal_url}

Or pay by bank transfer:
Bank: {bank["bank_name"]}
Acc. Name: {bank["bank_account_name"]}
BSB: {bank["bank_bsb"]}
Account: {bank["bank_account"]}
Reference: {invoice.invoice_number}

If you have already made this payment, please disregard this reminder. Bank transfers can take 1-2 business days to process.

Thanks,
{settings.trading_as}
""".strip()

    elif rtype == "payment_firm":
        template_name = "emails/payment_reminder_firm.html"
        subject = _render_subject(
            customs.get('payment_reminder_firm_subject', '').strip() or "Overdue Invoice — {invoice_number}",
            inv_vars
        )
        template_ctx["custom_intro"] = _render_subject(
            customs.get('payment_reminder_firm_intro', '').strip() or "Your payment for invoice {invoice_number} is now {days_overdue} days overdue. Please arrange payment as soon as possible.",
            inv_vars
        )
        template_ctx["custom_cta"] = customs.get('payment_reminder_firm_cta', '').strip() or "View Invoice & Arrange Payment"
        text_content = f"""
Hi {customer.name},

Your payment for invoice {invoice.invoice_number} is now {days_overdue} days overdue. Please arrange payment at your earliest convenience to avoid any further action.

Invoice: {invoice.invoice_number}
Balance Due: {balance_formatted}
Original Due Date: {due_date_formatted}

Please note: Late payment fees may apply for invoices that remain overdue for more than 14 days.

Pay online: {portal_url}

Or pay by bank transfer:
Bank: {bank["bank_name"]}
Acc. Name: {bank["bank_account_name"]}
BSB: {bank["bank_bsb"]}
Account: {bank["bank_account"]}
Reference: {invoice.invoice_number}

If you are experiencing difficulties, please contact us immediately to discuss payment arrangements.

{settings.trading_as}
{settings.business_phone}
""".strip()

    elif rtype == "payment_final":
        template_name = "emails/payment_reminder_final.html"
        subject = _render_subject(
            customs.get('payment_reminder_final_subject', '').strip() or "URGENT: Overdue Invoice — {invoice_number}",
            inv_vars
        )
        template_ctx["custom_intro"] = _render_subject(
            customs.get('payment_reminder_final_intro', '').strip() or "Despite previous reminders, invoice {invoice_number} remains unpaid and is now {days_overdue} days overdue. Immediate payment is required.",
            inv_vars
        )
        template_ctx["custom_cta"] = customs.get('payment_reminder_final_cta', '').strip() or "View Invoice & Arrange Payment"
        text_content = f"""
Hi {customer.name},

Despite previous reminders, invoice {invoice.invoice_number} remains unpaid and is now {days_overdue} days overdue. This is the final notice before further action is taken.

Invoice: {invoice.invoice_number}
Outstanding Balance: {balance_formatted}
Original Due Date: {due_date_formatted}

IMPORTANT: If payment of {balance_formatted} is not received within 7 days of this notice, we may be required to refer this matter to a debt collection agency. This may result in additional costs to you and could affect your credit rating.

Pay online: {portal_url}

Or pay by bank transfer:
Bank: {bank["bank_name"]}
Acc. Name: {bank["bank_account_name"]}
BSB: {bank["bank_bsb"]}
Account: {bank["bank_account"]}
Reference: {invoice.invoice_number}

If you are experiencing financial difficulty, please contact us IMMEDIATELY on {settings.business_phone} so we can discuss payment arrangements.

{settings.trading_as}
{settings.business_phone} | {settings.business_email}
""".strip()

    else:
        # Fallback for old payment_due/payment_overdue types already in DB
        template_name = "emails/payment_reminder.html"
        is_overdue = rtype == "payment_overdue"
        template_ctx["is_overdue"] = is_overdue
        subject = _render_subject(
            customs.get('payment_reminder_subject', '').strip() or "Payment Reminder — Invoice {invoice_number}",
            inv_vars
        )
        template_ctx["custom_intro"] = _render_subject(
            customs.get('payment_reminder_intro', '').strip() or "This is a friendly reminder that your payment for invoice {invoice_number} is coming up soon.",
            inv_vars
        )
        template_ctx["custom_cta"] = customs.get('payment_reminder_cta', '').strip() or "View Invoice & Payment Details"
        status_text = "overdue" if is_overdue else "due"
        text_content = f"""
Hi {customer.name},

This is a friendly reminder that invoice {invoice.invoice_number} is {status_text}.

Invoice: {invoice.invoice_number}
Amount Due: {balance_formatted}
Due Date: {due_date_formatted}

Pay online: {portal_url}

Or pay by bank transfer:
Bank: {bank["bank_name"]}
Acc. Name: {bank["bank_account_name"]}
BSB: {bank["bank_bsb"]}
Account: {bank["bank_account"]}
Reference: {invoice.invoice_number}

If you have already made this payment, please disregard this reminder.

Thanks,
{settings.trading_as}
""".strip()

    # Render HTML template
    try:
        html_content = templates.get_template(template_name).render(**template_ctx)
    except Exception as e:
        logger.error(f"Failed to render {template_name}: {e}")
        return False

    success = await send_email(
        to=customer.email,
        subject=subject,
        html_body=html_content,
        text_body=text_content,
        db=db,
        invoice_id=invoice.id,
        customer_id=customer.id,
        template_name=rtype,
    )

    if success:
        logger.info(f"Sent {rtype} reminder for invoice {invoice.invoice_number}")

    return success


# =============================================================================
# JOB REMINDERS
# =============================================================================

async def _send_job_reminder(
    db: AsyncSession,
    reminder: Reminder,
) -> bool:
    """
    Send a job reminder email.

    Args:
        db: Database session
        reminder: Job reminder

    Returns:
        True if email sent successfully
    """
    # Get quote
    quote = await db.get(Quote, reminder.entity_id)
    if not quote:
        logger.warning(f"Quote {reminder.entity_id} not found for reminder")
        return False

    # Skip if quote is not confirmed
    if quote.status != "confirmed":
        logger.info(f"Quote {quote.quote_number} not confirmed (status={quote.status}), skipping reminder")
        return True  # Mark as processed

    # Get customer
    customer = await db.get(Customer, quote.customer_id)
    if not customer or not customer.email:
        logger.warning(f"Customer {quote.customer_id} has no email, skipping reminder")
        return True  # Mark as processed to avoid retry

    if not customer.notify_email:
        logger.info(f"Customer {customer.id} has email notifications disabled")
        return True  # Mark as processed

    # Format date
    job_date_formatted = quote.confirmed_start_date.strftime("%A, %d %B %Y") if quote.confirmed_start_date else "TBD"

    # Determine email subject based on reminder type
    if reminder.reminder_type == "job_week":
        subject = f"Your Job Next Week - {job_date_formatted}"
        time_description = "next week"
    else:  # job_tomorrow
        subject = f"Your Job Tomorrow - {job_date_formatted}"
        time_description = "tomorrow"

    # Render HTML template
    try:
        html_content = templates.get_template("emails/job_reminder.html").render(
            quote=quote,
            customer=customer,
            job_date_formatted=job_date_formatted,
            time_description=time_description,
            business_name=settings.trading_as,
            business_phone=settings.business_phone,
            business_email=settings.business_email,
            business_abn=settings.abn,
            business_address=settings.business_address,
        )
    except Exception as e:
        logger.error(f"Failed to render job reminder template: {e}")
        return False

    # Plain text version
    text_content = f"""
Hi {customer.name},

This is a friendly reminder that your concreting job is scheduled for {time_description}.

Job Details:
Date: {job_date_formatted}
Location: {quote.job_address or 'As discussed'}
Quote: {quote.quote_number}

Please ensure the site is accessible and cleared for our team.

If you need to reschedule or have any questions, please call {settings.business_phone} as soon as possible.

We look forward to seeing you!

Thanks,
{settings.trading_as}
{settings.business_phone}
""".strip()

    success = await send_email(
        to=customer.email,
        subject=subject,
        html_body=html_content,
        text_body=text_content,
        db=db,
        quote_id=quote.id,
    )

    if success:
        logger.info(f"Sent {reminder.reminder_type} reminder for quote {quote.quote_number}")

    return success


# =============================================================================
# REMINDER QUERIES
# =============================================================================

async def get_pending_reminders(
    db: AsyncSession,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
) -> list[Reminder]:
    """
    Get pending (not sent, not cancelled) reminders.

    Args:
        db: Database session
        entity_type: Optional filter by entity type
        entity_id: Optional filter by entity ID

    Returns:
        List of pending reminders
    """
    query = select(Reminder).where(
        and_(
            Reminder.sent_at == None,
            Reminder.cancelled_at == None,
        )
    )

    if entity_type:
        query = query.where(Reminder.entity_type == entity_type)

    if entity_id:
        query = query.where(Reminder.entity_id == entity_id)

    query = query.order_by(Reminder.scheduled_for)

    result = await db.execute(query)
    return list(result.scalars().all())
