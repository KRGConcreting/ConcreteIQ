"""
Email service - Postmark integration for sending emails.

Emails fail gracefully - log errors but don't crash the app.
"""

import httpx
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Quote, Invoice, Payment, Customer, CommunicationLog
from app.core.dates import sydney_now
from app.core.templates import templates
from app.core.security import decrypt_customer_pii

logger = logging.getLogger(__name__)

# =============================================================================
# POSTMARK API CLIENT
# =============================================================================

POSTMARK_API_URL = "https://api.postmarkapp.com/email"


async def _get_postmark_key(db: Optional[AsyncSession] = None) -> Optional[str]:
    """
    Get the Postmark API key — checks database first, then falls back to env var.

    Credentials saved via Settings > Integrations go to the DB.
    Credentials set in .env are the fallback.
    """
    if db:
        try:
            from app.settings import service as settings_service
            db_key = await settings_service.get_setting(db, "integrations", "postmark_api_key")
            if db_key:
                return db_key
        except Exception:
            pass
    return settings.postmark_api_key or None


async def _get_email_settings(db: Optional[AsyncSession] = None) -> dict:
    """
    Get email settings (from_name, from_address, reply_to) from DB,
    falling back to env var defaults.

    Settings > Email page saves these to the 'email' category in the DB.
    """
    from_name = ""
    from_address = settings.postmark_from_email or ""
    reply_to = ""

    if db:
        try:
            from app.settings import service as settings_service
            email_settings = await settings_service.get_settings_by_category(db, "email")
            from_name = email_settings.get("from_name", "") or ""
            from_address = email_settings.get("from_address", "") or from_address
            reply_to = email_settings.get("reply_to", "") or ""
        except Exception:
            pass

    return {
        "from_name": from_name.strip(),
        "from_address": from_address.strip(),
        "reply_to": reply_to.strip(),
    }


async def send_email(
    to: str,
    subject: str,
    html_body: str,
    text_body: Optional[str] = None,
    db: Optional[AsyncSession] = None,
    quote_id: Optional[int] = None,
    invoice_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    template_name: Optional[str] = None,
) -> bool:
    """
    Send an email via Postmark API.

    Args:
        to: Recipient email address
        subject: Email subject
        html_body: HTML content
        text_body: Plain text content (optional, derived from HTML if not provided)
        db: Database session for logging (optional)
        quote_id: Link to quote for logging (optional)
        invoice_id: Link to invoice for logging (optional)

    Returns:
        True if email sent successfully, False otherwise.
        NEVER raises exceptions - fails gracefully.
    """
    # Get API key from DB (saved via UI) or env var fallback
    api_key = await _get_postmark_key(db)
    if not api_key:
        logger.warning("Postmark API key not configured - email not sent")
        return False

    if not to:
        logger.warning("No recipient email provided - email not sent")
        return False

    # Get email settings from DB (from_name, from_address, reply_to)
    email_cfg = await _get_email_settings(db)
    from_address = email_cfg["from_address"]
    from_name = email_cfg["from_name"]
    reply_to_addr = email_cfg["reply_to"]

    # Format "From" as "Name <email>" if name is configured
    if from_name:
        from_field = f"{from_name} <{from_address}>"
    else:
        from_field = from_address

    # Use plain text fallback if not provided
    if not text_body:
        text_body = f"Please view this email in an HTML-capable email client.\n\nSubject: {subject}"

    # Prepare payload
    payload = {
        "From": from_field,
        "To": to,
        "Subject": subject,
        "HtmlBody": html_body,
        "TextBody": text_body,
        "MessageStream": "outbound",
    }

    # Add ReplyTo if configured and different from From
    if reply_to_addr and reply_to_addr != from_address:
        payload["ReplyTo"] = reply_to_addr

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Postmark-Server-Token": api_key,
    }

    postmark_message_id = None
    postmark_error = None
    success = False

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                POSTMARK_API_URL,
                json=payload,
                headers=headers,
                timeout=30.0,
            )

            if response.status_code == 200:
                data = response.json()
                postmark_message_id = data.get("MessageID")
                success = True
                logger.info(f"Email sent to {to}: {subject} (MessageID: {postmark_message_id})")
            else:
                try:
                    err_data = response.json()
                    postmark_error = err_data.get("Message", response.text)
                except Exception:
                    postmark_error = response.text
                logger.error(f"Postmark API error {response.status_code}: {postmark_error}")

    except httpx.TimeoutException:
        postmark_error = "Request timed out connecting to Postmark"
        logger.error(f"Postmark API timeout sending to {to}")
    except Exception as e:
        postmark_error = str(e)
        logger.error(f"Email send error: {str(e)}")

    # Log to unified communication log
    if db:
        try:
            comm_log = CommunicationLog(
                channel="email",
                direction="outbound",
                customer_id=customer_id,
                quote_id=quote_id,
                invoice_id=invoice_id,
                to_address=to,
                subject=subject,
                template=template_name or "custom",
                provider_message_id=postmark_message_id,
                status="sent" if success else "failed",
                sent_at=sydney_now() if success else None,
            )
            db.add(comm_log)
            # Don't commit here - let the caller handle the transaction
        except Exception as e:
            logger.error(f"Failed to log email: {str(e)}")

    if not success and postmark_error:
        # Store last error for callers that want more detail
        send_email._last_error = postmark_error
    else:
        send_email._last_error = None

    return success


# =============================================================================
# QUOTE EMAILS
# =============================================================================

async def send_quote_email(
    db: AsyncSession,
    quote: Quote,
    customer: Customer,
    portal_url: str,
) -> bool:
    """
    Send quote email to customer.

    Args:
        db: Database session
        quote: The quote being sent
        customer: The customer receiving the quote
        portal_url: Full URL to the quote portal

    Returns:
        True if email sent successfully, False otherwise.
    """
    decrypt_customer_pii(customer)
    if not customer.email:
        logger.warning(f"Customer {customer.id} has no email - quote email not sent")
        return False

    if not customer.notify_email:
        logger.info(f"Customer {customer.id} has email notifications disabled")
        return False

    # Format total for display
    total_formatted = f"${quote.total_cents / 100:,.2f}"

    # Render HTML template
    try:
        html_content = templates.get_template("emails/quote_sent.html").render(
            quote=quote,
            customer=customer,
            portal_url=portal_url,
            total_formatted=total_formatted,
            business_name=settings.trading_as,
            business_abn=settings.abn,
            business_licence=settings.licence_number,
            business_address=settings.business_address,
            business_phone=settings.business_phone,
            business_email=settings.business_email,
        )
    except Exception as e:
        logger.error(f"Failed to render quote email template: {str(e)}")
        return False

    # Plain text version
    text_content = f"""
Hi {customer.name},

Thank you for requesting a quote from {settings.trading_as}.

Quote: {quote.quote_number}
Total: {total_formatted} (inc GST)

View and accept your quote online:
{portal_url}

This quote is valid until {quote.expiry_date.strftime('%d %B %Y') if quote.expiry_date else '30 days from today'}.

If you have any questions, please call {settings.business_phone} or reply to this email.

Thanks,
{settings.trading_as}
""".strip()

    subject = f"Quote {quote.quote_number} from {settings.trading_as}"

    return await send_email(
        to=customer.email,
        subject=subject,
        html_body=html_content,
        text_body=text_content,
        db=db,
        quote_id=quote.id,
        customer_id=customer.id,
        template_name="quote_sent",
    )


# =============================================================================
# AMENDMENT EMAILS
# =============================================================================

async def send_amendment_email(
    db: AsyncSession,
    amendment,
    quote: Quote,
    customer: Customer,
    portal_url: str,
) -> bool:
    """
    Send amendment/variation email to customer.

    Args:
        db: Database session
        amendment: The QuoteAmendment being sent
        quote: The parent quote
        customer: The customer receiving the email
        portal_url: Full URL to the amendment portal page

    Returns:
        True if email sent successfully, False otherwise.
    """
    decrypt_customer_pii(customer)
    if not customer.email:
        logger.warning(f"Customer {customer.id} has no email - amendment email not sent")
        return False

    if not customer.notify_email:
        logger.info(f"Customer {customer.id} has email notifications disabled")
        return False

    # Format amounts
    original_total = quote.total_cents or 0
    variation_amount = amendment.amount_cents or 0
    adjusted_total = original_total + variation_amount

    original_total_formatted = f"${original_total / 100:,.2f}"
    sign = "+" if variation_amount >= 0 else "-"
    variation_formatted = f"{sign}${abs(variation_amount) / 100:,.2f}"
    adjusted_total_formatted = f"${adjusted_total / 100:,.2f}"

    # Render HTML template
    try:
        html_content = templates.get_template("emails/amendment_sent.html").render(
            amendment=amendment,
            quote=quote,
            customer=customer,
            portal_url=portal_url,
            original_total_formatted=original_total_formatted,
            variation_formatted=variation_formatted,
            adjusted_total_formatted=adjusted_total_formatted,
            business_name=settings.trading_as,
            business_abn=settings.abn,
            business_licence=settings.licence_number,
            business_address=settings.business_address,
            business_phone=settings.business_phone,
            business_email=settings.business_email,
        )
    except Exception as e:
        logger.error(f"Failed to render amendment email template: {str(e)}")
        return False

    # Plain text version
    text_content = f"""
Hi {customer.name},

There's been a scope change on your project. Please review:

Quote: {quote.quote_number}
Variation #{amendment.amendment_number}

What's changing:
{amendment.description}

Original Total: {original_total_formatted}
This Variation: {variation_formatted}
Adjusted Total: {adjusted_total_formatted}

Review and respond online:
{portal_url}

If you have any questions, please call {settings.business_phone} or reply to this email.

Thanks,
{settings.trading_as}
""".strip()

    subject = f"Variation #{amendment.amendment_number} — Quote {quote.quote_number} | {settings.trading_as}"

    return await send_email(
        to=customer.email,
        subject=subject,
        html_body=html_content,
        text_body=text_content,
        db=db,
        quote_id=quote.id,
        customer_id=customer.id,
        template_name="amendment_sent",
    )


# =============================================================================
# INVOICE EMAILS
# =============================================================================

async def send_invoice_email(
    db: AsyncSession,
    invoice: Invoice,
    customer: Customer,
    portal_url: str,
) -> bool:
    """
    Send invoice email to customer.

    Args:
        db: Database session
        invoice: The invoice being sent
        customer: The customer receiving the invoice
        portal_url: Full URL to the invoice portal

    Returns:
        True if email sent successfully, False otherwise.
    """
    decrypt_customer_pii(customer)
    if not customer.email:
        logger.warning(f"Customer {customer.id} has no email - invoice email not sent")
        return False

    if not customer.notify_email:
        logger.info(f"Customer {customer.id} has email notifications disabled")
        return False

    # Format amounts
    total_formatted = f"${invoice.total_cents / 100:,.2f}"
    due_date_formatted = invoice.due_date.strftime("%d %B %Y") if invoice.due_date else "On receipt"

    # Stage description
    stage_labels = {
        "booking": "First Payment",
        "prepour": "Progress Payment",
        "completion": "Final Payment",
    }
    stage_label = stage_labels.get(invoice.stage, invoice.stage or "Invoice")

    # Render HTML template
    try:
        html_content = templates.get_template("emails/invoice_sent.html").render(
            invoice=invoice,
            customer=customer,
            portal_url=portal_url,
            total_formatted=total_formatted,
            due_date_formatted=due_date_formatted,
            stage_label=stage_label,
            business_name=settings.trading_as,
            business_abn=settings.abn,
            business_licence=settings.licence_number,
            business_address=settings.business_address,
            business_phone=settings.business_phone,
            business_email=settings.business_email,
            bank_name=settings.bank_name,
            bank_bsb=settings.bank_bsb,
            bank_account=settings.bank_account,
        )
    except Exception as e:
        logger.error(f"Failed to render invoice email template: {str(e)}")
        return False

    # Plain text version
    text_content = f"""
Hi {customer.name},

Please find attached your invoice from {settings.trading_as}.

Invoice: {invoice.invoice_number}
{stage_label}
Amount Due: {total_formatted}
Due Date: {due_date_formatted}

Pay online:
{portal_url}

Or pay by bank transfer:
Bank: {settings.bank_name}
BSB: {settings.bank_bsb}
Account: {settings.bank_account}
Reference: {invoice.invoice_number}

If you have any questions, please call {settings.business_phone} or reply to this email.

Thanks,
{settings.trading_as}
""".strip()

    subject = f"Invoice {invoice.invoice_number} from {settings.trading_as}"

    return await send_email(
        to=customer.email,
        subject=subject,
        html_body=html_content,
        text_body=text_content,
        db=db,
        invoice_id=invoice.id,
        customer_id=customer.id,
        template_name="invoice_sent",
    )


# =============================================================================
# PAYMENT RECEIPT EMAILS
# =============================================================================

async def send_payment_receipt_email(
    db: AsyncSession,
    payment: Payment,
    invoice: Invoice,
    customer: Customer,
) -> bool:
    """
    Send payment receipt email to customer.

    Args:
        db: Database session
        payment: The payment that was made
        invoice: The invoice that was paid
        customer: The customer who made the payment

    Returns:
        True if email sent successfully, False otherwise.
    """
    decrypt_customer_pii(customer)
    if not customer.email:
        logger.warning(f"Customer {customer.id} has no email - receipt email not sent")
        return False

    if not customer.notify_email:
        logger.info(f"Customer {customer.id} has email notifications disabled")
        return False

    # Format amounts
    amount_formatted = f"${payment.amount_cents / 100:,.2f}"
    total_formatted = f"${invoice.total_cents / 100:,.2f}"
    paid_formatted = f"${invoice.paid_cents / 100:,.2f}"
    balance_cents = invoice.total_cents - invoice.paid_cents
    balance_formatted = f"${balance_cents / 100:,.2f}"

    payment_date_formatted = payment.payment_date.strftime("%d %B %Y") if payment.payment_date else sydney_now().strftime("%d %B %Y")

    # Render HTML template
    try:
        html_content = templates.get_template("emails/payment_receipt.html").render(
            payment=payment,
            invoice=invoice,
            customer=customer,
            amount_formatted=amount_formatted,
            total_formatted=total_formatted,
            paid_formatted=paid_formatted,
            balance_formatted=balance_formatted,
            balance_cents=balance_cents,
            payment_date_formatted=payment_date_formatted,
            business_name=settings.trading_as,
            business_abn=settings.abn,
            business_licence=settings.licence_number,
            business_address=settings.business_address,
            business_phone=settings.business_phone,
            business_email=settings.business_email,
        )
    except Exception as e:
        logger.error(f"Failed to render payment receipt template: {str(e)}")
        return False

    # Plain text version
    balance_text = f"Balance remaining: {balance_formatted}" if balance_cents > 0 else "Invoice paid in full"

    text_content = f"""
Hi {customer.name},

Thank you for your payment!

Payment Receipt
---------------
Invoice: {invoice.invoice_number}
Amount Paid: {amount_formatted}
Date: {payment_date_formatted}
Method: {payment.method or 'Online'}

Invoice Total: {total_formatted}
Total Paid: {paid_formatted}
{balance_text}

If you have any questions, please call {settings.business_phone} or reply to this email.

Thanks,
{settings.trading_as}
""".strip()

    subject = f"Payment Receipt - {invoice.invoice_number}"

    return await send_email(
        to=customer.email,
        subject=subject,
        html_body=html_content,
        text_body=text_content,
        db=db,
        invoice_id=invoice.id,
        customer_id=customer.id,
        template_name="payment_receipt",
    )


# =============================================================================
# SYNCHRONOUS EMAIL HELPERS (for Celery tasks)
# =============================================================================

def send_email_sync(
    to: str,
    subject: str,
    html_body: str,
    text_body: Optional[str] = None,
) -> bool:
    """
    Send an email via Postmark API (synchronous).

    Used by Celery tasks that need synchronous execution.
    Does not log to database - Celery tasks should handle their own logging.

    Returns:
        True if email sent successfully, False otherwise.
    """
    import requests

    if not settings.postmark_api_key:
        logger.warning("Postmark API key not configured - email not sent")
        return False

    if not to:
        logger.warning("No recipient email provided - email not sent")
        return False

    if not text_body:
        text_body = f"Please view this email in an HTML-capable email client.\n\nSubject: {subject}"

    payload = {
        "From": settings.postmark_from_email,
        "To": to,
        "Subject": subject,
        "HtmlBody": html_body,
        "TextBody": text_body,
        "MessageStream": "outbound",
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Postmark-Server-Token": settings.postmark_api_key,
    }

    try:
        response = requests.post(
            POSTMARK_API_URL,
            json=payload,
            headers=headers,
            timeout=30,
        )

        if response.status_code == 200:
            data = response.json()
            postmark_message_id = data.get("MessageID")
            logger.info(f"Email sent to {to}: {subject} (MessageID: {postmark_message_id})")
            return True
        else:
            logger.error(f"Postmark API error {response.status_code}: {response.text}")
            return False

    except Exception as e:
        logger.error(f"Email send error: {str(e)}")
        return False


def send_payment_reminder_email_sync(
    invoice: Invoice,
    customer: Customer,
    is_overdue: bool = False,
    days_overdue: int = 0,
    portal_url: str = "",
) -> bool:
    """
    Send payment reminder email (synchronous).

    Used by Celery tasks for scheduled payment reminders.
    """
    decrypt_customer_pii(customer)
    if not customer.email:
        return False

    if not portal_url:
        logger.error(
            f"portal_url is empty for invoice {invoice.invoice_number} — "
            "customer will receive a broken payment link"
        )

    # Format amounts
    total_formatted = f"${invoice.total_cents / 100:,.2f}"
    paid_formatted = f"${invoice.paid_cents / 100:,.2f}"
    balance_cents = invoice.total_cents - invoice.paid_cents
    balance_formatted = f"${balance_cents / 100:,.2f}"
    due_date_formatted = invoice.due_date.strftime("%d %B %Y") if invoice.due_date else "On receipt"

    # Determine subject and tone
    if is_overdue:
        if days_overdue > 14:
            subject = f"URGENT: Overdue Invoice - {invoice.invoice_number}"
            status_text = f"now {days_overdue} days overdue"
        else:
            subject = f"Overdue Invoice - {invoice.invoice_number}"
            status_text = f"{days_overdue} days overdue"
    else:
        subject = f"Payment Reminder - Invoice {invoice.invoice_number}"
        status_text = f"due on {due_date_formatted}"

    # Render HTML template
    try:
        html_content = templates.get_template("emails/payment_reminder.html").render(
            invoice=invoice,
            customer=customer,
            portal_url=portal_url,
            total_formatted=total_formatted,
            paid_formatted=paid_formatted,
            balance_formatted=balance_formatted,
            balance_cents=balance_cents,
            due_date_formatted=due_date_formatted,
            is_overdue=is_overdue,
            days_overdue=days_overdue,
            business_name=settings.trading_as,
            business_abn=settings.abn,
            business_licence=settings.licence_number,
            business_address=settings.business_address,
            business_phone=settings.business_phone,
            business_email=settings.business_email,
            bank_name=settings.bank_name,
            bank_bsb=settings.bank_bsb,
            bank_account=settings.bank_account,
        )
    except Exception as e:
        logger.error(f"Failed to render payment reminder template: {str(e)}")
        # Fall back to plain text only
        html_content = None

    # Plain text version
    text_content = f"""
Hi {customer.name},

This is a friendly reminder that invoice {invoice.invoice_number} is {status_text}.

Invoice: {invoice.invoice_number}
Amount Due: {balance_formatted}
Due Date: {due_date_formatted}

Pay online:
{portal_url}

Or pay by bank transfer:
Bank: {settings.bank_name}
BSB: {settings.bank_bsb}
Account: {settings.bank_account}
Reference: {invoice.invoice_number}

If you have already made this payment, please disregard this reminder.

If you have any questions, please call {settings.business_phone} or reply to this email.

Thanks,
{settings.trading_as}
""".strip()

    if not html_content:
        html_content = f"<html><body><pre>{text_content}</pre></body></html>"

    return send_email_sync(
        to=customer.email,
        subject=subject,
        html_body=html_content,
        text_body=text_content,
    )


def send_job_reminder_email_sync(
    quote: Quote,
    customer: Customer,
    job_date,
    is_week_reminder: bool = False,
) -> bool:
    """
    Send job reminder email (synchronous).

    Used by Celery tasks for scheduled job reminders.
    """
    decrypt_customer_pii(customer)
    if not customer.email:
        return False

    job_date_formatted = job_date.strftime("%A, %d %B %Y") if job_date else "TBD"

    if is_week_reminder:
        subject = f"Your Job Next Week - {job_date_formatted}"
        time_description = "next week"
    else:
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
            business_abn=settings.abn,
            business_licence=settings.licence_number,
            business_address=settings.business_address,
            business_phone=settings.business_phone,
            business_email=settings.business_email,
        )
    except Exception as e:
        logger.error(f"Failed to render job reminder template: {str(e)}")
        html_content = None

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

    if not html_content:
        html_content = f"<html><body><pre>{text_content}</pre></body></html>"

    return send_email_sync(
        to=customer.email,
        subject=subject,
        html_body=html_content,
        text_body=text_content,
    )


# =============================================================================
# REVIEW REQUEST EMAIL (async)
# =============================================================================

async def send_review_request_email(
    db: AsyncSession,
    quote: Quote,
    customer: Customer,
) -> bool:
    """
    Send review request email to customer (async).

    Args:
        db: Database session
        quote: The completed quote/job
        customer: The customer to request a review from

    Returns:
        True if email sent successfully, False otherwise.
    """
    decrypt_customer_pii(customer)
    if not customer.email:
        logger.warning(f"Customer {customer.id} has no email - review request not sent")
        return False

    if not customer.notify_email:
        logger.info(f"Customer {customer.id} has email notifications disabled")
        return False

    review_url = settings.google_review_url or "#"
    subject = f"How did we do? — {settings.trading_as}"

    # Render HTML template
    try:
        html_content = templates.get_template("emails/review_request.html").render(
            customer=customer,
            quote=quote,
            review_url=review_url,
            business_name=settings.trading_as,
            business_abn=settings.abn,
            business_licence=settings.licence_number,
            business_address=settings.business_address,
            business_phone=settings.business_phone,
            business_email=settings.business_email,
        )
    except Exception as e:
        logger.error(f"Failed to render review request template: {str(e)}")
        return False

    # Plain text version
    text_content = f"""
Hi {customer.name},

Thank you for choosing {settings.trading_as} for your recent concreting project!

We hope you're happy with the finished result. If you have a moment, we'd really appreciate a Google review:

{review_url}

It only takes a minute and would mean a lot to our team!

If you have any concerns, please call us on {settings.business_phone}.

Thanks again,
{settings.trading_as}
""".strip()

    return await send_email(
        to=customer.email,
        subject=subject,
        html_body=html_content,
        text_body=text_content,
        db=db,
        quote_id=quote.id,
        customer_id=customer.id,
        template_name="review_request",
    )


# =============================================================================
# QUOTE FOLLOWUP EMAIL (async)
# =============================================================================

async def send_quote_followup_email(
    db: AsyncSession,
    quote: Quote,
    customer: Customer,
    portal_url: str,
    followup_number: int = 1,
) -> bool:
    """
    Send quote followup email to customer (async).

    Args:
        db: Database session
        quote: The quote to follow up on
        customer: The customer who received the quote
        portal_url: Full URL to the quote portal
        followup_number: Which followup this is (1st, 2nd, etc.)

    Returns:
        True if email sent successfully, False otherwise.
    """
    decrypt_customer_pii(customer)
    if not customer.email:
        logger.warning(f"Customer {customer.id} has no email - quote followup not sent")
        return False

    if not customer.notify_email:
        logger.info(f"Customer {customer.id} has email notifications disabled")
        return False

    total_formatted = f"${quote.total_cents / 100:,.2f}"
    subject = f"Following Up — Quote {quote.quote_number} from {settings.trading_as}"

    # Render HTML template
    try:
        html_content = templates.get_template("emails/quote_followup.html").render(
            customer=customer,
            quote=quote,
            portal_url=portal_url,
            total_formatted=total_formatted,
            followup_number=followup_number,
            business_name=settings.trading_as,
            business_abn=settings.abn,
            business_licence=settings.licence_number,
            business_address=settings.business_address,
            business_phone=settings.business_phone,
            business_email=settings.business_email,
        )
    except Exception as e:
        logger.error(f"Failed to render quote followup template: {str(e)}")
        return False

    # Plain text version
    text_content = f"""
Hi {customer.name},

We sent you a quote recently and wanted to check if you had any questions. We'd love to help you get your project underway!

Quote: {quote.quote_number}
{f"Project: {quote.job_name}" if quote.job_name else ""}
{f"Location: {quote.job_address}" if quote.job_address else ""}
Total: {total_formatted}

View your quote online:
{portal_url}

If the quote doesn't quite suit your needs, we're happy to adjust it. Give us a call on {settings.business_phone}.

Thanks,
{settings.trading_as}
""".strip()

    return await send_email(
        to=customer.email,
        subject=subject,
        html_body=html_content,
        text_body=text_content,
        db=db,
        quote_id=quote.id,
        customer_id=customer.id,
        template_name="quote_followup",
    )


# =============================================================================
# PROGRESS UPDATE EMAIL (async)
# =============================================================================

async def send_progress_update_email(
    db: AsyncSession,
    quote: Quote,
    customer: Customer,
    update_title: str,
    update_message: str,
    photos: list = None,
) -> bool:
    """
    Send progress update email with photos to customer (async).

    Args:
        db: Database session
        quote: The active job quote
        customer: The customer to update
        update_title: Title of the progress update
        update_message: Body message from the contractor
        photos: List of Photo objects with storage_url and optional caption

    Returns:
        True if email sent successfully, False otherwise.
    """
    decrypt_customer_pii(customer)
    if not customer.email:
        logger.warning(f"Customer {customer.id} has no email - progress update not sent")
        return False

    if not customer.notify_email:
        logger.info(f"Customer {customer.id} has email notifications disabled")
        return False

    subject = f"Progress Update: {update_title} — {settings.trading_as}"

    # Render HTML template
    try:
        html_content = templates.get_template("emails/progress_update.html").render(
            customer=customer,
            quote=quote,
            update_title=update_title,
            update_message=update_message,
            photos=photos or [],
            business_name=settings.trading_as,
            business_abn=settings.abn,
            business_licence=settings.licence_number,
            business_address=settings.business_address,
            business_phone=settings.business_phone,
            business_email=settings.business_email,
        )
    except Exception as e:
        logger.error(f"Failed to render progress update template: {str(e)}")
        return False

    # Plain text version
    photo_text = f"\n\n{len(photos)} photo(s) attached — view in your email client or browser." if photos else ""
    text_content = f"""
Hi {customer.name},

Here's an update on your concreting project:

{update_title}

{update_message}
{photo_text}

If you have any questions, give us a call on {settings.business_phone}. We're always happy to chat!

{settings.trading_as}
{settings.business_phone}
""".strip()

    return await send_email(
        to=customer.email,
        subject=subject,
        html_body=html_content,
        text_body=text_content,
        db=db,
        quote_id=quote.id,
        customer_id=customer.id,
        template_name="progress_update",
    )


# =============================================================================
# SYNCHRONOUS EMAIL HELPERS (for Celery tasks)
# =============================================================================

def send_review_request_email_sync(
    customer: Customer,
    quote: Quote,
) -> bool:
    """
    Send review request email (synchronous).

    Used by Celery tasks for post-job review requests.
    """
    decrypt_customer_pii(customer)
    if not customer.email:
        return False

    review_url = settings.google_review_url or "#"
    subject = f"How did we do? - {settings.trading_as}"

    # Render HTML template
    try:
        html_content = templates.get_template("emails/review_request.html").render(
            customer=customer,
            quote=quote,
            review_url=review_url,
            business_name=settings.trading_as,
            business_abn=settings.abn,
            business_licence=settings.licence_number,
            business_address=settings.business_address,
            business_phone=settings.business_phone,
            business_email=settings.business_email,
        )
    except Exception as e:
        logger.error(f"Failed to render review request template: {str(e)}")
        html_content = None

    # Plain text version
    text_content = f"""
Hi {customer.name},

Thank you for choosing {settings.trading_as} for your recent concreting project!

We hope you're happy with the finished result. If you have a moment, we'd really appreciate it if you could leave us a review. Your feedback helps other customers find quality tradespeople and helps us continue improving our service.

Leave a review:
{review_url}

It only takes a minute and would mean a lot to our team!

If you have any concerns or feedback you'd like to share privately, please don't hesitate to call us on {settings.business_phone}.

Thanks again for your business!

{settings.trading_as}
{settings.business_phone}
""".strip()

    if not html_content:
        html_content = f"<html><body><pre>{text_content}</pre></body></html>"

    return send_email_sync(
        to=customer.email,
        subject=subject,
        html_body=html_content,
        text_body=text_content,
    )


# =============================================================================
# QUOTE EXPIRY WARNING EMAIL (async)
# =============================================================================

async def send_quote_expiry_warning_email(
    db: AsyncSession,
    quote: Quote,
    customer: Customer,
    portal_url: str,
    days_remaining: int = 0,
) -> bool:
    """
    Send proactive quote expiry warning email to customer.

    Sent a few days before the quote expires to give the customer
    a chance to accept before pricing changes.

    Args:
        db: Database session
        quote: The expiring quote
        customer: The customer
        portal_url: Full URL to the quote portal
        days_remaining: Days until expiry (0 = expires today)

    Returns:
        True if email sent successfully, False otherwise.
    """
    decrypt_customer_pii(customer)
    if not customer.email:
        logger.warning(f"Customer {customer.id} has no email - expiry warning not sent")
        return False

    if not customer.notify_email:
        logger.info(f"Customer {customer.id} has email notifications disabled")
        return False

    total_formatted = f"${quote.total_cents / 100:,.2f}"
    expiry_date_formatted = quote.expiry_date.strftime("%d %B %Y") if quote.expiry_date else "soon"

    subject = f"Your Quote Expires Soon — {quote.quote_number}"

    # Render HTML template
    try:
        html_content = templates.get_template("emails/quote_expiry_warning.html").render(
            quote=quote,
            customer=customer,
            portal_url=portal_url,
            total_formatted=total_formatted,
            expiry_date_formatted=expiry_date_formatted,
            days_remaining=days_remaining,
            business_name=settings.trading_as,
            business_abn=settings.abn,
            business_licence=settings.licence_number,
            business_address=settings.business_address,
            business_phone=settings.business_phone,
            business_email=settings.business_email,
        )
    except Exception as e:
        logger.error(f"Failed to render quote expiry warning template: {str(e)}")
        return False

    # Plain text version
    text_content = f"""
Hi {customer.name},

Just a friendly heads-up — your quote from {settings.trading_as} is expiring on {expiry_date_formatted}{f' ({days_remaining} days from now)' if days_remaining > 0 else ''}.

Quote: {quote.quote_number}
{f"Project: {quote.job_name}" if quote.job_name else ""}
Total: {total_formatted}

After this date, pricing may change due to material and labour cost updates. Lock in your current price by accepting online:

{portal_url}

If the quote doesn't quite suit your needs, we're happy to adjust it. Give us a call on {settings.business_phone}.

Looking forward to getting your project underway!

{settings.trading_as}
{settings.business_phone}
""".strip()

    return await send_email(
        to=customer.email,
        subject=subject,
        html_body=html_content,
        text_body=text_content,
        db=db,
        quote_id=quote.id,
        customer_id=customer.id,
        template_name="quote_expiry_warning",
    )


# =============================================================================
# JOB COMPLETE EMAIL (async)
# =============================================================================

async def send_job_complete_email(
    db: AsyncSession,
    quote: Quote,
    customer: Customer,
    portal_url: str = "",
) -> bool:
    """
    Send job completion email to customer.

    Includes:
    - Job summary with payment status
    - What's next (curing, sealing advice)
    - Final payment link (if balance remaining)
    - Review request (if configured)

    Args:
        db: Database session
        quote: The completed job quote
        customer: The customer
        portal_url: URL to final invoice portal (optional)

    Returns:
        True if email sent successfully, False otherwise.
    """
    decrypt_customer_pii(customer)
    if not customer.email:
        logger.warning(f"Customer {customer.id} has no email - job complete email not sent")
        return False

    if not customer.notify_email:
        logger.info(f"Customer {customer.id} has email notifications disabled")
        return False

    total_formatted = f"${quote.total_cents / 100:,.2f}"
    paid_cents = quote.total_paid_cents or 0
    paid_formatted = f"${paid_cents / 100:,.2f}"
    balance_cents = (quote.total_cents or 0) - paid_cents
    balance_formatted = f"${balance_cents / 100:,.2f}"

    review_url = settings.google_review_url or ""

    subject = f"Your Job is Complete! — {settings.trading_as}"

    # Render HTML template
    try:
        html_content = templates.get_template("emails/job_complete.html").render(
            quote=quote,
            customer=customer,
            portal_url=portal_url,
            total_formatted=total_formatted,
            paid_formatted=paid_formatted,
            balance_cents=balance_cents,
            balance_formatted=balance_formatted,
            review_url=review_url,
            business_name=settings.trading_as,
            business_abn=settings.abn,
            business_licence=settings.licence_number,
            business_address=settings.business_address,
            business_phone=settings.business_phone,
            business_email=settings.business_email,
        )
    except Exception as e:
        logger.error(f"Failed to render job complete template: {str(e)}")
        return False

    # Plain text version
    balance_text = f"\nFinal Payment: {balance_formatted} remaining — pay online: {portal_url}" if balance_cents > 0 else ""
    review_text = f"\n\nHappy with the result? Leave us a Google review: {review_url}" if review_url and review_url != "#" else ""

    text_content = f"""
Hi {customer.name},

Great news — your concreting project is now complete! We hope you're thrilled with the result.

Job Summary:
Quote: {quote.quote_number}
{f"Project: {quote.job_name}" if quote.job_name else ""}
{f"Location: {quote.job_address}" if quote.job_address else ""}
Total: {total_formatted}
Paid: {paid_formatted}{balance_text}

What's Next:
- Curing: Keep the concrete moist for at least 7 days. Avoid heavy loads for 28 days.
- Sealing: We recommend sealing your concrete 28 days after the pour.{review_text}

Thank you for choosing {settings.trading_as}!

{settings.trading_as}
{settings.business_phone}
""".strip()

    return await send_email(
        to=customer.email,
        subject=subject,
        html_body=html_content,
        text_body=text_content,
        db=db,
        quote_id=quote.id,
        customer_id=customer.id,
        template_name="job_complete",
    )


# =============================================================================
# BOOKING CONFIRMED EMAIL (async)
# =============================================================================

async def send_booking_confirmed_email(
    db: AsyncSession,
    quote: Quote,
    customer: Customer,
    start_date,
    invoice_url: str = "",
) -> bool:
    """
    Send rich booking confirmation email to customer.

    Includes:
    - Start date and job details
    - Payment schedule with paid/outstanding status
    - Invoice link
    - "Before We Arrive" checklist

    Args:
        db: Database session
        quote: The confirmed quote
        customer: The customer
        start_date: The confirmed start date
        invoice_url: URL to the first payment invoice (optional)

    Returns:
        True if email sent successfully, False otherwise.
    """
    decrypt_customer_pii(customer)
    if not customer.email:
        logger.warning(f"Customer {customer.id} has no email - booking confirmation not sent")
        return False

    if not customer.notify_email:
        logger.info(f"Customer {customer.id} has email notifications disabled")
        return False

    total_formatted = f"${quote.total_cents / 100:,.2f}"
    start_date_formatted = start_date.strftime("%A, %d %B %Y") if start_date else "To be confirmed"

    # Get payment schedule from calculator result
    payments = (quote.calculator_result or {}).get("payments", [])
    if not payments:
        # Fallback: generate standard 30/60/10 payment info
        total = quote.total_cents or 0
        deposit_amount = int(round(total * 0.30))
        progress_amount = int(round(total * 0.60))
        final_amount = total - deposit_amount - progress_amount
        payments = [
            {"name": "First Payment (30%)", "amount_cents": deposit_amount, "percent": 0.30},
            {"name": "Progress Payment (60%)", "amount_cents": progress_amount, "percent": 0.60},
            {"name": "Final Payment (10%)", "amount_cents": final_amount, "percent": 0.10},
        ]

    # Check if first payment is already paid
    first_payment_paid = (quote.total_paid_cents or 0) >= (payments[0]["amount_cents"] if payments else 0)

    subject = f"Booking Confirmed — {quote.quote_number} | {settings.trading_as}"

    # Render HTML template
    try:
        html_content = templates.get_template("emails/booking_confirmed.html").render(
            quote=quote,
            customer=customer,
            payments=payments,
            first_payment_paid=first_payment_paid,
            start_date_formatted=start_date_formatted,
            total_formatted=total_formatted,
            invoice_url=invoice_url,
            business_name=settings.trading_as,
            business_abn=settings.abn,
            business_licence=settings.licence_number,
            business_address=settings.business_address,
            business_phone=settings.business_phone,
            business_email=settings.business_email,
        )
    except Exception as e:
        logger.error(f"Failed to render booking confirmed template: {str(e)}")
        return False

    # Plain text version
    payment_text = "\n".join(
        f"  - {p['name']}: ${p['amount_cents'] / 100:,.2f}" for p in payments
    )

    text_content = f"""
Hi {customer.name},

Your job is confirmed with {settings.trading_as}!

Booking Details:
Start Date: {start_date_formatted}
{f"Project: {quote.job_name}" if quote.job_name else ""}
{f"Location: {quote.job_address}" if quote.job_address else ""}
Quote Total: {total_formatted}

Payment Schedule:
{payment_text}

Before We Arrive:
- Ensure clear access to the work area for trucks and equipment
- Move vehicles, furniture, or items from the work zone
- Keep pets and children away from the work area on pour day
- Let your neighbours know about the work

If you need to reschedule, please call {settings.business_phone} as soon as possible.

We're looking forward to getting started!

{settings.trading_as}
{settings.business_phone}
""".strip()

    return await send_email(
        to=customer.email,
        subject=subject,
        html_body=html_content,
        text_body=text_content,
        db=db,
        quote_id=quote.id,
        customer_id=customer.id,
        template_name="booking_confirmed",
    )
