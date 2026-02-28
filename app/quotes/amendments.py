"""
Quote Amendment/Variation Service.

Handles post-acceptance scope changes with separate approval flow.
Uses the existing QuoteAmendment model.
"""

import hashlib
import secrets
from typing import Optional, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request

from app.models import QuoteAmendment, Quote, ActivityLog
from app.core.dates import sydney_now
from app.core.money import extract_gst, format_money


def _hash_amendment_token(raw_token: str) -> str:
    """Hash an amendment portal token (SHA-256) for storage."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def generate_amendment_token() -> tuple[str, str]:
    """Generate (raw_token, hashed_token) pair for amendment portal URLs."""
    raw_token = secrets.token_urlsafe(48)
    return raw_token, _hash_amendment_token(raw_token)


async def create_amendment(
    db: AsyncSession,
    quote_id: int,
    description: str,
    amount_cents: int,
) -> QuoteAmendment:
    """Create a new amendment (draft status)."""

    # Verify quote exists
    result = await db.execute(select(Quote).where(Quote.id == quote_id))
    quote = result.scalar_one_or_none()
    if not quote:
        raise ValueError(f"Quote {quote_id} not found")

    # Get next amendment number for this quote
    result = await db.execute(
        select(func.max(QuoteAmendment.amendment_number))
        .where(QuoteAmendment.quote_id == quote_id)
    )
    max_num = result.scalar() or 0
    next_num = max_num + 1

    _raw_token, hashed_token = generate_amendment_token()
    amendment = QuoteAmendment(
        quote_id=quote_id,
        amendment_number=next_num,
        description=description,
        amount_cents=amount_cents,
        status="draft",
        portal_token=hashed_token,
    )

    db.add(amendment)
    await db.flush()

    # Log activity
    sign = "+" if amount_cents >= 0 else ""
    activity = ActivityLog(
        action="amendment_created",
        description=f"Created Amendment #{next_num} ({sign}{format_money(amount_cents)}) on {quote.quote_number or f'Quote #{quote_id}'}",
        entity_type="quote",
        entity_id=quote_id,
        extra_data={"amendment_id": amendment.id, "amount_cents": amount_cents},
    )
    db.add(activity)

    await db.commit()
    await db.refresh(amendment)

    return amendment


async def get_amendment(db: AsyncSession, amendment_id: int) -> Optional[QuoteAmendment]:
    """Get amendment by ID."""
    result = await db.execute(
        select(QuoteAmendment).where(QuoteAmendment.id == amendment_id)
    )
    return result.scalar_one_or_none()


async def get_amendments_for_quote(db: AsyncSession, quote_id: int) -> List[QuoteAmendment]:
    """Get all amendments for a quote, ordered by number."""
    result = await db.execute(
        select(QuoteAmendment)
        .where(QuoteAmendment.quote_id == quote_id)
        .order_by(QuoteAmendment.amendment_number)
    )
    return list(result.scalars().all())


async def update_amendment(
    db: AsyncSession,
    amendment_id: int,
    description: Optional[str] = None,
    amount_cents: Optional[int] = None,
) -> QuoteAmendment:
    """Update amendment (only if draft)."""

    amendment = await get_amendment(db, amendment_id)
    if not amendment:
        raise ValueError(f"Amendment {amendment_id} not found")

    if amendment.status != "draft":
        raise ValueError("Cannot update non-draft amendment")

    if description is not None:
        amendment.description = description
    if amount_cents is not None:
        amendment.amount_cents = amount_cents

    await db.commit()
    await db.refresh(amendment)

    return amendment


async def send_amendment(db: AsyncSession, amendment_id: int) -> QuoteAmendment:
    """Mark amendment as sent."""

    amendment = await get_amendment(db, amendment_id)
    if not amendment:
        raise ValueError(f"Amendment {amendment_id} not found")

    if amendment.status != "draft":
        raise ValueError("Can only send draft amendments")

    amendment.status = "sent"
    amendment.sent_at = sydney_now()

    # Log activity
    activity = ActivityLog(
        action="amendment_sent",
        description=f"Sent Amendment #{amendment.amendment_number} to customer",
        entity_type="quote",
        entity_id=amendment.quote_id,
        extra_data={"amendment_id": amendment.id},
    )
    db.add(activity)

    await db.commit()
    await db.refresh(amendment)

    return amendment


async def accept_amendment(
    db: AsyncSession,
    amendment_id: int,
    signature_data: Optional[str] = None,
    signature_name: Optional[str] = None,
) -> QuoteAmendment:
    """Accept amendment (customer action via portal)."""

    amendment = await get_amendment(db, amendment_id)
    if not amendment:
        raise ValueError(f"Amendment {amendment_id} not found")

    if amendment.status != "sent":
        raise ValueError("Can only accept sent amendments")

    amendment.status = "accepted"
    amendment.accepted_at = sydney_now()
    amendment.signature_data = signature_data
    amendment.signature_name = signature_name

    await db.commit()
    await db.refresh(amendment)

    return amendment


async def delete_amendment(db: AsyncSession, amendment_id: int) -> None:
    """Delete amendment (only if draft)."""

    amendment = await get_amendment(db, amendment_id)
    if not amendment:
        raise ValueError(f"Amendment {amendment_id} not found")

    if amendment.status != "draft":
        raise ValueError("Can only delete draft amendments")

    await db.delete(amendment)
    await db.commit()


async def get_amendment_by_token(db: AsyncSession, token: str) -> Optional[QuoteAmendment]:
    """Get amendment by portal token (hashes incoming raw token for lookup)."""
    hashed = _hash_amendment_token(token)
    result = await db.execute(
        select(QuoteAmendment).where(QuoteAmendment.portal_token == hashed)
    )
    return result.scalar_one_or_none()


async def decline_amendment(
    db: AsyncSession,
    amendment_id: int,
    reason: Optional[str] = None,
) -> QuoteAmendment:
    """Decline amendment (customer action via portal or admin)."""

    amendment = await get_amendment(db, amendment_id)
    if not amendment:
        raise ValueError(f"Amendment {amendment_id} not found")

    if amendment.status != "sent":
        raise ValueError("Can only decline sent amendments")

    amendment.status = "declined"
    amendment.declined_at = sydney_now()
    amendment.decline_reason = reason

    # Log activity
    activity = ActivityLog(
        action="amendment_declined",
        description=f"Amendment #{amendment.amendment_number} was declined" + (f": {reason}" if reason else ""),
        entity_type="quote",
        entity_id=amendment.quote_id,
        extra_data={"amendment_id": amendment.id, "reason": reason},
    )
    db.add(activity)

    await db.commit()
    await db.refresh(amendment)

    return amendment


async def get_accepted_amendments_total(db: AsyncSession, quote_id: int) -> int:
    """
    Get sum of amount_cents for all accepted amendments on a quote.
    Returns total in cents (can be negative if credits outweigh additions).
    """
    result = await db.execute(
        select(func.coalesce(func.sum(QuoteAmendment.amount_cents), 0))
        .where(QuoteAmendment.quote_id == quote_id)
        .where(QuoteAmendment.status == "accepted")
    )
    return result.scalar()


async def create_variation_invoice(
    db: AsyncSession,
    amendment: QuoteAmendment,
    quote: Quote,
    request: Request = None,
) -> Optional[tuple]:
    """
    Create and send a variation invoice for a positive-amount accepted amendment.

    Only creates an invoice for additions (positive amount_cents).
    Credits/reductions don't generate invoices.

    Returns (invoice, raw_token) tuple or None if amount is negative.
    """
    if amendment.amount_cents <= 0:
        return None

    from app.invoices.service import create_invoice, send_invoice
    from app.schemas import InvoiceCreate

    # Extract GST from the GST-inclusive variation amount
    subtotal_cents, gst_cents = extract_gst(amendment.amount_cents)

    invoice_data = InvoiceCreate(
        customer_id=quote.customer_id,
        quote_id=quote.id,
        description=f"Variation #{amendment.amendment_number}: {amendment.description}",
        stage="variation",
        subtotal_cents=subtotal_cents,
    )

    invoice, raw_token = await create_invoice(db, invoice_data, request)

    # Send the invoice immediately
    try:
        raw_token = await send_invoice(db, invoice, request)
    except Exception:
        pass  # Invoice created but send failed — admin can resend manually

    # Log activity
    activity = ActivityLog(
        action="variation_invoice_created",
        description=f"Variation invoice created for Amendment #{amendment.amendment_number} ({format_money(amendment.amount_cents)})",
        entity_type="quote",
        entity_id=quote.id,
        extra_data={"amendment_id": amendment.id, "invoice_id": invoice.id},
    )
    db.add(activity)
    await db.commit()

    return invoice, raw_token
