"""
Payment service - Stripe integration.

CRITICAL: Webhook idempotency via WebhookEvent table.
- Check FIRST: Query WebhookEvent before processing
- Record BEFORE processing: Insert to prevent race conditions
- Always return 200: Prevent Stripe retries on processing errors
"""

import stripe
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.models import Invoice, Payment, WebhookEvent, ActivityLog
from app.config import settings
from app.invoices import service as invoice_service
from app.core.dates import sydney_now


# =============================================================================
# STRIPE CHECKOUT
# =============================================================================

async def create_checkout_session(
    db: AsyncSession,
    invoice: Invoice,
    success_url: str,
    cancel_url: str,
) -> dict:
    """
    Create Stripe Checkout session for invoice payment.

    Uses idempotency key to prevent duplicate sessions.

    Returns:
        dict with checkout_url and session_id
    """
    if not settings.stripe_secret_key:
        raise ValueError("Stripe is not configured")

    # Initialize Stripe with secret key
    stripe.api_key = settings.stripe_secret_key

    # Calculate balance due
    balance_cents = invoice.total_cents - invoice.paid_cents
    if balance_cents <= 0:
        raise ValueError("Invoice is already paid")

    # Build line items for Stripe
    line_items = [{
        "price_data": {
            "currency": "aud",
            "product_data": {
                "name": f"Invoice {invoice.invoice_number}",
                "description": invoice.description or f"Payment for invoice {invoice.invoice_number}",
            },
            "unit_amount": balance_cents,
        },
        "quantity": 1,
    }]

    # Create checkout session with idempotency key
    # Key format ensures same invoice amount creates same session
    idempotency_key = f"invoice_{invoice.id}_{balance_cents}"

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=line_items,
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "invoice_id": str(invoice.id),
                "invoice_number": invoice.invoice_number,
            },
            idempotency_key=idempotency_key,
        )
    except stripe.error.StripeError as e:
        raise ValueError(f"Stripe error: {str(e)}")

    return {
        "checkout_url": session.url,
        "session_id": session.id,
    }


# =============================================================================
# WEBHOOK SIGNATURE VERIFICATION
# =============================================================================

async def verify_webhook_signature(
    payload: bytes,
    signature: str,
) -> dict:
    """
    Verify Stripe webhook signature.

    Returns parsed event if valid.
    Raises HTTPException if invalid.
    """
    if not settings.stripe_webhook_secret:
        raise HTTPException(500, "Stripe webhook secret not configured")

    try:
        event = stripe.Webhook.construct_event(
            payload,
            signature,
            settings.stripe_webhook_secret,
        )
        return event
    except stripe.error.SignatureVerificationError:
        raise HTTPException(400, "Invalid webhook signature")
    except Exception as e:
        raise HTTPException(400, f"Webhook error: {str(e)}")


# =============================================================================
# WEBHOOK IDEMPOTENCY
# =============================================================================

async def check_webhook_idempotency(
    db: AsyncSession,
    provider: str,
    event_id: str,
) -> bool:
    """
    Check if webhook event was already processed.

    Returns True if already processed (should skip).
    Returns False if new event (should process).
    """
    result = await db.execute(
        select(WebhookEvent)
        .where(WebhookEvent.provider == provider)
        .where(WebhookEvent.event_id == event_id)
    )
    return result.scalar_one_or_none() is not None


async def record_webhook_event(
    db: AsyncSession,
    provider: str,
    event_id: str,
    event_type: str,
) -> WebhookEvent:
    """
    Record webhook event for idempotency.

    MUST be called BEFORE processing to prevent race conditions.
    The unique constraint on (provider, event_id) ensures only one
    process can record the event.
    """
    event = WebhookEvent(
        provider=provider,
        event_id=event_id,
        event_type=event_type,
    )
    db.add(event)
    await db.flush()  # Ensure unique constraint is checked immediately
    return event


# =============================================================================
# WEBHOOK EVENT HANDLERS
# =============================================================================

async def handle_checkout_completed(
    db: AsyncSession,
    session_data: dict,
) -> Optional[Payment]:
    """
    Handle checkout.session.completed event.

    Creates payment record and updates invoice.
    """
    # Extract metadata
    metadata = session_data.get("metadata", {})
    invoice_id = metadata.get("invoice_id")

    if not invoice_id:
        return None  # Not our invoice

    # Get invoice
    invoice = await invoice_service.get_invoice(db, int(invoice_id))
    if not invoice:
        return None

    # Get payment amount (in cents)
    amount_cents = session_data.get("amount_total", 0)

    # Get Stripe IDs
    payment_intent_id = session_data.get("payment_intent")
    checkout_session_id = session_data.get("id")

    # Secondary idempotency check: verify payment not already recorded by session ID
    result = await db.execute(
        select(Payment)
        .where(Payment.stripe_checkout_session_id == checkout_session_id)
    )
    if result.scalar_one_or_none():
        return None  # Already recorded

    # Record payment
    payment = await invoice_service.record_payment(
        db=db,
        invoice=invoice,
        amount_cents=amount_cents,
        method="stripe",
        stripe_payment_intent_id=payment_intent_id,
        stripe_checkout_session_id=checkout_session_id,
    )

    return payment


# =============================================================================
# MAIN WEBHOOK PROCESSOR
# =============================================================================

async def process_stripe_webhook(
    db: AsyncSession,
    event: dict,
) -> dict:
    """
    Process verified Stripe webhook event.

    Idempotency flow:
    1. Check WebhookEvent table for event_id
    2. If exists, return "already_processed"
    3. Record event BEFORE processing (prevents race conditions)
    4. Process the event
    5. Return result

    Returns result dict.
    """
    event_type = event.get("type")
    event_id = event.get("id")

    # Step 1: Check idempotency FIRST
    if await check_webhook_idempotency(db, "stripe", event_id):
        return {"status": "already_processed", "event_id": event_id}

    # Step 2: Record event BEFORE processing (prevents race conditions)
    try:
        await record_webhook_event(db, "stripe", event_id, event_type)
    except Exception:
        # If we can't record (unique constraint violation = race condition)
        # another process already got it
        return {"status": "already_processed", "event_id": event_id}

    # Step 3: Handle event types
    if event_type == "checkout.session.completed":
        session_data = event.get("data", {}).get("object", {})
        payment = await handle_checkout_completed(db, session_data)

        if payment:
            return {
                "status": "processed",
                "event_type": event_type,
                "payment_id": payment.id,
                "invoice_id": payment.invoice_id,
            }
        return {"status": "processed", "event_type": event_type, "note": "No matching invoice"}

    # payment_intent.succeeded - alternative event type
    elif event_type == "payment_intent.succeeded":
        # Could handle this as backup, but checkout.session.completed is preferred
        return {"status": "ignored", "event_type": event_type, "note": "Using checkout.session.completed instead"}

    # Other event types (payment_intent.payment_failed, etc.)
    return {"status": "ignored", "event_type": event_type}
