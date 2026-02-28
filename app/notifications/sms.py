"""
SMS Service — Vonage integration for sending SMS notifications.

SMS fail gracefully - log errors but don't crash the app.
"""

import httpx
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Quote, Invoice, Customer, CommunicationLog
from app.settings import service as settings_service
from app.core.dates import sydney_now
from app.core.security import decrypt_customer_pii

logger = logging.getLogger(__name__)


# =============================================================================
# VONAGE SMS API
# =============================================================================

VONAGE_API_URL = "https://rest.nexmo.com/sms/json"


async def send_sms(
    db: AsyncSession,
    to: str,
    message: str,
    quote_id: Optional[int] = None,
    invoice_id: Optional[int] = None,
    customer_id: Optional[int] = None,
) -> dict:
    """
    Send SMS via Vonage.

    Args:
        db: Database session
        to: Phone number (Australian format)
        message: SMS message content (max 160 chars for single SMS)
        quote_id: Link to quote for logging
        invoice_id: Link to invoice for logging

    Returns:
        dict with 'success' (bool) and 'error' or 'message_id'
    """
    sms_settings = await settings_service.get_settings_by_category(db, 'sms')

    if not sms_settings.get('enabled'):
        logger.info("SMS disabled - message not sent")
        return {"success": False, "error": "SMS disabled"}

    api_key = sms_settings.get('vonage_api_key')
    api_secret = sms_settings.get('vonage_api_secret')
    from_number = sms_settings.get('vonage_from_number')

    if not all([api_key, api_secret, from_number]):
        logger.warning("SMS not configured - missing API credentials")
        return {"success": False, "error": "SMS not configured"}

    # Normalize phone number (remove spaces, +, leading 0)
    phone = _normalize_phone(to)
    if not phone:
        logger.warning(f"Invalid phone number: {to}")
        return {"success": False, "error": "Invalid phone number"}

    payload = {
        "api_key": api_key,
        "api_secret": api_secret,
        "from": from_number,
        "to": phone,
        "text": message,
    }

    message_id = None
    success = False
    error_text = None

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                VONAGE_API_URL,
                data=payload,
                timeout=30.0,
            )

            result = response.json()
            messages = result.get("messages", [{}])

            if messages and messages[0].get("status") == "0":
                message_id = messages[0].get("message-id")
                success = True
                logger.info(f"SMS sent to {phone}: {message[:50]}... (ID: {message_id})")
            else:
                error_text = messages[0].get("error-text", "Unknown error") if messages else "No response"
                logger.error(f"Vonage API error: {error_text}")

    except httpx.TimeoutException:
        error_text = "API timeout"
        logger.error(f"Vonage API timeout sending to {phone}")
    except Exception as e:
        error_text = str(e)
        logger.error(f"SMS send error: {error_text}")

    # Log to unified communication log
    try:
        comm_log = CommunicationLog(
            channel="sms",
            direction="outbound",
            customer_id=customer_id,
            quote_id=quote_id,
            invoice_id=invoice_id,
            to_phone=phone,
            body=message[:500],  # Truncate for storage
            provider_message_id=message_id,
            status="sent" if success else "failed",
            sent_at=sydney_now() if success else None,
        )
        db.add(comm_log)
    except Exception as e:
        logger.error(f"Failed to log SMS: {str(e)}")

    if success:
        return {"success": True, "message_id": message_id}
    return {"success": False, "error": error_text or "Unknown error"}


async def send_test_sms(db: AsyncSession, to: str) -> dict:
    """Send a test SMS to verify configuration."""
    message = "Test SMS from ConcreteIQ. If you received this, SMS is configured correctly!"
    return await send_sms(db, to, message)


# =============================================================================
# QUOTE SMS
# =============================================================================

async def send_quote_sms(
    db: AsyncSession,
    quote: Quote,
    customer: Customer,
    portal_url: str,
) -> dict:
    """
    Send quote notification SMS.

    Args:
        db: Database session
        quote: The quote being sent
        customer: The customer receiving the SMS
        portal_url: Short URL to the quote portal

    Returns:
        dict with 'success' and error/message_id
    """
    decrypt_customer_pii(customer)
    if not customer.phone:
        return {"success": False, "error": "No phone number"}

    if not customer.notify_sms:
        logger.info(f"Customer {customer.id} has SMS notifications disabled")
        return {"success": False, "error": "SMS notifications disabled"}

    # Get business name from settings
    business = await settings_service.get_settings_by_category(db, 'business')
    trading_as = business.get('trading_as', 'KRG Concreting')

    # Build message (keep under 160 chars if possible)
    first_name = customer.name.split()[0] if customer.name else "there"
    total = f"${quote.total_cents / 100:,.0f}"

    message = f"Hi {first_name}, your quote {quote.quote_number} ({total}) from {trading_as} is ready. View it here: {portal_url}"

    # Truncate if too long
    if len(message) > 160:
        message = f"Hi {first_name}, quote {quote.quote_number} from {trading_as} is ready: {portal_url}"

    return await send_sms(db, customer.phone, message, quote_id=quote.id, customer_id=customer.id)


# =============================================================================
# AMENDMENT SMS
# =============================================================================

async def send_amendment_sms(
    db: AsyncSession,
    amendment,
    quote: Quote,
    customer: Customer,
    portal_url: str,
) -> dict:
    """
    Send amendment/variation notification SMS.

    Args:
        db: Database session
        amendment: The QuoteAmendment being sent
        quote: The parent quote
        customer: The customer receiving the SMS
        portal_url: URL to the amendment portal page

    Returns:
        dict with 'success' and error/message_id
    """
    decrypt_customer_pii(customer)
    if not customer.phone:
        return {"success": False, "error": "No phone number"}

    if not customer.notify_sms:
        logger.info(f"Customer {customer.id} has SMS notifications disabled")
        return {"success": False, "error": "SMS notifications disabled"}

    # Get business name from settings
    business = await settings_service.get_settings_by_category(db, 'business')
    trading_as = business.get('trading_as', 'KRG Concreting')

    first_name = customer.name.split()[0] if customer.name else "there"
    amount = amendment.amount_cents or 0
    sign = "+" if amount >= 0 else "-"
    amount_str = f"{sign}${abs(amount) / 100:,.0f}"

    message = f"Hi {first_name}, a variation ({amount_str}) for your quote {quote.quote_number} needs your review. View here: {portal_url}"

    if len(message) > 160:
        message = f"Hi {first_name}, variation on {quote.quote_number} needs review: {portal_url}"

    return await send_sms(db, customer.phone, message, quote_id=quote.id, customer_id=customer.id)


# =============================================================================
# INVOICE SMS
# =============================================================================

async def send_invoice_sms(
    db: AsyncSession,
    invoice: Invoice,
    customer: Customer,
    portal_url: str,
) -> dict:
    """
    Send invoice notification SMS.

    Args:
        db: Database session
        invoice: The invoice being sent
        customer: The customer receiving the SMS
        portal_url: Short URL to the invoice portal

    Returns:
        dict with 'success' and error/message_id
    """
    decrypt_customer_pii(customer)
    if not customer.phone:
        return {"success": False, "error": "No phone number"}

    if not customer.notify_sms:
        logger.info(f"Customer {customer.id} has SMS notifications disabled")
        return {"success": False, "error": "SMS notifications disabled"}

    # Get business name from settings
    business = await settings_service.get_settings_by_category(db, 'business')
    trading_as = business.get('trading_as', 'KRG Concreting')

    first_name = customer.name.split()[0] if customer.name else "there"
    total = f"${invoice.total_cents / 100:,.2f}"

    message = f"Hi {first_name}, invoice {invoice.invoice_number} for {total} from {trading_as} is ready. Pay here: {portal_url}"

    return await send_sms(db, customer.phone, message, invoice_id=invoice.id, customer_id=customer.id)


# =============================================================================
# PAYMENT REMINDER SMS
# =============================================================================

async def send_payment_reminder_sms(
    db: AsyncSession,
    invoice: Invoice,
    customer: Customer,
    days_overdue: int = 0,
) -> dict:
    """
    Send payment reminder SMS.

    Args:
        db: Database session
        invoice: The invoice needing payment
        customer: The customer to remind
        days_overdue: Number of days past due (0 = upcoming reminder)

    Returns:
        dict with 'success' and error/message_id
    """
    decrypt_customer_pii(customer)
    if not customer.phone:
        return {"success": False, "error": "No phone number"}

    if not customer.notify_sms:
        return {"success": False, "error": "SMS notifications disabled"}

    first_name = customer.name.split()[0] if customer.name else "there"
    total = f"${invoice.total_cents / 100:,.2f}"

    if days_overdue > 0:
        message = f"Hi {first_name}, invoice {invoice.invoice_number} for {total} is {days_overdue} days overdue. Please pay ASAP to avoid late fees."
    else:
        message = f"Hi {first_name}, friendly reminder: invoice {invoice.invoice_number} for {total} is due soon."

    return await send_sms(db, customer.phone, message, invoice_id=invoice.id, customer_id=customer.id)


# =============================================================================
# JOB REMINDER SMS
# =============================================================================

async def send_job_reminder_sms(
    db: AsyncSession,
    quote: Quote,
    customer: Customer,
) -> dict:
    """
    Send job reminder SMS.

    Args:
        db: Database session
        quote: The quote/job to remind about
        customer: The customer to remind

    Returns:
        dict with 'success' and error/message_id
    """
    decrypt_customer_pii(customer)
    if not customer.phone:
        return {"success": False, "error": "No phone number"}

    if not customer.notify_sms:
        return {"success": False, "error": "SMS notifications disabled"}

    # Get business name from settings
    business = await settings_service.get_settings_by_category(db, 'business')
    trading_as = business.get('trading_as', 'KRG Concreting')

    first_name = customer.name.split()[0] if customer.name else "there"

    # Format date
    if quote.confirmed_start_date:
        date_str = quote.confirmed_start_date.strftime('%A, %d %B')
    else:
        date_str = "soon"

    # Build message
    address = quote.job_address or "your property"
    if len(address) > 30:
        address = "your property"

    message = f"Hi {first_name}, reminder: your concrete job at {address} is scheduled for {date_str}. See you then! - {trading_as}"

    # Truncate if needed
    if len(message) > 160:
        message = f"Hi {first_name}, job reminder: {date_str}. See you then! - {trading_as}"

    return await send_sms(db, customer.phone, message, quote_id=quote.id, customer_id=customer.id)


# =============================================================================
# REVIEW REQUEST SMS
# =============================================================================

async def send_review_request_sms(
    db: AsyncSession,
    quote: Quote,
    customer: Customer,
) -> dict:
    """
    Send review request SMS to customer.

    Args:
        db: Database session
        quote: The completed quote/job
        customer: The customer to request a review from

    Returns:
        dict with 'success' and error/message_id
    """
    from app.config import settings as app_settings

    decrypt_customer_pii(customer)
    if not customer.phone:
        return {"success": False, "error": "No phone number"}

    if not customer.notify_sms:
        return {"success": False, "error": "SMS notifications disabled"}

    # Get business name from settings
    business = await settings_service.get_settings_by_category(db, 'business')
    trading_as = business.get('trading_as', 'KRG Concreting')

    first_name = customer.name.split()[0] if customer.name else "there"
    review_url = app_settings.google_review_url or ""

    if review_url:
        message = f"Hi {first_name}, thanks for choosing {trading_as}! We'd love a Google review: {review_url}"
    else:
        message = f"Hi {first_name}, thanks for choosing {trading_as}! If you have a moment, we'd appreciate a Google review."

    if len(message) > 160:
        message = f"Hi {first_name}, thanks for choosing {trading_as}! Leave a review: {review_url}"

    return await send_sms(db, customer.phone, message, quote_id=quote.id, customer_id=customer.id)


# =============================================================================
# QUOTE FOLLOWUP SMS
# =============================================================================

async def send_quote_followup_sms(
    db: AsyncSession,
    quote: Quote,
    customer: Customer,
    portal_url: str,
) -> dict:
    """
    Send quote followup SMS to customer.

    Args:
        db: Database session
        quote: The quote to follow up on
        customer: The customer who received the quote
        portal_url: URL to the quote portal

    Returns:
        dict with 'success' and error/message_id
    """
    decrypt_customer_pii(customer)
    if not customer.phone:
        return {"success": False, "error": "No phone number"}

    if not customer.notify_sms:
        return {"success": False, "error": "SMS notifications disabled"}

    # Get business name from settings
    business = await settings_service.get_settings_by_category(db, 'business')
    trading_as = business.get('trading_as', 'KRG Concreting')

    first_name = customer.name.split()[0] if customer.name else "there"

    message = f"Hi {first_name}, just following up on your quote {quote.quote_number} from {trading_as}. View it here: {portal_url}"

    if len(message) > 160:
        message = f"Hi {first_name}, following up on quote {quote.quote_number}: {portal_url}"

    return await send_sms(db, customer.phone, message, quote_id=quote.id, customer_id=customer.id)


# =============================================================================
# PROGRESS UPDATE SMS
# =============================================================================

async def send_progress_update_sms(
    db: AsyncSession,
    quote: Quote,
    customer: Customer,
) -> dict:
    """
    Send progress update notification SMS. Photos are in the email.

    Args:
        db: Database session
        quote: The active job quote
        customer: The customer to notify

    Returns:
        dict with 'success' and error/message_id
    """
    decrypt_customer_pii(customer)
    if not customer.phone:
        return {"success": False, "error": "No phone number"}

    if not customer.notify_sms:
        return {"success": False, "error": "SMS notifications disabled"}

    # Get business name from settings
    business = await settings_service.get_settings_by_category(db, 'business')
    trading_as = business.get('trading_as', 'KRG Concreting')

    first_name = customer.name.split()[0] if customer.name else "there"

    message = f"Hi {first_name}, we've sent you a progress update on your job! Check your email for photos. - {trading_as}"

    if len(message) > 160:
        message = f"Hi {first_name}, progress update sent to your email with photos! - {trading_as}"

    return await send_sms(db, customer.phone, message, quote_id=quote.id, customer_id=customer.id)


# =============================================================================
# ON MY WAY SMS
# =============================================================================

async def send_on_my_way_sms(
    db: AsyncSession,
    quote: Quote,
    customer: Customer,
    eta_minutes: Optional[int] = None,
) -> dict:
    """
    Send 'On My Way' SMS to customer.

    Uses configurable templates from settings (sms_templates category).
    Falls back to hardcoded defaults if no custom template is set.

    Args:
        db: Database session
        quote: The job quote
        customer: The customer to notify
        eta_minutes: Optional estimated arrival time in minutes

    Returns:
        dict with 'success' and error/message_id
    """
    decrypt_customer_pii(customer)
    if not customer.phone:
        return {"success": False, "error": "No phone number"}

    if not customer.notify_sms:
        return {"success": False, "error": "SMS notifications disabled"}

    # Get business name and SMS templates from settings
    business = await settings_service.get_settings_by_category(db, 'business')
    sms_templates = await settings_service.get_settings_by_category(db, 'sms_templates')
    trading_as = business.get('trading_as', 'KRG Concreting')

    first_name = customer.name.split()[0] if customer.name else "there"
    address = quote.job_address or "your property"

    # Build message from template or use default
    if eta_minutes:
        template = (sms_templates.get('on_my_way_eta') or '').strip()
        if not template:
            template = "Hi {first_name}, we're heading to {address} now! ETA roughly {eta} minutes. See you soon! - {business_name}"
    else:
        template = (sms_templates.get('on_my_way') or '').strip()
        if not template:
            template = "Hi {first_name}, we're heading to {address} now! See you soon! - {business_name}"

    message = _render_sms_template(template, {
        "first_name": first_name,
        "customer_name": customer.name or "there",
        "address": address,
        "eta": str(eta_minutes) if eta_minutes else "",
        "business_name": trading_as,
        "job_date": quote.confirmed_start_date.strftime('%A, %d %B') if quote.confirmed_start_date else "TBC",
        "quote_number": quote.quote_number or str(quote.id),
    })

    return await send_sms(db, customer.phone, message, quote_id=quote.id, customer_id=customer.id)


# =============================================================================
# DAY BEFORE REMINDER SMS
# =============================================================================

async def send_day_before_sms(
    db: AsyncSession,
    quote: Quote,
    customer: Customer,
) -> dict:
    """
    Send day-before reminder SMS to customer.

    Uses configurable templates from settings (sms_templates category).
    Falls back to hardcoded defaults if no custom template is set.

    Args:
        db: Database session
        quote: The job quote scheduled for tomorrow
        customer: The customer to remind

    Returns:
        dict with 'success' and error/message_id
    """
    decrypt_customer_pii(customer)
    if not customer.phone:
        return {"success": False, "error": "No phone number"}

    if not customer.notify_sms:
        return {"success": False, "error": "SMS notifications disabled"}

    # Get business name and SMS templates from settings
    business = await settings_service.get_settings_by_category(db, 'business')
    sms_templates = await settings_service.get_settings_by_category(db, 'sms_templates')
    trading_as = business.get('trading_as', 'KRG Concreting')

    first_name = customer.name.split()[0] if customer.name else "there"
    address = quote.job_address or "your property"
    if len(address) > 30:
        address = "your property"

    template = (sms_templates.get('day_before_reminder') or '').strip()
    if not template:
        template = "Hi {first_name}, quick reminder: we'll be at {address} tomorrow for your concrete job. If there's anything you need to prepare, please let us know! - {business_name}"

    message = _render_sms_template(template, {
        "first_name": first_name,
        "customer_name": customer.name or "there",
        "address": address,
        "eta": "",
        "business_name": trading_as,
        "job_date": quote.confirmed_start_date.strftime('%A, %d %B') if quote.confirmed_start_date else "tomorrow",
        "quote_number": quote.quote_number or str(quote.id),
    })

    return await send_sms(db, customer.phone, message, quote_id=quote.id, customer_id=customer.id)


# =============================================================================
# TEMPLATE HELPERS
# =============================================================================

def _render_sms_template(template: str, variables: dict) -> str:
    """
    Render an SMS template by replacing {variable} placeholders.

    Args:
        template: Template string with {variable} placeholders
        variables: Dict of variable name -> value

    Returns:
        Rendered message string
    """
    message = template
    for key, value in variables.items():
        message = message.replace(f"{{{key}}}", str(value))
    return message


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _normalize_phone(phone: str) -> Optional[str]:
    """
    Normalize phone number to international format.

    Converts Australian numbers to 61XXXXXXXXX format.
    Returns None if invalid.
    """
    if not phone:
        return None

    # Remove all non-digit characters
    digits = ''.join(c for c in phone if c.isdigit())

    if not digits:
        return None

    # Handle Australian numbers
    if digits.startswith('61'):
        # Already international format
        if len(digits) == 11:
            return digits
    elif digits.startswith('0'):
        # Local format - convert to international
        digits = '61' + digits[1:]
        if len(digits) == 11:
            return digits
    elif digits.startswith('4'):
        # Mobile without leading 0
        digits = '61' + digits
        if len(digits) == 11:
            return digits

    # Return as-is if we couldn't normalize (might be international)
    if len(digits) >= 10:
        return digits

    return None
