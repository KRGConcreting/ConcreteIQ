"""
Notifications service - Create and manage in-app notifications.

Provides a centralized way to create notifications for various events:
- Quote events: viewed, accepted, declined, expired
- Invoice events: sent, overdue, payment received
- Job events: scheduled, tomorrow reminder
- Customer events: new customer
"""

from datetime import date, datetime, timedelta
from typing import Optional, List
from sqlalchemy import select, update, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, Quote, Invoice, Customer, Payment
from app.core.dates import sydney_now, sydney_today


# =============================================================================
# CORE NOTIFICATION FUNCTIONS
# =============================================================================

async def create_notification(
    db: AsyncSession,
    type: str,
    title: str,
    message: str,
    priority: str = "normal",
    customer_id: Optional[int] = None,
    quote_id: Optional[int] = None,
    invoice_id: Optional[int] = None,
) -> Notification:
    """Create a new notification."""
    notification = Notification(
        type=type,
        title=title,
        message=message,
        priority=priority,
        customer_id=customer_id,
        quote_id=quote_id,
        invoice_id=invoice_id,
    )
    db.add(notification)
    return notification


async def get_unread_notifications(
    db: AsyncSession,
    limit: int = 20,
) -> List[Notification]:
    """Get unread notifications, most recent first."""
    result = await db.execute(
        select(Notification)
        .where(Notification.is_read == False)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_all_notifications(
    db: AsyncSession,
    limit: int = 50,
    offset: int = 0,
    include_read: bool = True,
) -> tuple[List[Notification], int]:
    """Get all notifications with pagination."""
    query = select(Notification)

    if not include_read:
        query = query.where(Notification.is_read == False)

    # Get total count
    count_query = select(func.count(Notification.id))
    if not include_read:
        count_query = count_query.where(Notification.is_read == False)
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Get notifications
    result = await db.execute(
        query
        .order_by(Notification.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    return list(result.scalars().all()), total


async def mark_as_read(db: AsyncSession, notification_id: int) -> bool:
    """Mark a single notification as read."""
    result = await db.execute(
        update(Notification)
        .where(Notification.id == notification_id)
        .values(is_read=True)
    )
    return result.rowcount > 0


async def mark_all_as_read(db: AsyncSession) -> int:
    """Mark all notifications as read. Returns count of updated."""
    result = await db.execute(
        update(Notification)
        .where(Notification.is_read == False)
        .values(is_read=True)
    )
    return result.rowcount


async def delete_notification(db: AsyncSession, notification_id: int) -> bool:
    """Delete a notification."""
    notification = await db.get(Notification, notification_id)
    if notification:
        await db.delete(notification)
        return True
    return False


async def get_unread_count(db: AsyncSession) -> int:
    """Get count of unread notifications."""
    result = await db.execute(
        select(func.count(Notification.id))
        .where(Notification.is_read == False)
    )
    return result.scalar() or 0


# =============================================================================
# QUOTE NOTIFICATIONS
# =============================================================================

async def notify_quote_viewed(
    db: AsyncSession,
    quote: Quote,
    customer: Optional[Customer] = None,
) -> Notification:
    """Notify when a customer views their quote."""
    if not customer and quote.customer_id:
        customer = await db.get(Customer, quote.customer_id)

    customer_name = customer.name if customer else "Customer"

    return await create_notification(
        db=db,
        type="quote_viewed",
        title="Quote Viewed",
        message=f"{customer_name} opened quote {quote.quote_number}",
        priority="normal",
        customer_id=quote.customer_id,
        quote_id=quote.id,
    )


async def notify_quote_accepted(
    db: AsyncSession,
    quote: Quote,
    customer: Optional[Customer] = None,
) -> Notification:
    """Notify when a customer accepts a quote."""
    if not customer and quote.customer_id:
        customer = await db.get(Customer, quote.customer_id)

    customer_name = customer.name if customer else "Customer"
    total = (quote.total_cents or 0) / 100

    return await create_notification(
        db=db,
        type="quote_accepted",
        title="🎉 Quote Accepted!",
        message=f"{customer_name} accepted {quote.quote_number} (${total:,.0f}). First payment invoice sent.",
        priority="high",
        customer_id=quote.customer_id,
        quote_id=quote.id,
    )


async def notify_quote_declined(
    db: AsyncSession,
    quote: Quote,
    reason: Optional[str] = None,
    customer: Optional[Customer] = None,
) -> Notification:
    """Notify when a customer declines a quote."""
    if not customer and quote.customer_id:
        customer = await db.get(Customer, quote.customer_id)

    customer_name = customer.name if customer else "Customer"
    message = f"{customer_name} declined quote {quote.quote_number}"
    if reason:
        message += f". Reason: {reason}"

    return await create_notification(
        db=db,
        type="quote_declined",
        title="Quote Declined",
        message=message,
        priority="normal",
        customer_id=quote.customer_id,
        quote_id=quote.id,
    )


async def notify_quote_expired(
    db: AsyncSession,
    quote: Quote,
    customer: Optional[Customer] = None,
) -> Notification:
    """Notify when a quote expires without response."""
    if not customer and quote.customer_id:
        customer = await db.get(Customer, quote.customer_id)

    customer_name = customer.name if customer else "Customer"

    return await create_notification(
        db=db,
        type="quote_expired",
        title="Quote Expired",
        message=f"Quote {quote.quote_number} for {customer_name} has expired without response",
        priority="low",
        customer_id=quote.customer_id,
        quote_id=quote.id,
    )


async def notify_date_selected(
    db: AsyncSession,
    quote: Quote,
    customer: Optional[Customer] = None,
    requested_date: Optional[date] = None,
) -> Notification:
    """Notify when a customer selects their preferred start date."""
    if not customer and quote.customer_id:
        customer = await db.get(Customer, quote.customer_id)

    customer_name = customer.name if customer else "Customer"

    if requested_date:
        formatted_date = requested_date.strftime("%A, %d %B %Y")
        message = f"{customer_name} wants to start {quote.quote_number} on {formatted_date}"
    else:
        message = f"{customer_name} selected a start date for {quote.quote_number}"

    return await create_notification(
        db=db,
        type="date_selected",
        title="Start Date Requested",
        message=message,
        priority="high",
        customer_id=quote.customer_id,
        quote_id=quote.id,
    )


# =============================================================================
# AMENDMENT NOTIFICATIONS
# =============================================================================

async def notify_amendment_sent(
    db: AsyncSession,
    amendment,
    quote: Quote,
    customer: Optional[Customer] = None,
) -> Notification:
    """Notify when an amendment/variation is sent to customer."""
    if not customer and quote.customer_id:
        customer = await db.get(Customer, quote.customer_id)

    customer_name = customer.name if customer else "Customer"
    amount = (amendment.amount_cents or 0) / 100
    sign = "+" if amount >= 0 else ""

    return await create_notification(
        db=db,
        type="amendment_sent",
        title="Variation Sent",
        message=f"Variation #{amendment.amendment_number} ({sign}${abs(amount):,.0f}) sent to {customer_name} for {quote.quote_number}",
        priority="normal",
        customer_id=quote.customer_id,
        quote_id=quote.id,
    )


async def notify_amendment_accepted(
    db: AsyncSession,
    amendment,
    quote: Quote,
    customer: Optional[Customer] = None,
) -> Notification:
    """Notify when a customer accepts an amendment/variation."""
    if not customer and quote.customer_id:
        customer = await db.get(Customer, quote.customer_id)

    customer_name = customer.name if customer else "Customer"
    amount = (amendment.amount_cents or 0) / 100
    sign = "+" if amount >= 0 else ""

    return await create_notification(
        db=db,
        type="amendment_accepted",
        title="Variation Accepted!",
        message=f"{customer_name} accepted Variation #{amendment.amendment_number} ({sign}${abs(amount):,.0f}) on {quote.quote_number}",
        priority="high",
        customer_id=quote.customer_id,
        quote_id=quote.id,
    )


async def notify_amendment_declined(
    db: AsyncSession,
    amendment,
    quote: Quote,
    customer: Optional[Customer] = None,
) -> Notification:
    """Notify when a customer declines an amendment/variation."""
    if not customer and quote.customer_id:
        customer = await db.get(Customer, quote.customer_id)

    customer_name = customer.name if customer else "Customer"
    message = f"{customer_name} declined Variation #{amendment.amendment_number} on {quote.quote_number}"
    if amendment.decline_reason:
        message += f". Reason: {amendment.decline_reason}"

    return await create_notification(
        db=db,
        type="amendment_declined",
        title="Variation Declined",
        message=message,
        priority="normal",
        customer_id=quote.customer_id,
        quote_id=quote.id,
    )


# =============================================================================
# INVOICE & PAYMENT NOTIFICATIONS
# =============================================================================

async def notify_invoice_sent(
    db: AsyncSession,
    invoice: Invoice,
    customer: Optional[Customer] = None,
) -> Notification:
    """Notify when an invoice is sent."""
    if not customer and invoice.customer_id:
        customer = await db.get(Customer, invoice.customer_id)

    customer_name = customer.name if customer else "Customer"
    total = (invoice.total_cents or 0) / 100
    stage = invoice.stage or "invoice"

    return await create_notification(
        db=db,
        type="invoice_sent",
        title="Invoice Sent",
        message=f"{stage.title()} invoice {invoice.invoice_number} (${total:,.0f}) sent to {customer_name}",
        priority="normal",
        customer_id=invoice.customer_id,
        invoice_id=invoice.id,
        quote_id=invoice.quote_id,
    )


async def notify_payment_received(
    db: AsyncSession,
    payment: Payment,
    invoice: Optional[Invoice] = None,
    customer: Optional[Customer] = None,
) -> Notification:
    """Notify when a payment is received."""
    if not invoice and payment.invoice_id:
        invoice = await db.get(Invoice, payment.invoice_id)

    if not customer and invoice and invoice.customer_id:
        customer = await db.get(Customer, invoice.customer_id)

    customer_name = customer.name if customer else "Customer"
    amount = (payment.amount_cents or 0) / 100

    # Check if invoice is now fully paid
    is_paid_in_full = False
    if invoice:
        balance = (invoice.total_cents or 0) - (invoice.paid_cents or 0)
        is_paid_in_full = balance <= 0

    if is_paid_in_full:
        title = "💰 Payment - Paid in Full!"
        message = f"${amount:,.2f} received from {customer_name}. Invoice {invoice.invoice_number} is now paid in full."
        priority = "high"
    else:
        title = "💰 Payment Received"
        remaining = balance / 100 if invoice else 0
        message = f"${amount:,.2f} received from {customer_name}. ${remaining:,.2f} remaining."
        priority = "normal"

    return await create_notification(
        db=db,
        type="payment_received",
        title=title,
        message=message,
        priority=priority,
        customer_id=customer.id if customer else None,
        invoice_id=invoice.id if invoice else None,
        quote_id=invoice.quote_id if invoice else None,
    )


async def notify_invoice_overdue(
    db: AsyncSession,
    invoice: Invoice,
    days_overdue: int,
    customer: Optional[Customer] = None,
) -> Notification:
    """Notify when an invoice becomes overdue."""
    if not customer and invoice.customer_id:
        customer = await db.get(Customer, invoice.customer_id)

    customer_name = customer.name if customer else "Customer"
    balance = ((invoice.total_cents or 0) - (invoice.paid_cents or 0)) / 100

    if days_overdue <= 7:
        priority = "normal"
        title = "Invoice Overdue"
    elif days_overdue <= 14:
        priority = "high"
        title = "⚠️ Invoice 2 Weeks Overdue"
    else:
        priority = "critical"
        title = "🚨 Invoice Seriously Overdue"

    return await create_notification(
        db=db,
        type="invoice_overdue",
        title=title,
        message=f"{invoice.invoice_number} from {customer_name} is {days_overdue} days overdue. ${balance:,.2f} outstanding.",
        priority=priority,
        customer_id=invoice.customer_id,
        invoice_id=invoice.id,
        quote_id=invoice.quote_id,
    )


# =============================================================================
# JOB NOTIFICATIONS
# =============================================================================

async def notify_job_scheduled(
    db: AsyncSession,
    quote: Quote,
    scheduled_date: date,
    customer: Optional[Customer] = None,
) -> Notification:
    """Notify when a job is scheduled."""
    if not customer and quote.customer_id:
        customer = await db.get(Customer, quote.customer_id)

    customer_name = customer.name if customer else "Customer"
    date_str = scheduled_date.strftime("%A, %d %B")

    return await create_notification(
        db=db,
        type="job_scheduled",
        title="Job Scheduled",
        message=f"Job for {customer_name} scheduled for {date_str}",
        priority="normal",
        customer_id=quote.customer_id,
        quote_id=quote.id,
    )


async def notify_job_tomorrow(
    db: AsyncSession,
    quote: Quote,
    customer: Optional[Customer] = None,
) -> Notification:
    """Notify about a job scheduled for tomorrow."""
    if not customer and quote.customer_id:
        customer = await db.get(Customer, quote.customer_id)

    customer_name = customer.name if customer else "Customer"
    address = quote.job_address or "TBC"

    # Extract suburb from address
    if address and "," in address:
        parts = address.split(",")
        suburb = parts[-2].strip() if len(parts) > 2 else parts[-1].strip()
    else:
        suburb = address[:30] if address else "TBC"

    return await create_notification(
        db=db,
        type="job_tomorrow",
        title="📅 Job Tomorrow",
        message=f"Job for {customer_name} in {suburb} is scheduled for tomorrow",
        priority="high",
        customer_id=quote.customer_id,
        quote_id=quote.id,
    )


# =============================================================================
# CUSTOMER NOTIFICATIONS
# =============================================================================

async def notify_new_customer(
    db: AsyncSession,
    customer: Customer,
    source: Optional[str] = None,
) -> Notification:
    """Notify when a new customer is added."""
    message = f"New customer: {customer.name}"
    if source:
        message += f" (via {source})"
    if customer.phone:
        message += f" - {customer.phone}"

    return await create_notification(
        db=db,
        type="new_customer",
        title="New Customer",
        message=message,
        priority="low",
        customer_id=customer.id,
    )


# =============================================================================
# QUOTE SENT / INVOICE SENT (Admin-side actions)
# =============================================================================

async def notify_quote_sent(
    db: AsyncSession,
    quote: Quote,
    customer: Optional[Customer] = None,
) -> Notification:
    """Notify when a quote is sent to customer."""
    if not customer and quote.customer_id:
        customer = await db.get(Customer, quote.customer_id)

    customer_name = customer.name if customer else "Customer"
    total = (quote.total_cents or 0) / 100

    return await create_notification(
        db=db,
        type="quote_sent",
        title="Quote Sent",
        message=f"Quote {quote.quote_number} (${total:,.0f}) sent to {customer_name}",
        priority="low",
        customer_id=quote.customer_id,
        quote_id=quote.id,
    )


async def notify_invoice_sent_admin(
    db: AsyncSession,
    invoice: Invoice,
    customer: Optional[Customer] = None,
) -> Notification:
    """Notify when an invoice is sent to customer (admin confirmation)."""
    if not customer and invoice.customer_id:
        customer = await db.get(Customer, invoice.customer_id)

    customer_name = customer.name if customer else "Customer"
    total = (invoice.total_cents or 0) / 100
    stage = invoice.stage or "invoice"

    return await create_notification(
        db=db,
        type="invoice_sent",
        title="Invoice Sent",
        message=f"{stage.title()} invoice {invoice.invoice_number} (${total:,.0f}) sent to {customer_name}",
        priority="low",
        customer_id=invoice.customer_id,
        invoice_id=invoice.id,
        quote_id=invoice.quote_id,
    )


# =============================================================================
# INVOICE VIEWED
# =============================================================================

async def notify_invoice_viewed(
    db: AsyncSession,
    invoice: Invoice,
    customer: Optional[Customer] = None,
) -> Notification:
    """Notify when a customer views their invoice in the portal."""
    if not customer and invoice.customer_id:
        customer = await db.get(Customer, invoice.customer_id)

    customer_name = customer.name if customer else "Customer"

    return await create_notification(
        db=db,
        type="invoice_viewed",
        title="Invoice Viewed",
        message=f"{customer_name} opened invoice {invoice.invoice_number}",
        priority="normal",
        customer_id=invoice.customer_id,
        invoice_id=invoice.id,
        quote_id=invoice.quote_id,
    )


# =============================================================================
# EMAIL TRACKING NOTIFICATIONS
# =============================================================================

async def notify_email_opened(
    db: AsyncSession,
    customer_name: str,
    subject: str,
    customer_id: Optional[int] = None,
    quote_id: Optional[int] = None,
    invoice_id: Optional[int] = None,
) -> Notification:
    """Notify when a customer opens an email."""
    return await create_notification(
        db=db,
        type="email_opened",
        title="Email Opened",
        message=f"{customer_name} opened \"{subject}\"",
        priority="low",
        customer_id=customer_id,
        quote_id=quote_id,
        invoice_id=invoice_id,
    )


async def notify_email_clicked(
    db: AsyncSession,
    customer_name: str,
    subject: str,
    customer_id: Optional[int] = None,
    quote_id: Optional[int] = None,
    invoice_id: Optional[int] = None,
) -> Notification:
    """Notify when a customer clicks a link in an email."""
    return await create_notification(
        db=db,
        type="email_clicked",
        title="Link Clicked",
        message=f"{customer_name} clicked a link in \"{subject}\"",
        priority="normal",
        customer_id=customer_id,
        quote_id=quote_id,
        invoice_id=invoice_id,
    )


async def notify_email_bounced(
    db: AsyncSession,
    customer_name: str,
    subject: str,
    customer_id: Optional[int] = None,
    quote_id: Optional[int] = None,
    invoice_id: Optional[int] = None,
) -> Notification:
    """Notify when an email bounces or is marked as spam."""
    return await create_notification(
        db=db,
        type="email_bounced",
        title="Email Bounced",
        message=f"Email to {customer_name} bounced: \"{subject}\"",
        priority="high",
        customer_id=customer_id,
        quote_id=quote_id,
        invoice_id=invoice_id,
    )


# =============================================================================
# BATCH PROCESSING
# =============================================================================

async def check_overdue_invoices(db: AsyncSession) -> int:
    """
    Check for overdue invoices and create notifications.
    Run this daily via cron job.
    Returns count of new notifications created.
    """
    today = sydney_today()
    count = 0

    # Find overdue invoices that haven't been notified recently
    result = await db.execute(
        select(Invoice)
        .where(Invoice.status.in_(["sent", "viewed", "partial"]))
        .where(Invoice.due_date < today)
        .where(Invoice.total_cents > Invoice.paid_cents)
    )
    invoices = result.scalars().all()

    for invoice in invoices:
        # Mark as overdue if not already
        if invoice.status != "overdue":
            invoice.status = "overdue"

        days_overdue = (today - invoice.due_date).days

        # Only notify at specific intervals: 1, 7, 14, 21, 30 days
        if days_overdue in [1, 7, 14, 21, 30]:
            # Check if we already notified for this interval
            existing = await db.execute(
                select(Notification)
                .where(Notification.invoice_id == invoice.id)
                .where(Notification.type == "invoice_overdue")
                .where(Notification.created_at >= sydney_now() - timedelta(days=6))
            )
            if not existing.scalar():
                await notify_invoice_overdue(db, invoice, days_overdue)
                count += 1

    return count


async def check_jobs_tomorrow(db: AsyncSession) -> int:
    """
    Check for jobs scheduled tomorrow and create reminders.
    Run this daily via cron job (evening).
    Returns count of new notifications created.
    """
    tomorrow = sydney_today() + timedelta(days=1)
    count = 0

    result = await db.execute(
        select(Quote)
        .where(Quote.status.in_(["accepted", "confirmed", "pour_stage"]))
        .where(Quote.confirmed_start_date == tomorrow)
    )
    quotes = result.scalars().all()

    for quote in quotes:
        # Check if we already notified for this job
        existing = await db.execute(
            select(Notification)
            .where(Notification.quote_id == quote.id)
            .where(Notification.type == "job_tomorrow")
            .where(Notification.created_at >= sydney_now() - timedelta(hours=20))
        )
        if not existing.scalar():
            await notify_job_tomorrow(db, quote)
            count += 1

    return count


async def check_expiring_quotes(db: AsyncSession) -> int:
    """
    Check for quotes that expired and create notifications.
    Also sends proactive expiry warning emails to customers before expiry.
    Run this daily via cron job.
    Returns count of new notifications created.
    """
    from app.settings.service import get_settings_by_category

    today = sydney_today()
    count = 0

    # --- 1. Proactive expiry WARNINGS (before expiry) ---
    # Send email warnings to customers whose quotes are about to expire
    reminder_settings = await get_settings_by_category(db, "reminders")
    warning_days = reminder_settings.get("expiry_warning_days", 3)

    if warning_days and warning_days > 0:
        warning_date = today + timedelta(days=warning_days)

        expiring_soon = await db.execute(
            select(Quote)
            .where(Quote.status.in_(["sent", "viewed"]))
            .where(Quote.expiry_date == warning_date)
        )
        expiring_quotes = expiring_soon.scalars().all()

        for quote in expiring_quotes:
            # Check we haven't already sent a warning for this quote
            existing_warning = await db.execute(
                select(Notification)
                .where(Notification.quote_id == quote.id)
                .where(Notification.type == "quote_expiry_warning")
            )
            if existing_warning.scalar():
                continue

            # Send warning email to customer
            customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None
            if customer:
                try:
                    from app.notifications.email import send_quote_expiry_warning_email
                    # Generate fresh raw token — DB stores hash, URL uses raw
                    from app.quotes.service import generate_portal_token as gen_quote_token
                    raw_token, hashed_token = gen_quote_token()
                    quote.portal_token = hashed_token
                    await db.flush()
                    portal_url = f"{_get_app_url()}/p/quote/{raw_token}"
                    await send_quote_expiry_warning_email(
                        db=db,
                        quote=quote,
                        customer=customer,
                        portal_url=portal_url,
                        days_remaining=warning_days,
                    )
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f"Failed to send expiry warning: {e}")

            # Create admin notification
            customer_name = customer.name if customer else "Customer"
            await create_notification(
                db=db,
                type="quote_expiry_warning",
                title="Quote Expiring Soon",
                message=f"Quote {quote.quote_number} for {customer_name} expires in {warning_days} days — expiry warning email sent",
                priority="normal",
                customer_id=quote.customer_id,
                quote_id=quote.id,
            )
            count += 1

    # --- 2. Actual expiry processing (on/after expiry) ---
    result = await db.execute(
        select(Quote)
        .where(Quote.status.in_(["sent", "viewed"]))
        .where(Quote.expiry_date < today)
    )
    quotes = result.scalars().all()

    for quote in quotes:
        # Update status to expired
        quote.status = "expired"

        # Check if we already notified
        existing = await db.execute(
            select(Notification)
            .where(Notification.quote_id == quote.id)
            .where(Notification.type == "quote_expired")
        )
        if not existing.scalar():
            await notify_quote_expired(db, quote)
            count += 1

    return count


def _get_app_url() -> str:
    """Get the app URL from settings."""
    from app.config import settings as app_settings
    return app_settings.app_url


async def check_quote_followups(db: AsyncSession) -> int:
    """
    Check for quotes that need follow-up reminders and create notifications.
    Run this daily via cron job (e.g. 9am Sydney time).

    Reads follow-up schedule from settings (reminders.followup_days).
    If follow-ups are disabled in settings, returns 0.

    Returns count of new notifications created.
    """
    from app.settings.service import get_settings_by_category

    # Check if follow-ups are enabled
    reminder_settings = await get_settings_by_category(db, "reminders")
    if not reminder_settings.get("followup_enabled", True):
        return 0

    # Get configurable follow-up days from settings
    followup_days = reminder_settings.get("followup_days", [3, 7, 14])
    if not isinstance(followup_days, list) or not followup_days:
        followup_days = [3, 7, 14]

    # Sort to ensure ascending order
    followup_days = sorted([d for d in followup_days if isinstance(d, (int, float)) and d > 0])

    today = sydney_today()
    now = sydney_now()
    count = 0

    # Find quotes that are still waiting on a response
    result = await db.execute(
        select(Quote)
        .where(Quote.status.in_(["sent", "viewed"]))
        .where(Quote.sent_at.isnot(None))
    )
    quotes = result.scalars().all()

    # Build follow-up schedule from settings with escalating priority/messaging
    schedule = []
    for i, days in enumerate(followup_days):
        if i == 0:
            schedule.append((days, "📋 Follow-up Reminder",
                f"{{name}}'s quote {{number}} (${{total}}) was sent {days} days ago — want to check in?", "low"))
        elif i == len(followup_days) - 1:
            schedule.append((days, "📋 Final Follow-up",
                f"It's been {days} days since {{name}} received quote {{number}} (${{total}}). Consider a final follow-up.", "high"))
        else:
            schedule.append((days, "📋 Quote Follow-up",
                f"{{name}}'s quote {{number}} (${{total}}) has been waiting {days} days. Time to follow up?", "normal"))

    for quote in quotes:
        sent_date = quote.sent_at.date() if quote.sent_at else None
        if not sent_date:
            continue

        days_since_sent = (today - sent_date).days

        # Skip if customer viewed recently (within last 24h) — they're still engaging
        if quote.viewed_at and (now - quote.viewed_at).total_seconds() < 86400:
            continue

        # Get customer name
        customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None
        customer_name = customer.name if customer else "Customer"
        total = (quote.total_cents or 0) / 100

        for target_days, title, msg_template, priority in schedule:
            if days_since_sent < target_days:
                break  # Schedule is ordered, no point checking later ones

            if days_since_sent != target_days:
                continue  # Only trigger on the exact day

            # Check if we already sent a follow-up notification for this quote recently
            existing = await db.execute(
                select(Notification)
                .where(Notification.quote_id == quote.id)
                .where(Notification.type == "quote_followup")
                .where(Notification.created_at >= now - timedelta(days=6))
            )
            if existing.scalar():
                continue  # Already reminded recently

            message = msg_template.format(
                name=customer_name,
                number=quote.quote_number,
                total=f"{total:,.0f}",
            )

            await create_notification(
                db=db,
                type="quote_followup",
                title=title,
                message=message,
                priority=priority,
                customer_id=quote.customer_id,
                quote_id=quote.id,
            )
            count += 1

    return count
