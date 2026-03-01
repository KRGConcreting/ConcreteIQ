"""
Quotes service — Business logic for quote operations.

Handles CRUD, quote numbering, portal tokens, and status transitions.
"""

import secrets
import hashlib
from datetime import timedelta
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request

from app.models import Quote, Customer, Invoice, Sequence, ActivityLog
from app.schemas import QuoteCreate, QuoteUpdate, LabourQuoteCreate, CustomQuoteCreate
from app.core.dates import sydney_now, sydney_today
from app.quotes.calculator import calculate_quote
from app.quotes.customer_lines import (
    generate_customer_line_items,
    sum_customer_line_items,
)
from app.config import settings
from app.database import is_sqlite


# =============================================================================
# HELPERS
# =============================================================================

async def _get_expiry_days(db: AsyncSession) -> int:
    """Get quote expiry days from settings, default 30."""
    from app.settings.service import get_setting
    val = await get_setting(db, "quotation", "default_expiry_days", default=30)
    try:
        return int(val)
    except (TypeError, ValueError):
        return 30


# =============================================================================
# PORTAL TOKEN FUNCTIONS
# =============================================================================

def generate_portal_token() -> tuple[str, str]:
    """
    Generate a secure portal token.

    Returns:
        tuple of (raw_token, hashed_token)
        - raw_token: Given to customer in portal URL
        - hashed_token: Stored in database
    """
    raw_token = secrets.token_urlsafe(48)  # 64 characters
    hashed_token = hash_portal_token(raw_token)
    return raw_token, hashed_token


def hash_portal_token(raw_token: str) -> str:
    """
    Hash a portal token for storage/lookup.

    Uses SHA256 for consistent hashing.
    """
    return hashlib.sha256(raw_token.encode()).hexdigest()


# =============================================================================
# QUOTE NUMBER GENERATION
# =============================================================================

async def get_next_quote_number(db: AsyncSession) -> str:
    """
    Generate the next quote number in format Q-YYYY-NNNNN.

    Uses row-level locking to prevent race conditions.
    """
    year = sydney_now().year
    sequence_name = f"quote_{year}"

    # Try to get existing sequence with lock
    # SQLite doesn't support SELECT ... FOR UPDATE; skip row-level locking on SQLite
    query = select(Sequence).where(Sequence.name == sequence_name)
    if not is_sqlite:
        query = query.with_for_update()
    result = await db.execute(query)
    sequence = result.scalar_one_or_none()

    if sequence is None:
        # Create new sequence for this year
        sequence = Sequence(name=sequence_name, current_value=0)
        db.add(sequence)
        await db.flush()
        # Re-fetch with lock
        query = select(Sequence).where(Sequence.name == sequence_name)
        if not is_sqlite:
            query = query.with_for_update()
        result = await db.execute(query)
        sequence = result.scalar_one()

    # Increment
    sequence.current_value += 1
    next_number = sequence.current_value

    return f"Q-{year}-{next_number:05d}"


# =============================================================================
# STATUS TRANSITIONS
# =============================================================================

VALID_TRANSITIONS = {
    "draft": ["sent"],
    "sent": ["viewed", "accepted", "expired"],       # accepted when first payment received
    "viewed": ["accepted", "declined", "expired"],    # viewed still tracked but not required
    "accepted": ["confirmed"],                         # admin confirms date
    "confirmed": ["pour_stage"],                       # 60% prepour invoice sent
    "pour_stage": ["pending_completion"],               # final 10% invoice sent
    "pending_completion": ["completed"],                # final payment received
}


def validate_status_transition(current: str, new: str) -> bool:
    """Check if a status transition is allowed."""
    return new in VALID_TRANSITIONS.get(current, [])


# =============================================================================
# CRUD OPERATIONS
# =============================================================================

async def create_quote(
    db: AsyncSession,
    data: QuoteCreate,
    request: Request,
) -> tuple[Quote, str]:
    """
    Create a new quote with calculated values.

    Returns:
        tuple of (Quote, raw_portal_token)
        - raw_portal_token is for the portal URL (not stored)
    """
    # Verify customer exists
    customer = await db.get(Customer, data.customer_id)
    if not customer:
        raise ValueError(f"Customer {data.customer_id} not found")

    # Generate quote number and portal token
    quote_number = await get_next_quote_number(db)
    raw_token, hashed_token = generate_portal_token()

    # Run calculator
    calculator_input = data.calculator_input.model_dump()

    # Apply customer discount if set
    if customer.discount_percent and customer.discount_percent > 0:
        calculator_input["customer_discount_percent"] = float(customer.discount_percent)

    calculator_result = calculate_quote(calculator_input)

    # Generate customer-facing line item groups
    customer_line_items = generate_customer_line_items(calculator_result, calculator_input)

    # Create quote
    quote = Quote(
        quote_type="calculator",
        quote_number=quote_number,
        customer_id=data.customer_id,
        job_name=data.job_name,
        job_address=data.job_address,
        calculator_input=calculator_input,
        calculator_result=calculator_result,
        line_items=calculator_result.get("line_items", []),
        customer_line_items=customer_line_items,
        subtotal_cents=calculator_result["subtotal_cents"],
        discount_cents=calculator_result.get("discount_cents", 0),
        gst_cents=calculator_result["gst_cents"],
        total_cents=calculator_result["total_cents"],
        status="draft",
        quote_date=sydney_today(),
        expiry_date=sydney_today() + timedelta(days=await _get_expiry_days(db)),
        portal_token=hashed_token,
        notes=data.notes,
        internal_notes=data.internal_notes,
    )

    db.add(quote)
    await db.flush()

    # Log activity
    activity = ActivityLog(
        action="quote_created",
        description=f"Created quote {quote_number} for {customer.name}",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=request.client.host if request.client else None,
        extra_data={"total_cents": quote.total_cents},
    )
    db.add(activity)

    return quote, raw_token


async def create_labour_quote(
    db: AsyncSession,
    data: LabourQuoteCreate,
    request: Request,
) -> tuple[Quote, str]:
    """
    Create a labour invoice quote with PAYG/super/workcover breakdown.

    Used for when a subcontractor or another concreter works for the day.
    """
    from uuid import uuid4
    from app.quotes.pricing import TEAM_RATES, GST_RATE

    PAYG_RATE = 0.17
    SUPER_RATE = 0.12
    WORKCOVER_RATE = 0.085

    customer = await db.get(Customer, data.customer_id)
    if not customer:
        raise ValueError(f"Customer {data.customer_id} not found")

    quote_number = await get_next_quote_number(db)
    raw_token, hashed_token = generate_portal_token()

    # Determine hourly rate
    if data.hourly_rate_cents:
        hourly_rate = data.hourly_rate_cents
    else:
        team = TEAM_RATES.get(data.team_tier, TEAM_RATES["Standard"])
        hourly_rate = team["hourly"]

    # Calculate breakdown
    gross_cents = int(round(hourly_rate * data.hours))
    payg_cents = int(round(gross_cents * PAYG_RATE))
    super_cents = int(round(gross_cents * SUPER_RATE))
    workcover_cents = int(round(gross_cents * WORKCOVER_RATE))
    subtotal_cents = gross_cents + super_cents + workcover_cents
    gst_cents = int(round(subtotal_cents * GST_RATE))
    total_cents = subtotal_cents + gst_cents

    # Build internal line items
    line_items = [
        {"description": f"Labour - {data.worker_name}", "quantity": data.hours,
         "unit": "hrs", "unit_price_cents": hourly_rate,
         "total_cents": gross_cents, "category": "labour"},
        {"description": f"Superannuation ({SUPER_RATE*100:.0f}%)", "quantity": 1, "unit": "calc",
         "unit_price_cents": super_cents, "total_cents": super_cents, "category": "payroll"},
        {"description": f"WorkCover ({WORKCOVER_RATE*100:.0f}%)", "quantity": 1, "unit": "calc",
         "unit_price_cents": workcover_cents, "total_cents": workcover_cents, "category": "payroll"},
    ]

    # Build customer-facing line items
    customer_line_items = [{
        "id": str(uuid4()),
        "category": f"Labour - {data.worker_name}",
        "sub_items": [
            {"description": f"{data.hours}hrs @ ${hourly_rate/100:.2f}/hr", "total_cents": gross_cents},
            {"description": f"Super ({SUPER_RATE*100:.0f}%)", "total_cents": super_cents},
            {"description": f"WorkCover ({WORKCOVER_RATE*100:.0f}%)", "total_cents": workcover_cents},
        ],
        "total_cents": subtotal_cents,
        "show_sub_prices": True,
        "sort_order": 0,
    }]

    # Store labour-specific data in calculator_input for reference/editing
    calculator_input = {
        "quote_type": "labour",
        "worker_name": data.worker_name,
        "work_date": str(data.work_date),
        "hours": data.hours,
        "team_tier": data.team_tier,
        "hourly_rate_cents": hourly_rate,
        "payg_rate": PAYG_RATE,
        "super_rate": SUPER_RATE,
        "workcover_rate": WORKCOVER_RATE,
    }

    calculator_result = {
        "gross_cents": gross_cents,
        "payg_cents": payg_cents,
        "super_cents": super_cents,
        "workcover_cents": workcover_cents,
        "net_wages_cents": gross_cents - payg_cents,
        "subtotal_cents": subtotal_cents,
        "gst_cents": gst_cents,
        "total_cents": total_cents,
    }

    job_name = data.job_name or f"Labour - {data.worker_name} - {data.work_date}"

    quote = Quote(
        quote_type="labour",
        quote_number=quote_number,
        customer_id=data.customer_id,
        job_name=job_name,
        job_address=data.job_address,
        calculator_input=calculator_input,
        calculator_result=calculator_result,
        line_items=line_items,
        customer_line_items=customer_line_items,
        subtotal_cents=subtotal_cents,
        discount_cents=0,
        gst_cents=gst_cents,
        total_cents=total_cents,
        status="draft",
        quote_date=sydney_today(),
        expiry_date=sydney_today() + timedelta(days=await _get_expiry_days(db)),
        portal_token=hashed_token,
        notes=data.notes,
        internal_notes=data.internal_notes,
    )
    db.add(quote)
    await db.flush()

    activity = ActivityLog(
        action="quote_created",
        description=f"Created labour quote {quote_number} for {customer.name}",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=request.client.host if request.client else None,
        extra_data={"total_cents": total_cents, "quote_type": "labour"},
    )
    db.add(activity)
    return quote, raw_token


async def create_custom_quote(
    db: AsyncSession,
    data: CustomQuoteCreate,
    request: Request,
) -> tuple[Quote, str]:
    """
    Create a custom/freeform quote from manual line items.
    """
    from uuid import uuid4
    from app.quotes.pricing import GST_RATE

    customer = await db.get(Customer, data.customer_id)
    if not customer:
        raise ValueError(f"Customer {data.customer_id} not found")

    quote_number = await get_next_quote_number(db)
    raw_token, hashed_token = generate_portal_token()

    # Build line items and calculate totals
    line_items = []
    subtotal_cents = 0
    taxable_subtotal = 0
    for item in data.line_items:
        item_total = int(round(item.quantity * item.unit_price_cents))
        taxable = getattr(item, 'taxable', True)
        line_items.append({
            "description": item.description,
            "category": getattr(item, 'category', 'Service'),
            "quantity": item.quantity,
            "unit": item.unit,
            "unit_price_cents": item.unit_price_cents,
            "total_cents": item_total,
            "taxable": taxable,
        })
        subtotal_cents += item_total
        if taxable:
            taxable_subtotal += item_total

    gst_cents = int(round(taxable_subtotal * GST_RATE))
    total_cents = subtotal_cents + gst_cents

    # Build customer-facing line items grouped by category
    categories_seen = []
    items_by_cat: dict[str, list] = {}
    for li in line_items:
        cat = li.get("category", "Service")
        if cat not in items_by_cat:
            items_by_cat[cat] = []
            categories_seen.append(cat)
        items_by_cat[cat].append(li)

    customer_line_items = []
    for sort_idx, cat in enumerate(categories_seen):
        cat_items = items_by_cat[cat]
        cat_total = sum(li["total_cents"] for li in cat_items)
        customer_line_items.append({
            "id": str(uuid4()),
            "category": cat,
            "sub_items": [
                {
                    "description": f"{li['description']} ({li['quantity']} {li['unit']} @ ${li['unit_price_cents']/100:.2f})",
                    "total_cents": li["total_cents"],
                }
                for li in cat_items
            ],
            "total_cents": cat_total,
            "show_sub_prices": True,
            "sort_order": sort_idx,
        })

    quote = Quote(
        quote_type="custom",
        quote_number=quote_number,
        customer_id=data.customer_id,
        job_name=data.job_name,
        job_type=data.job_type,
        job_address=data.job_address,
        calculator_input={"quote_type": "custom"},
        calculator_result=None,
        line_items=line_items,
        customer_line_items=customer_line_items,
        subtotal_cents=subtotal_cents,
        discount_cents=0,
        gst_cents=gst_cents,
        total_cents=total_cents,
        status="draft",
        quote_date=sydney_today(),
        expiry_date=sydney_today() + timedelta(days=await _get_expiry_days(db)),
        portal_token=hashed_token,
        notes=data.notes,
        internal_notes=data.internal_notes,
    )
    db.add(quote)
    await db.flush()

    activity = ActivityLog(
        action="quote_created",
        description=f"Created custom quote {quote_number} for {customer.name}",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=request.client.host if request.client else None,
        extra_data={"total_cents": total_cents, "quote_type": "custom"},
    )
    db.add(activity)
    return quote, raw_token


async def get_quotes(
    db: AsyncSession,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Quote], int]:
    """
    Get paginated list of quotes with optional status filter.

    Returns:
        tuple of (quotes, total_count)
    """
    offset = (page - 1) * page_size

    # Base query
    query = select(Quote).order_by(Quote.created_at.desc())
    count_query = select(func.count(Quote.id))

    # Apply status filter
    if status:
        query = query.where(Quote.status == status)
        count_query = count_query.where(Quote.status == status)

    # Execute
    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(query.offset(offset).limit(page_size))
    quotes = result.scalars().all()

    return list(quotes), total


async def get_quote(db: AsyncSession, quote_id: int) -> Optional[Quote]:
    """Get a single quote by ID."""
    return await db.get(Quote, quote_id)


async def get_quote_by_token(db: AsyncSession, raw_token: str) -> Optional[Quote]:
    """
    Get a quote by portal token.

    Hashes the incoming raw token and looks up by hash.
    """
    hashed_token = hash_portal_token(raw_token)
    result = await db.execute(
        select(Quote).where(Quote.portal_token == hashed_token)
    )
    return result.scalar_one_or_none()


async def update_quote(
    db: AsyncSession,
    quote: Quote,
    data: QuoteUpdate,
    request: Request,
) -> Quote:
    """
    Update a draft quote.

    Only draft quotes can be updated.
    Re-runs calculator if input changed.
    """
    if quote.status != "draft":
        raise ValueError(f"Cannot update quote in '{quote.status}' status")

    update_data = data.model_dump(exclude_unset=True)
    changes = list(update_data.keys())

    # Check if calculator input changed
    recalculate = False
    if "calculator_input" in update_data and update_data["calculator_input"]:
        calculator_input = update_data["calculator_input"].model_dump() if hasattr(update_data["calculator_input"], 'model_dump') else update_data["calculator_input"]
        quote.calculator_input = calculator_input
        recalculate = True
        del update_data["calculator_input"]

    # Apply other updates
    for key, value in update_data.items():
        setattr(quote, key, value)

    # Recalculate if needed
    if recalculate:
        calculator_result = calculate_quote(quote.calculator_input)
        quote.calculator_result = calculator_result
        quote.line_items = calculator_result.get("line_items", [])
        quote.customer_line_items = generate_customer_line_items(
            calculator_result, quote.calculator_input
        )
        quote.subtotal_cents = calculator_result["subtotal_cents"]
        quote.discount_cents = calculator_result.get("discount_cents", 0)
        quote.gst_cents = calculator_result["gst_cents"]
        quote.total_cents = calculator_result["total_cents"]

    # Log activity
    activity = ActivityLog(
        action="quote_updated",
        description=f"Updated quote {quote.quote_number}",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=request.client.host if request.client else None,
        extra_data={"changes": changes},
    )
    db.add(activity)

    return quote


async def delete_quote(
    db: AsyncSession,
    quote: Quote,
    request: Request,
) -> None:
    """
    Delete a draft quote.

    Only draft quotes can be deleted.
    """
    if quote.status != "draft":
        raise ValueError(f"Cannot delete quote in '{quote.status}' status")

    # Log before delete
    activity = ActivityLog(
        action="quote_deleted",
        description=f"Deleted quote {quote.quote_number}",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=request.client.host if request.client else None,
    )
    db.add(activity)

    await db.delete(quote)


async def update_quote_preview(
    db: AsyncSession,
    quote: Quote,
    customer_line_items: list[dict],
    notes: Optional[str],
    request: Request,
) -> Quote:
    """
    Update customer-facing line items from preview page.

    Recalculates subtotal/gst/total from customer_line_items sum.
    Does NOT touch calculator_result or calculator_input.
    """
    if quote.status != "draft":
        raise ValueError(f"Cannot update quote in '{quote.status}' status")

    quote.customer_line_items = customer_line_items
    subtotal, gst, total = sum_customer_line_items(customer_line_items)
    quote.subtotal_cents = subtotal
    quote.gst_cents = gst
    quote.total_cents = total
    quote.discount_cents = 0  # Discounts baked into group prices

    if notes is not None:
        quote.notes = notes

    # Log activity
    activity = ActivityLog(
        action="quote_preview_updated",
        description=f"Updated customer line items for {quote.quote_number}",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=request.client.host if request.client else None,
        extra_data={"total_cents": total},
    )
    db.add(activity)

    return quote


async def duplicate_quote(
    db: AsyncSession,
    quote: Quote,
    request: Request,
) -> tuple[Quote, str]:
    """
    Duplicate a quote to a new draft.

    Returns:
        tuple of (new_quote, raw_portal_token)
    """
    # Generate new quote number and portal token
    quote_number = await get_next_quote_number(db)
    raw_token, hashed_token = generate_portal_token()

    # Create duplicate
    new_quote = Quote(
        quote_type=quote.quote_type or "calculator",
        quote_number=quote_number,
        customer_id=quote.customer_id,
        job_name=quote.job_name,
        job_address=quote.job_address,
        job_address_lat=quote.job_address_lat,
        job_address_lng=quote.job_address_lng,
        distance_km=quote.distance_km,
        calculator_input=quote.calculator_input,
        calculator_result=quote.calculator_result,
        line_items=quote.line_items,
        customer_line_items=quote.customer_line_items,
        subtotal_cents=quote.subtotal_cents,
        discount_cents=quote.discount_cents,
        gst_cents=quote.gst_cents,
        total_cents=quote.total_cents,
        status="draft",
        quote_date=sydney_today(),
        expiry_date=sydney_today() + timedelta(days=await _get_expiry_days(db)),
        portal_token=hashed_token,
        notes=quote.notes,
        internal_notes=quote.internal_notes,
        # Signature fields NOT copied
    )

    db.add(new_quote)
    await db.flush()

    # Log activity
    activity = ActivityLog(
        action="quote_duplicated",
        description=f"Duplicated quote {quote.quote_number} to {quote_number}",
        entity_type="quote",
        entity_id=new_quote.id,
        ip_address=request.client.host if request.client else None,
        extra_data={"source_quote_id": quote.id},
    )
    db.add(activity)

    return new_quote, raw_token


# =============================================================================
# STATUS TRANSITIONS
# =============================================================================

async def send_quote(
    db: AsyncSession,
    quote: Quote,
    request: Request,
) -> str:
    """
    Mark quote as sent and send email to customer.

    Returns:
        raw_portal_token for the portal URL
    """
    if quote.status != "draft":
        raise ValueError(f"Cannot send quote in '{quote.status}' status")

    # Generate new portal token (in case old one was compromised)
    raw_token, hashed_token = generate_portal_token()

    quote.status = "sent"
    quote.sent_at = sydney_now()
    quote.portal_token = hashed_token

    # Get customer for email
    customer = await db.get(Customer, quote.customer_id)
    customer_email = customer.email if customer else None

    # Build portal URL
    portal_url = f"{settings.app_url}/p/{raw_token}"

    # Send email (import here to avoid circular imports)
    email_sent = False
    if customer:
        from app.notifications.email import send_quote_email
        email_sent = await send_quote_email(db, quote, customer, portal_url)

    # Log activity
    activity = ActivityLog(
        action="quote_sent",
        description=f"Sent quote {quote.quote_number}",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=request.client.host if request.client else None,
        extra_data={
            "customer_email": customer_email,
            "email_sent": email_sent,
            "portal_url_sent": True,  # Token redacted for security
        },
    )
    db.add(activity)

    # Create notification
    from app.notifications.service import notify_quote_sent
    await notify_quote_sent(db, quote, customer)

    return raw_token


async def mark_quote_viewed(
    db: AsyncSession,
    quote: Quote,
    ip_address: Optional[str] = None,
) -> Quote:
    """
    Mark quote as viewed (first portal access).

    Only updates if status is 'sent'.
    """
    if quote.status != "sent":
        return quote  # Already viewed or in different state

    quote.status = "viewed"
    quote.viewed_at = sydney_now()

    # Log activity
    activity = ActivityLog(
        action="quote_viewed",
        description=f"Quote {quote.quote_number} viewed in portal",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=ip_address,
    )
    db.add(activity)

    # Create notification
    from app.notifications.service import notify_quote_viewed
    await notify_quote_viewed(db, quote)

    return quote


async def sign_quote(
    db: AsyncSession,
    quote: Quote,
    signature_data: str,
    signature_name: str,
    ip_address: Optional[str] = None,
    signature_type: Optional[str] = None,
) -> tuple[Quote, bool]:
    """
    Customer signs quote T&C. Does NOT change status to accepted.

    Status transitions to 'accepted' only when first payment invoice is paid
    (handled by on_deposit_paid in invoices service).

    Captures signature and creates + sends first payment invoice immediately.

    Returns:
        tuple of (Quote, was_already_signed)
        - was_already_signed: True if quote was already signed (idempotent)
    """
    # Idempotent: if already signed
    if quote.signed_at:
        return quote, True

    # Cannot sign if declined
    if quote.status == "declined":
        raise ValueError("Cannot sign a declined quote")

    if quote.status not in ("sent", "viewed"):
        raise ValueError(f"Cannot sign quote in '{quote.status}' status")

    # Cannot sign if expired
    if quote.expiry_date and quote.expiry_date < sydney_today():
        raise ValueError("This quote has expired and can no longer be signed")

    # Capture signature (but do NOT change status — stays sent/viewed)
    quote.signature_data = signature_data
    quote.signature_type = signature_type or ("typed" if signature_data.startswith("typed:") else "drawn")
    quote.signature_name = signature_name
    quote.signature_ip = ip_address
    quote.signed_at = sydney_now()

    # Create all progress invoices (first payment 30%, progress 60%, final 10%)
    from app.invoices.service import create_progress_invoices, send_invoice
    invoices = await create_progress_invoices(db, quote, ip_address=ip_address)

    # Send the first payment invoice immediately
    deposit_invoice = next(
        (inv for inv in invoices if inv.stage in ("deposit", "booking")),
        None
    )
    deposit_invoice_number = ""
    if deposit_invoice:
        await send_invoice(db, deposit_invoice, ip_address=ip_address)
        deposit_invoice_number = deposit_invoice.invoice_number

    # Log activity
    activity = ActivityLog(
        action="quote_signed",
        description=f"Quote {quote.quote_number} signed by {signature_name}. First payment invoice {deposit_invoice_number} sent.",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=ip_address,
        extra_data={
            "signer_name": signature_name,
            "deposit_invoice_number": deposit_invoice_number,
            "invoices_created": len(invoices),
        },
    )
    db.add(activity)

    # Create notification
    from app.notifications.service import notify_quote_accepted
    await notify_quote_accepted(db, quote)

    return quote, False


# Backward compat alias
async def accept_quote(
    db: AsyncSession,
    quote: Quote,
    signature_data: str,
    signature_name: str,
    ip_address: Optional[str] = None,
) -> tuple[Quote, bool]:
    """Backward-compatible alias for sign_quote."""
    return await sign_quote(db, quote, signature_data, signature_name, ip_address)


async def decline_quote(
    db: AsyncSession,
    quote: Quote,
    reason: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> tuple[Quote, bool]:
    """
    Decline a quote.

    Sets status to 'declined'.

    Returns:
        tuple of (Quote, was_already_declined)
        - was_already_declined: True if quote was already declined (idempotent)
    """
    # Idempotent: if already declined, return success
    if quote.status == "declined":
        return quote, True

    # Cannot decline if already accepted
    if quote.status == "accepted":
        raise ValueError("Cannot decline an accepted quote")

    if quote.status not in ("sent", "viewed"):
        raise ValueError(f"Cannot decline quote in '{quote.status}' status")

    quote.status = "declined"
    quote.declined_at = sydney_now()
    quote.decline_reason = reason

    # Log activity
    activity = ActivityLog(
        action="quote_declined",
        description=f"Quote {quote.quote_number} declined",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=ip_address,
        extra_data={"reason": reason} if reason else None,
    )
    db.add(activity)

    # Create notification
    from app.notifications.service import notify_quote_declined
    await notify_quote_declined(db, quote, reason)

    return quote, False


async def select_date(
    db: AsyncSession,
    quote: Quote,
    requested_date,
    ip_address: Optional[str] = None,
) -> Quote:
    """
    Customer selects preferred start date.

    Does NOT change status - admin confirms date later.
    """
    quote.requested_start_date = requested_date

    # Log activity
    activity = ActivityLog(
        action="date_requested",
        description=f"Date {requested_date} requested for quote {quote.quote_number}",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=ip_address,
        extra_data={"requested_date": str(requested_date)},
    )
    db.add(activity)

    return quote


# =============================================================================
# BOOKING CONFIRMATION
# =============================================================================

async def confirm_booking(
    db: AsyncSession,
    quote: Quote,
    confirmed_date,
    request: Request,
) -> Quote:
    """
    Admin confirms booking date. First payment invoice already exists (created at sign time).

    Flow:
    1. Validate quote is in 'accepted' status
    2. Set confirmed_start_date and status='confirmed'
    3. Create Google Calendar event (fails gracefully)
    4. Schedule job reminders

    Args:
        db: Database session
        quote: The accepted quote
        confirmed_date: The confirmed start date (date object)
        request: HTTP request for IP logging

    Returns:
        Updated quote

    Raises:
        ValueError: If quote is not in 'accepted' status
    """
    if quote.status != "accepted":
        raise ValueError(f"Cannot confirm booking for quote in '{quote.status}' status. Quote must be accepted first.")

    # Update quote
    quote.confirmed_start_date = confirmed_date
    quote.status = "confirmed"

    # Fetch customer for calendar event and email (pre-fetch to avoid lazy loading)
    customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None

    # Create Google Calendar event (fails gracefully)
    from app.integrations.google_calendar import create_job_event
    from app.workers.service import get_job_assignments

    try:
        # Get assigned workers for calendar event description
        assignments = await get_job_assignments(db, quote.id)
        worker_names = [a.worker.name for a in assignments if a.worker]

        event_id = await create_job_event(
            quote, worker_names,
            customer_name=customer.name if customer else None,
            customer_phone=customer.phone if customer else None,
        )
        if event_id:
            quote.gcal_event_id = event_id
    except Exception as e:
        # Log but don't fail the booking
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to create calendar event for quote {quote.quote_number}: {e}")

    # Schedule job reminders (fails gracefully)
    try:
        from app.notifications.reminders import schedule_job_reminders
        await schedule_job_reminders(db, quote)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to schedule job reminders for quote {quote.quote_number}: {e}")

    # Log activity
    activity = ActivityLog(
        action="booking_confirmed",
        description=f"Booking confirmed for quote {quote.quote_number}, date: {confirmed_date}",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=request.client.host if request.client else None,
        extra_data={
            "confirmed_date": str(confirmed_date),
            "calendar_event_id": quote.gcal_event_id,
        },
    )
    db.add(activity)

    # Create job scheduled notification
    try:
        from app.notifications.service import notify_job_scheduled
        await notify_job_scheduled(db, quote, confirmed_date)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to create job scheduled notification: {e}")

    # Send booking confirmed email to customer (non-blocking)
    # customer already fetched above for calendar event
    if customer:
        try:
            from app.notifications.email import send_booking_confirmed_email
            from app.config import settings as app_settings

            # Find the first payment invoice URL if it exists
            invoice_url = ""
            from sqlalchemy import select as sa_select
            from app.models import Invoice
            inv_result = await db.execute(
                sa_select(Invoice)
                .where(Invoice.quote_id == quote.id, Invoice.stage == "booking")
                .limit(1)
            )
            booking_invoice = inv_result.scalars().first()
            if booking_invoice:
                # Generate fresh raw token — DB stores hash, URL uses raw
                from app.invoices.service import generate_portal_token as gen_inv_token
                raw_token, hashed_token = gen_inv_token()
                booking_invoice.portal_token = hashed_token
                await db.flush()
                invoice_url = f"{app_settings.app_url}/p/invoice/{raw_token}"

            await send_booking_confirmed_email(
                db=db,
                quote=quote,
                customer=customer,
                start_date=confirmed_date,
                invoice_url=invoice_url,
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to send booking confirmed email: {e}")

    return quote
