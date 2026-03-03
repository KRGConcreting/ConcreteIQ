"""
Webhook endpoints - Stripe, Resend, Vonage.

CRITICAL SECURITY:
1. Verify webhook signatures / auth tokens before processing
2. Check idempotency BEFORE processing
3. Record event BEFORE processing
4. Return 200 even on processing errors (prevents retries)
5. Enforce payload size limits (1 MB max)

Authentication:
- Stripe: verified by stripe-signature header (HMAC)
- Resend/ClickSend/Vonage: verified by WEBHOOK_SECRET bearer token or query param
"""

import hmac
import logging
from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import CommunicationLog, Customer, EmailLog, SMSLog
from app.payments import service as payment_service
from app.core.dates import sydney_now

logger = logging.getLogger(__name__)

router = APIRouter()

# Maximum webhook payload size: 1 MB
MAX_PAYLOAD_BYTES = 1_048_576


def _verify_webhook_token(request: Request) -> bool:
    """
    Verify webhook authentication via Bearer token or ?token= query param.

    Returns True if WEBHOOK_SECRET is not configured (fail-open for dev),
    or if the provided token matches.
    """
    secret = settings.webhook_secret
    if not secret:
        # Not configured — warn but allow (backward-compatible for dev)
        logger.warning("WEBHOOK_SECRET not set — webhook authentication disabled")
        return True

    # Check Authorization header first
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        return hmac.compare_digest(token, secret)

    # Fallback: check query parameter ?token=
    token_param = request.query_params.get("token", "")
    if token_param:
        return hmac.compare_digest(token_param, secret)

    return False


async def _read_body_limited(request: Request, max_bytes: int = MAX_PAYLOAD_BYTES) -> bytes:
    """Read request body with size enforcement."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > max_bytes:
        raise HTTPException(413, "Payload too large")

    body = await request.body()
    if len(body) > max_bytes:
        raise HTTPException(413, "Payload too large")
    return body


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Handle Stripe webhooks.

    Security flow:
    1. Verify signature with stripe_webhook_secret
    2. Check idempotency via WebhookEvent table
    3. Record event BEFORE processing (prevents race conditions)
    4. Process event
    5. Always return 200 (prevents Stripe retries on our errors)

    Event types handled:
    - checkout.session.completed: Creates Payment record, updates Invoice
    """
    # Enforce payload size limit
    payload = await _read_body_limited(request)
    signature = request.headers.get("stripe-signature")

    if not signature:
        # Return 200 with error — prevents Stripe retries entirely
        logger.warning("Stripe webhook received without signature")
        return {"received": True, "error": "Missing Stripe signature"}

    # Verify signature — return 200 with error to prevent retries
    try:
        event = await payment_service.verify_webhook_signature(payload, signature, db=db)
    except Exception as e:
        logger.warning(f"Stripe webhook signature verification failed: {e}")
        return {"received": True, "error": "Invalid webhook signature"}

    # Process event with idempotency
    try:
        result = await payment_service.process_stripe_webhook(db, event)
        await db.commit()
        return {"received": True, **result}
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        await db.rollback()
        return {"received": True, "error": "Processing error"}


# =============================================================================
# RESEND EMAIL TRACKING
# =============================================================================

@router.post("/resend")
async def resend_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Handle Resend email tracking webhooks (via Svix).

    Authentication: Bearer token via WEBHOOK_SECRET.
    Configure in Resend dashboard → Webhooks:
      URL: https://app.krgconcreting.au/webhooks/resend?token=YOUR_SECRET

    Resend event types:
    - email.delivered: Email delivered
    - email.opened: Email opened
    - email.clicked: Link clicked
    - email.bounced: Email bounced
    - email.complained: Marked as spam

    Resend sends JSON with a "type" field and "data.email_id" for tracking.
    Updates both CommunicationLog and legacy EmailLog tables.
    """
    # Authenticate webhook
    if not _verify_webhook_token(request):
        logger.warning(f"Resend webhook auth failed from {request.client.host if request.client else 'unknown'}")
        raise HTTPException(403, "Invalid webhook token")

    # Enforce payload size limit
    await _read_body_limited(request)

    try:
        payload = await request.json()
    except Exception:
        return {"received": True, "error": "Invalid JSON"}

    event_type = payload.get("type", "")
    data = payload.get("data", {})
    message_id = data.get("email_id")

    if not message_id:
        return {"received": True, "error": "No email_id"}

    # Map Resend event types to our status
    event_map = {
        "email.delivered": "delivered",
        "email.opened": "opened",
        "email.clicked": "clicked",
        "email.bounced": "bounced",
        "email.complained": "spam",
    }
    status = event_map.get(event_type)
    if not status:
        return {"received": True, "event_type": event_type, "skipped": True}

    now = sydney_now()
    updated = 0

    # Update CommunicationLog
    result = await db.execute(
        select(CommunicationLog).where(
            CommunicationLog.provider_message_id == message_id
        )
    )
    comm_log = result.scalar_one_or_none()

    if comm_log:
        comm_log.status = status
        if status == "delivered":
            comm_log.delivered_at = now
        elif status == "opened":
            comm_log.opened_at = now
        elif status == "clicked":
            comm_log.clicked_at = now
        updated += 1

    # Update legacy EmailLog
    email_result = await db.execute(
        select(EmailLog).where(
            EmailLog.postmark_message_id == message_id
        )
    )
    email_log = email_result.scalar_one_or_none()

    if email_log:
        email_log.status = status
        if status == "delivered":
            email_log.delivered_at = now
        elif status == "opened":
            email_log.opened_at = now
        elif status == "clicked":
            email_log.clicked_at = now
        updated += 1

    # Create in-app notifications for engagement events
    if comm_log and status in ("opened", "clicked", "bounced", "spam"):
        try:
            customer_name = "Customer"
            if comm_log.customer_id:
                customer = await db.get(Customer, comm_log.customer_id)
                if customer:
                    customer_name = customer.name
            subject = comm_log.subject or "email"

            from app.notifications.service import (
                notify_email_opened, notify_email_clicked, notify_email_bounced,
            )

            if status == "opened":
                await notify_email_opened(
                    db, customer_name, subject,
                    customer_id=comm_log.customer_id,
                    quote_id=comm_log.quote_id,
                    invoice_id=comm_log.invoice_id,
                )
            elif status == "clicked":
                await notify_email_clicked(
                    db, customer_name, subject,
                    customer_id=comm_log.customer_id,
                    quote_id=comm_log.quote_id,
                    invoice_id=comm_log.invoice_id,
                )
            elif status in ("bounced", "spam"):
                await notify_email_bounced(
                    db, customer_name, subject,
                    customer_id=comm_log.customer_id,
                    quote_id=comm_log.quote_id,
                    invoice_id=comm_log.invoice_id,
                )
        except Exception as e:
            logger.error(f"Failed to create notification for {event_type}: {e}")

    if updated > 0:
        await db.commit()
        logger.info(f"Resend webhook: {event_type} for email {message_id}")
    else:
        logger.debug(f"Resend webhook: no matching log for email {message_id}")

    return {"received": True, "event_type": event_type, "updated": updated}


# =============================================================================
# SMS DELIVERY RECEIPTS (VONAGE)
# =============================================================================

@router.post("/sms")
async def sms_delivery_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Handle SMS delivery receipt webhooks (Vonage DLR).

    Authentication: Bearer token via WEBHOOK_SECRET.
    Configure in Vonage dashboard (Settings → Default SMS Webhook):
      URL: https://app.krgconcreting.au/webhooks/sms?token=YOUR_SECRET

    Vonage sends delivery receipts with:
    - messageId: The provider message ID
    - status: delivered, expired, failed, rejected, accepted, buffered, unknown
    - err-code: Error code (0 = delivered)

    ClickSend sends:
    - message_id: The provider message ID
    - status: Delivered, Undelivered
    """
    # Authenticate webhook
    if not _verify_webhook_token(request):
        logger.warning(f"SMS delivery webhook auth failed from {request.client.host if request.client else 'unknown'}")
        raise HTTPException(403, "Invalid webhook token")

    # Enforce payload size limit
    await _read_body_limited(request)

    try:
        payload = await request.json()
    except Exception:
        # Vonage sometimes sends form-encoded
        try:
            form = await request.form()
            payload = dict(form)
        except Exception:
            return {"received": True, "error": "Invalid payload"}

    # Normalise across Vonage and ClickSend payload formats
    message_id = payload.get("messageId") or payload.get("message_id") or payload.get("MessageID")
    status_raw = (payload.get("status") or payload.get("Status") or "").lower()

    if not message_id:
        return {"received": True, "error": "No message ID"}

    # Map provider status to our status
    if status_raw in ("delivered",):
        new_status = "delivered"
    elif status_raw in ("failed", "rejected", "undelivered", "expired"):
        new_status = "failed"
    elif status_raw in ("accepted", "buffered", "submitted"):
        new_status = "accepted"
    else:
        new_status = status_raw or "unknown"

    now = sydney_now()
    updated = 0

    # Update CommunicationLog
    result = await db.execute(
        select(CommunicationLog).where(
            CommunicationLog.provider_message_id == message_id,
            CommunicationLog.channel == "sms",
        )
    )
    comm_log = result.scalar_one_or_none()

    if comm_log:
        comm_log.status = new_status
        if new_status == "delivered":
            comm_log.delivered_at = now
        updated += 1

    # Update legacy SMSLog
    sms_result = await db.execute(
        select(SMSLog).where(
            SMSLog.provider_message_id == message_id
        )
    )
    sms_log = sms_result.scalar_one_or_none()

    if sms_log:
        sms_log.status = new_status
        updated += 1

    if updated > 0:
        await db.commit()
        logger.info(f"SMS webhook: {new_status} for message {message_id}")
    else:
        logger.debug(f"SMS webhook: no matching log for message {message_id}")

    return {"received": True, "status": new_status, "updated": updated}


# =============================================================================
# VONAGE INBOUND SMS
# =============================================================================

@router.post("/vonage/inbound")
async def vonage_inbound_sms(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Handle inbound SMS from Vonage.

    Authentication: Bearer token via WEBHOOK_SECRET.
    Configure in Vonage dashboard:
      URL: https://app.krgconcreting.au/webhooks/vonage/inbound?token=YOUR_SECRET

    Vonage sends inbound messages with:
    - msisdn: Sender phone number
    - to: Your virtual number
    - messageId: Vonage message ID
    - text: Message content
    - type: text, unicode, binary
    - keyword: First word of message (uppercase)

    Tries to match the sender to an existing customer by phone number.
    Creates a CommunicationLog entry with direction='inbound'.
    """
    # Authenticate webhook
    if not _verify_webhook_token(request):
        logger.warning(f"Vonage inbound webhook auth failed from {request.client.host if request.client else 'unknown'}")
        raise HTTPException(403, "Invalid webhook token")

    # Enforce payload size limit
    await _read_body_limited(request)

    try:
        # Vonage can send JSON or form-encoded
        content_type = request.headers.get("content-type", "")
        if "json" in content_type:
            payload = await request.json()
        else:
            form = await request.form()
            payload = dict(form)
    except Exception:
        return {"received": True, "error": "Invalid payload"}

    sender_phone = payload.get("msisdn") or payload.get("from") or ""
    message_text = payload.get("text") or payload.get("body") or ""
    message_id = payload.get("messageId") or payload.get("message-id") or ""

    if not sender_phone or not message_text:
        return {"received": True, "error": "Missing sender or text"}

    now = sydney_now()

    # Try to match sender to a customer
    customer_id = None
    from app.notifications.sms import _normalize_phone
    normalized = _normalize_phone(sender_phone)

    if normalized:
        # Search by normalized phone
        result = await db.execute(
            select(Customer).where(
                Customer.phone.contains(sender_phone[-9:])  # Last 9 digits
            ).limit(1)
        )
        customer = result.scalar_one_or_none()
        if customer:
            customer_id = customer.id

    # Find most recent outbound SMS to this phone to link to quote
    quote_id = None
    if normalized:
        recent_outbound = await db.execute(
            select(CommunicationLog)
            .where(CommunicationLog.channel == "sms")
            .where(CommunicationLog.direction == "outbound")
            .where(CommunicationLog.to_phone == normalized)
            .order_by(CommunicationLog.created_at.desc())
            .limit(1)
        )
        recent = recent_outbound.scalar_one_or_none()
        if recent:
            quote_id = recent.quote_id
            if not customer_id:
                customer_id = recent.customer_id

    # Create inbound communication log
    comm_log = CommunicationLog(
        channel="sms",
        direction="inbound",
        customer_id=customer_id,
        quote_id=quote_id,
        from_phone=normalized or sender_phone,
        body=message_text[:2000],
        provider_message_id=message_id,
        status="received",
        sent_at=now,
        created_at=now,
    )
    db.add(comm_log)

    # Create notification for Kyle
    try:
        from app.models import Notification
        customer_name = "Unknown"
        if customer_id:
            cust = await db.get(Customer, customer_id)
            if cust:
                customer_name = cust.name

        notification = Notification(
            type="inbound_sms",
            title=f"SMS from {customer_name}",
            message=message_text[:100],
            customer_id=customer_id,
            quote_id=quote_id,
            priority="high",
            created_at=now,
        )
        db.add(notification)
    except Exception as e:
        logger.error(f"Failed to create inbound SMS notification: {e}")

    await db.commit()

    logger.info(f"Inbound SMS from {sender_phone}: {message_text[:50]}...")
    return {"received": True, "customer_matched": customer_id is not None}
