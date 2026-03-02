"""
Invoice service - Business logic for invoice operations.

Handles CRUD, invoice numbering, portal tokens, and status transitions.
"""

import secrets
import hashlib
from datetime import timedelta
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request

from app.models import Invoice, Quote, Customer, Payment, Sequence, ActivityLog
from app.schemas import InvoiceCreate
from app.core.dates import sydney_now, sydney_today
from app.core.money import calculate_gst, calculate_payment_split
from app.config import settings
from app.database import is_sqlite


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
# INVOICE NUMBER GENERATION
# =============================================================================

async def get_next_invoice_number(db: AsyncSession) -> str:
    """
    Generate the next invoice number in format INV-YYYY-NNNNN.

    Uses row-level locking to prevent race conditions.
    """
    year = sydney_now().year
    sequence_name = f"invoice_{year}"

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

    return f"INV-{year}-{next_number:05d}"


# =============================================================================
# STAGE AMOUNTS
# =============================================================================

def calculate_stage_amount(quote_total_cents: int, stage: str) -> int:
    """
    Calculate invoice amount based on stage.

    Uses the 30/60/10 split from calculate_payment_split.
    """
    split = calculate_payment_split(quote_total_cents)

    # Support both canonical and legacy stage names
    if stage in ("deposit", "booking"):
        return split["deposit"]  # 30%
    elif stage == "prepour":
        return split["prepour"]  # 60%
    elif stage in ("final", "completion"):
        return split["final"]  # 10%
    else:
        raise ValueError(f"Unknown stage: {stage}")


# =============================================================================
# STATUS TRANSITIONS
# =============================================================================

VALID_TRANSITIONS = {
    "draft": ["sent"],
    "sent": ["viewed", "paid", "partial", "overdue", "voided"],
    "viewed": ["paid", "partial", "overdue", "voided"],
    "partial": ["paid", "overdue", "voided"],
    "overdue": ["paid", "partial", "voided"],
}


def validate_status_transition(current: str, new: str) -> bool:
    """Check if a status transition is allowed."""
    return new in VALID_TRANSITIONS.get(current, [])


# =============================================================================
# CRUD OPERATIONS
# =============================================================================

async def create_invoice(
    db: AsyncSession,
    data: InvoiceCreate,
    request: Request,
) -> tuple[Invoice, str]:
    """
    Create a new invoice.

    Returns:
        tuple of (Invoice, raw_portal_token)
        - raw_portal_token is for the portal URL (not stored)
    """
    # Verify customer exists
    customer = await db.get(Customer, data.customer_id)
    if not customer:
        raise ValueError(f"Customer {data.customer_id} not found")

    # Generate invoice number and portal token
    invoice_number = await get_next_invoice_number(db)
    raw_token, hashed_token = generate_portal_token()

    # Calculate GST
    gst_cents = calculate_gst(data.subtotal_cents)
    total_cents = data.subtotal_cents + gst_cents

    # Create invoice
    invoice = Invoice(
        invoice_number=invoice_number,
        quote_id=data.quote_id,
        customer_id=data.customer_id,
        description=data.description,
        stage=data.stage,
        line_items=data.line_items,
        subtotal_cents=data.subtotal_cents,
        gst_cents=gst_cents,
        total_cents=total_cents,
        paid_cents=0,
        status="draft",
        issue_date=data.issue_date or sydney_today(),
        due_date=data.due_date or (sydney_today() + timedelta(days=7)),
        portal_token=hashed_token,
        notes=data.notes,
    )

    db.add(invoice)
    await db.flush()

    # Log activity
    activity = ActivityLog(
        action="invoice_created",
        description=f"Created invoice {invoice_number} for {customer.name}",
        entity_type="invoice",
        entity_id=invoice.id,
        ip_address=request.client.host if request.client else None,
        extra_data={"total_cents": total_cents, "stage": data.stage},
    )
    db.add(activity)

    return invoice, raw_token


async def create_invoice_from_quote(
    db: AsyncSession,
    quote: Quote,
    stage: str,
    request: Request,
) -> tuple[Invoice, str]:
    """
    Create invoice from quote for specific stage.

    Args:
        quote: The accepted quote
        stage: 'booking', 'prepour', or 'completion'

    Returns:
        tuple of (Invoice, raw_portal_token)
    """
    if stage not in ("deposit", "booking", "prepour", "final", "completion"):
        raise ValueError(f"Invalid stage: {stage}. Must be deposit, prepour, or final.")

    # Calculate amount for this stage (use subtotal ex-GST, not total inc-GST)
    subtotal_cents = calculate_stage_amount(quote.subtotal_cents, stage)

    # Determine description (support both canonical and legacy names)
    stage_names = {
        "deposit": "First Payment (30%)",
        "booking": "First Payment (30%)",
        "prepour": "Progress Payment (60%)",
        "final": "Final Payment (10%)",
        "completion": "Final Payment (10%)",
    }
    description = f"{stage_names[stage]} - {quote.quote_number}"

    # Build line items
    line_items = [{
        "description": description,
        "quantity": 1,
        "unit": "each",
        "unit_price_cents": subtotal_cents,
        "total_cents": subtotal_cents,
    }]

    data = InvoiceCreate(
        customer_id=quote.customer_id,
        quote_id=quote.id,
        description=description,
        stage=stage,
        line_items=line_items,
        subtotal_cents=subtotal_cents,
    )

    return await create_invoice(db, data, request)


async def get_invoices(
    db: AsyncSession,
    status: Optional[str] = None,
    customer_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Invoice], int]:
    """
    Get paginated list of invoices with optional filters.

    Returns:
        tuple of (invoices, total_count)
    """
    offset = (page - 1) * page_size

    # Base query
    query = select(Invoice).order_by(Invoice.created_at.desc())
    count_query = select(func.count(Invoice.id))

    # Apply filters
    if status:
        query = query.where(Invoice.status == status)
        count_query = count_query.where(Invoice.status == status)

    if customer_id:
        query = query.where(Invoice.customer_id == customer_id)
        count_query = count_query.where(Invoice.customer_id == customer_id)

    # Execute
    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(query.offset(offset).limit(page_size))
    invoices = result.scalars().all()

    return list(invoices), total


async def get_invoice(db: AsyncSession, invoice_id: int) -> Optional[Invoice]:
    """Get a single invoice by ID."""
    return await db.get(Invoice, invoice_id)


async def get_invoice_by_token(db: AsyncSession, raw_token: str) -> Optional[Invoice]:
    """
    Get an invoice by portal token.

    Hashes the incoming raw token and looks up by hash.
    """
    hashed_token = hash_portal_token(raw_token)
    result = await db.execute(
        select(Invoice).where(Invoice.portal_token == hashed_token)
    )
    return result.scalar_one_or_none()


# =============================================================================
# STATUS TRANSITIONS
# =============================================================================

async def send_invoice(
    db: AsyncSession,
    invoice: Invoice,
    request: Request = None,
    ip_address: Optional[str] = None,
) -> str:
    """
    Mark invoice as sent and send email to customer.

    Args:
        request: FastAPI request (optional, for IP logging)
        ip_address: Alternative to request for IP logging (used from portal)

    Returns:
        raw_portal_token for the portal URL
    """
    if invoice.status != "draft":
        raise ValueError(f"Cannot send invoice in '{invoice.status}' status")

    # Generate new portal token (in case old one was compromised)
    raw_token, hashed_token = generate_portal_token()

    invoice.status = "sent"
    invoice.portal_token = hashed_token

    # Get customer for email
    customer = await db.get(Customer, invoice.customer_id)
    if customer:
        from app.core.security import decrypt_customer_pii
        decrypt_customer_pii(customer)
    customer_email = customer.email if customer else None

    # Build portal URL
    portal_url = f"{settings.app_url}/p/invoice/{raw_token}"

    # Send email (import here to avoid circular imports)
    email_sent = False
    if customer:
        from app.notifications.email import send_invoice_email
        email_sent = await send_invoice_email(db, invoice, customer, portal_url)

    # Schedule payment reminders
    try:
        from app.notifications.reminders import schedule_payment_reminders
        await schedule_payment_reminders(db, invoice)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to schedule payment reminders: {e}")

    # Sync to Xero (fails gracefully)
    xero_id = None
    try:
        from app.integrations.xero import sync_invoice_to_xero
        xero_id = await sync_invoice_to_xero(db, invoice)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to sync invoice to Xero: {e}")

    # Log activity
    _ip = ip_address or (request.client.host if request and request.client else None)
    activity = ActivityLog(
        action="invoice_sent",
        description=f"Sent invoice {invoice.invoice_number}",
        entity_type="invoice",
        entity_id=invoice.id,
        ip_address=_ip,
        extra_data={
            "customer_email": customer_email,
            "email_sent": email_sent,
            "portal_url_sent": True,  # Token redacted for security
            "xero_invoice_id": xero_id,
        },
    )
    db.add(activity)

    # Create notification
    from app.notifications.service import notify_invoice_sent_admin
    await notify_invoice_sent_admin(db, invoice, customer)

    return raw_token


async def mark_invoice_viewed(
    db: AsyncSession,
    invoice: Invoice,
    ip_address: Optional[str] = None,
) -> Invoice:
    """
    Mark invoice as viewed (first portal access).

    Only updates if status is 'sent'.
    """
    if invoice.status != "sent":
        return invoice  # Already viewed or in different state

    invoice.status = "viewed"

    # Log activity
    activity = ActivityLog(
        action="invoice_viewed",
        description=f"Invoice {invoice.invoice_number} viewed in portal",
        entity_type="invoice",
        entity_id=invoice.id,
        ip_address=ip_address,
    )
    db.add(activity)

    # Create notification
    from app.notifications.service import notify_invoice_viewed
    await notify_invoice_viewed(db, invoice)

    return invoice


async def record_payment(
    db: AsyncSession,
    invoice: Invoice,
    amount_cents: int,
    method: str,
    reference: Optional[str] = None,
    stripe_payment_intent_id: Optional[str] = None,
    stripe_checkout_session_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    send_receipt: bool = True,
) -> Payment:
    """
    Record a payment against an invoice.
    Updates invoice paid_cents and status.
    Optionally sends payment receipt email.
    """
    # Validate payment amount
    if amount_cents <= 0:
        raise ValueError("Payment amount must be positive")

    # Guard against overpayment
    balance = (invoice.total_cents or 0) - (invoice.paid_cents or 0)
    if amount_cents > balance:
        raise ValueError(
            f"Payment of ${amount_cents/100:.2f} exceeds remaining balance of ${balance/100:.2f}"
        )

    # Create payment record
    payment = Payment(
        invoice_id=invoice.id,
        amount_cents=amount_cents,
        method=method,
        reference=reference,
        stripe_payment_intent_id=stripe_payment_intent_id,
        stripe_checkout_session_id=stripe_checkout_session_id,
        payment_date=sydney_today(),
    )
    db.add(payment)

    # Update invoice
    invoice.paid_cents += amount_cents

    if invoice.paid_cents >= invoice.total_cents:
        invoice.status = "paid"
        invoice.paid_date = sydney_today()
    elif invoice.paid_cents > 0:
        invoice.status = "partial"

    # Send receipt email
    email_sent = False
    if send_receipt:
        customer = await db.get(Customer, invoice.customer_id)
        if customer:
            from app.notifications.email import send_payment_receipt_email
            email_sent = await send_payment_receipt_email(db, payment, invoice, customer)

    # Cancel payment reminders if fully paid
    reminders_cancelled = 0
    if invoice.status == "paid":
        try:
            from app.notifications.reminders import cancel_reminders
            reminders_cancelled = await cancel_reminders(db, "invoice", invoice.id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to cancel payment reminders: {e}")

    # Sync payment to Xero (fails gracefully)
    xero_payment_id = None
    try:
        from app.integrations.xero import sync_payment_to_xero
        xero_payment_id = await sync_payment_to_xero(db, payment)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to sync payment to Xero: {e}")

    # Log activity
    activity = ActivityLog(
        action="payment_received",
        description=f"Payment of ${amount_cents/100:.2f} received for {invoice.invoice_number}",
        entity_type="invoice",
        entity_id=invoice.id,
        ip_address=ip_address,
        extra_data={
            "amount_cents": amount_cents,
            "method": method,
            "new_status": invoice.status,
            "payment_intent_id": stripe_payment_intent_id,
            "receipt_email_sent": email_sent,
            "xero_payment_id": xero_payment_id,
            "reminders_cancelled": reminders_cancelled,
        },
    )
    db.add(activity)

    # Create notification
    from app.notifications.service import notify_payment_received
    await notify_payment_received(db, payment, invoice)

    # Trigger payment milestone workflows
    if invoice.quote_id:
        was_first_payment = (invoice.paid_cents == amount_cents)  # This was the very first payment
        if was_first_payment:
            await on_first_payment(db, invoice)

        if invoice.status == "paid":
            await on_fully_paid(db, invoice)

    return payment


async def void_invoice(
    db: AsyncSession,
    invoice: Invoice,
    request: Request,
) -> Invoice:
    """Void an invoice. Also voids in Xero if synced."""
    if invoice.status == "paid":
        raise ValueError("Cannot void a fully paid invoice")

    if (invoice.paid_cents or 0) > 0:
        raise ValueError(
            f"Cannot void invoice with ${invoice.paid_cents/100:.2f} in payments. "
            "Refund or remove payments first."
        )

    invoice.status = "voided"

    # Sync void to Xero (fails gracefully)
    xero_voided = False
    try:
        from app.integrations.xero import void_invoice_in_xero
        xero_voided = await void_invoice_in_xero(db, invoice)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to void invoice in Xero: {e}")

    activity = ActivityLog(
        action="invoice_voided",
        description=f"Invoice {invoice.invoice_number} voided",
        entity_type="invoice",
        entity_id=invoice.id,
        ip_address=request.client.host if request.client else None,
        extra_data={"xero_voided": xero_voided},
    )
    db.add(activity)

    return invoice


# =============================================================================
# PROGRESS PAYMENT FUNCTIONS
# =============================================================================

DEFAULT_PAYMENT_SCHEDULE = [
    {"label": "Deposit", "percent": 30, "due": "on_acceptance"},
    {"label": "Progress Payment", "percent": 60, "due": "before_pour"},
    {"label": "Final Payment", "percent": 10, "due": "on_completion"},
]


def get_payment_schedule(quote: Quote) -> list[dict]:
    """Get the payment schedule for a quote, using defaults if not set.

    Always returns the list-of-dicts format. Converts legacy dict format if needed.
    """
    schedule = quote.payment_schedule
    if not schedule:
        return DEFAULT_PAYMENT_SCHEDULE

    # Convert legacy dict format to list format
    if isinstance(schedule, dict):
        label_map = {"deposit": "Deposit", "prepour": "Progress Payment", "final": "Final Payment"}
        return [
            {"label": label_map.get(stage, stage.title()), "percent": config["percent"], "due": config["due"]}
            for stage, config in schedule.items()
        ]

    return schedule


async def create_job_invoice(
    db: AsyncSession,
    quote: Quote,
    request: Request = None,
    ip_address: Optional[str] = None,
) -> Invoice:
    """
    Create a SINGLE invoice for the full quote amount.

    The invoice includes the full line items from the quote and a payment
    schedule showing milestones (e.g. 30% deposit, 60% progress, 10% final).
    Multiple payments are recorded against this one invoice.

    Returns:
        The created Invoice object
    """
    # Guard against duplicate invoice creation (e.g. double-click on sign button)
    existing = await get_invoices_for_quote(db, quote.id)
    if existing:
        return existing[0]

    # Use the full quote amounts
    subtotal_cents = quote.subtotal_cents
    gst_cents = calculate_gst(subtotal_cents)
    total_cents = subtotal_cents + gst_cents

    # Generate invoice number and portal token
    invoice_number = await get_next_invoice_number(db)
    raw_token, hashed_token = generate_portal_token()

    description = f"Tax Invoice - {quote.quote_number}"

    # Build line items from quote's customer-facing line items
    line_items = []
    if quote.customer_line_items:
        for group in quote.customer_line_items:
            line_items.append({
                "description": group.get("category", "Item"),
                "quantity": 1,
                "unit": "lot",
                "unit_price_cents": group.get("total_cents", 0),
                "total_cents": group.get("total_cents", 0),
            })
    else:
        # Fallback: single line item for full amount
        line_items.append({
            "description": description,
            "quantity": 1,
            "unit": "lot",
            "unit_price_cents": subtotal_cents,
            "total_cents": subtotal_cents,
        })

    # Build payment schedule with calculated amounts
    schedule = get_payment_schedule(quote)
    payment_schedule = []
    running_total = 0
    for i, milestone in enumerate(schedule):
        percent = milestone["percent"]
        if percent <= 0:
            continue
        # Last milestone gets remainder to avoid rounding issues
        if i == len(schedule) - 1:
            milestone_amount = subtotal_cents - running_total
        else:
            milestone_amount = int(round(subtotal_cents * percent / 100))
            running_total += milestone_amount

        milestone_gst = calculate_gst(milestone_amount)
        payment_schedule.append({
            "label": milestone.get("label", f"Payment {i+1}"),
            "percent": percent,
            "amount_cents": milestone_amount + milestone_gst,  # inc GST
            "due": milestone.get("due", "on_acceptance"),
        })

    # Create invoice
    invoice = Invoice(
        invoice_number=invoice_number,
        quote_id=quote.id,
        customer_id=quote.customer_id,
        description=description,
        stage="progress",
        line_items=line_items,
        payment_schedule=payment_schedule,
        subtotal_cents=subtotal_cents,
        gst_cents=gst_cents,
        total_cents=total_cents,
        paid_cents=0,
        status="draft",
        issue_date=sydney_today(),
        due_date=sydney_today() + timedelta(days=7),  # First payment due in 7 days
        portal_token=hashed_token,
    )
    db.add(invoice)
    await db.flush()

    # Update quote's total_invoiced_cents
    quote.total_invoiced_cents = total_cents

    # Log activity
    activity = ActivityLog(
        action="invoice_created",
        description=f"Created invoice {invoice_number} for {quote.quote_number} (${total_cents/100:,.2f} inc GST)",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=ip_address or (request.client.host if request and request.client else None),
        extra_data={
            "invoice_id": invoice.id,
            "total_cents": total_cents,
            "milestones": len(payment_schedule),
        },
    )
    db.add(activity)

    return invoice


# Backward-compat alias (old code may call this)
async def create_progress_invoices(
    db: AsyncSession,
    quote: Quote,
    request: Request = None,
    ip_address: Optional[str] = None,
) -> list[Invoice]:
    """Backward-compatible wrapper — creates single invoice, returns as list."""
    invoice = await create_job_invoice(db, quote, request, ip_address)
    return [invoice]


async def get_invoice_by_stage(
    db: AsyncSession,
    quote_id: int,
    stage: str,
) -> Optional[Invoice]:
    """Get an invoice for a specific quote and stage."""
    result = await db.execute(
        select(Invoice)
        .where(Invoice.quote_id == quote_id)
        .where(Invoice.stage == stage)
        .where(Invoice.status != "voided")
    )
    return result.scalar_one_or_none()


async def get_invoices_for_quote(
    db: AsyncSession,
    quote_id: int,
) -> list[Invoice]:
    """Get all non-voided invoices for a quote, ordered by stage."""
    stage_order = {"progress": 0, "deposit": 1, "booking": 1, "prepour": 2, "final": 3, "completion": 3}

    result = await db.execute(
        select(Invoice)
        .where(Invoice.quote_id == quote_id)
        .where(Invoice.status != "voided")
    )
    invoices = list(result.scalars().all())

    # Sort by stage order
    invoices.sort(key=lambda inv: stage_order.get(inv.stage, 99))

    return invoices


async def update_quote_payment_totals(
    db: AsyncSession,
    quote: Quote,
) -> None:
    """
    Update the cached payment totals on a quote.

    Should be called after recording payments or creating invoices.
    """
    invoices = await get_invoices_for_quote(db, quote.id)

    quote.total_invoiced_cents = sum(inv.total_cents for inv in invoices)
    quote.total_paid_cents = sum(inv.paid_cents for inv in invoices)


async def get_invoices_by_payment_stage(
    db: AsyncSession,
    stage_filter: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Invoice], int]:
    """
    Get invoices filtered by payment status.

    stage_filter can be:
    - 'awaiting_deposit' / 'unpaid': invoices sent but not yet paid at all
    - 'awaiting_prepour' / 'partial': invoices that are partially paid
    - 'awaiting_final': (legacy, returns empty) - no longer used
    - 'overdue': any invoice that is overdue
    - 'paid': any invoice that is paid
    - 'draft': draft invoices
    - None: all invoices
    """
    offset = (page - 1) * page_size

    # Base query
    query = select(Invoice).order_by(Invoice.created_at.desc())
    count_query = select(func.count(Invoice.id))

    # Apply status filters (remapped from old stage-based to status-based)
    if stage_filter in ("awaiting_deposit", "unpaid"):
        # Unpaid: sent/viewed invoices with no payments yet
        query = query.where(
            Invoice.status.in_(["sent", "viewed"]),
            Invoice.paid_cents == 0,
        )
        count_query = count_query.where(
            Invoice.status.in_(["sent", "viewed"]),
            Invoice.paid_cents == 0,
        )
    elif stage_filter in ("awaiting_prepour", "partial"):
        # Partial: invoices with some payment but not fully paid
        query = query.where(Invoice.status == "partial")
        count_query = count_query.where(Invoice.status == "partial")
    elif stage_filter == "awaiting_final":
        # Legacy: no longer applicable, return empty set
        query = query.where(Invoice.id == -1)
        count_query = count_query.where(Invoice.id == -1)
    elif stage_filter == "overdue":
        query = query.where(Invoice.status == "overdue")
        count_query = count_query.where(Invoice.status == "overdue")
    elif stage_filter == "paid":
        query = query.where(Invoice.status == "paid")
        count_query = count_query.where(Invoice.status == "paid")
    elif stage_filter == "draft":
        query = query.where(Invoice.status == "draft")
        count_query = count_query.where(Invoice.status == "draft")

    # Execute
    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(query.offset(offset).limit(page_size))
    invoices = result.scalars().all()

    return list(invoices), total


async def get_jobs_with_invoices(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """
    Get quotes/jobs with their invoices grouped together.

    Returns a list of dicts with quote info and nested invoices.
    """
    # Get quotes that have invoices
    offset = (page - 1) * page_size

    # Count total quotes with invoices
    count_query = select(func.count(func.distinct(Invoice.quote_id))).where(
        Invoice.quote_id.isnot(None)
    )
    total = (await db.execute(count_query)).scalar() or 0

    # Get distinct quote IDs with invoices
    quote_ids_query = (
        select(Invoice.quote_id)
        .where(Invoice.quote_id.isnot(None))
        .distinct()
        .order_by(Invoice.quote_id.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(quote_ids_query)
    quote_ids = [row[0] for row in result.fetchall()]

    jobs = []
    for quote_id in quote_ids:
        quote = await db.get(Quote, quote_id)
        if not quote:
            continue

        customer = await db.get(Customer, quote.customer_id)
        invoices = await get_invoices_for_quote(db, quote_id)

        # Calculate totals
        total_invoiced = sum(inv.total_cents for inv in invoices)
        total_paid = sum(inv.paid_cents for inv in invoices)

        jobs.append({
            "quote": quote,
            "customer": customer,
            "invoices": invoices,
            "total_invoiced_cents": total_invoiced,
            "total_paid_cents": total_paid,
            "payment_progress_percent": int((total_paid / total_invoiced * 100) if total_invoiced > 0 else 0),
        })

    return jobs, total


# =============================================================================
# WORKFLOW HANDLERS
# =============================================================================

async def on_quote_accepted(
    db: AsyncSession,
    quote: Quote,
    request: Request,
) -> list[Invoice]:
    """
    Handle quote acceptance - create single job invoice and send it.
    """
    invoice = await create_job_invoice(db, quote, request)

    if invoice.status == "draft":
        await send_invoice(db, invoice, request)

        from app.models import Notification
        notification = Notification(
            type="invoice_sent",
            title="Invoice Sent",
            message=f"Invoice for {quote.quote_number} sent to customer",
            quote_id=quote.id,
            invoice_id=invoice.id,
        )
        db.add(notification)

    return [invoice]


async def on_first_payment(
    db: AsyncSession,
    invoice: Invoice,
) -> None:
    """
    Handle first payment received — auto-transitions quote to 'accepted'.

    This is the first payment against the single job invoice.
    """
    if not invoice.quote_id:
        return

    quote = await db.get(Quote, invoice.quote_id)
    if not quote:
        return

    # Auto-transition: sent/viewed → accepted (first payment = accepted awaiting date confirmation)
    if quote.status in ("sent", "viewed"):
        quote.status = "accepted"
        quote.accepted_at = sydney_now()

    # Update quote payment totals
    await update_quote_payment_totals(db, quote)

    # Create notification
    from app.models import Notification
    customer = await db.get(Customer, quote.customer_id)
    if customer:
        from app.core.security import decrypt_customer_pii
        decrypt_customer_pii(customer)
    customer_name = customer.name if customer else "Unknown"

    paid_amount = invoice.paid_cents / 100
    notification = Notification(
        type="payment_received",
        title="First Payment Received — Ready to Confirm Date",
        message=f"${paid_amount:,.2f} received for {customer_name} ({quote.quote_number}). Confirm the start date to lock in the job.",
        quote_id=quote.id,
        invoice_id=invoice.id,
        priority="high",
    )
    db.add(notification)

    # Log activity
    activity = ActivityLog(
        action="first_payment_received",
        description=f"First payment received for {quote.quote_number}. Status → accepted (awaiting date confirmation).",
        entity_type="quote",
        entity_id=quote.id,
        extra_data={"invoice_id": invoice.id, "paid_cents": invoice.paid_cents, "new_status": quote.status},
    )
    db.add(activity)


# Backward-compat alias
async def on_deposit_paid(db: AsyncSession, invoice: Invoice) -> None:
    """Legacy alias for on_first_payment."""
    await on_first_payment(db, invoice)


async def on_job_scheduled(
    db: AsyncSession,
    quote: Quote,
    pour_date,
    request: Request = None,
    ip_address: Optional[str] = None,
) -> Optional[Invoice]:
    """
    Handle job scheduling — update due date and transition quote.

    With single-invoice model, we just update the due date on the existing
    invoice (no separate prepour invoice to send).
    """
    invoices = await get_invoices_for_quote(db, quote.id)
    invoice = invoices[0] if invoices else None

    if not invoice:
        return None

    # Auto-transition: confirmed → pour_stage
    if quote.status == "confirmed":
        quote.status = "pour_stage"

    # Log activity
    _ip = ip_address or (request.client.host if request and request.client else None)
    activity = ActivityLog(
        action="job_scheduled",
        description=f"Job scheduled for {quote.quote_number}. Pour date: {pour_date}. Status → pour_stage.",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=_ip,
        extra_data={"invoice_id": invoice.id, "pour_date": str(pour_date), "new_status": quote.status},
    )
    db.add(activity)

    return invoice


async def on_job_completed(
    db: AsyncSession,
    quote: Quote,
    request: Request = None,
    ip_address: Optional[str] = None,
) -> Optional[Invoice]:
    """
    Handle job completion. With single-invoice model, just transitions the quote.
    """
    invoices = await get_invoices_for_quote(db, quote.id)
    invoice = invoices[0] if invoices else None

    # Auto-transition: pour_stage → pending_completion
    if quote.status == "pour_stage":
        quote.status = "pending_completion"

    # Log activity
    _ip = ip_address or (request.client.host if request and request.client else None)
    activity = ActivityLog(
        action="job_completed_stage",
        description=f"Job marked complete for {quote.quote_number}. Status → pending_completion.",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=_ip,
        extra_data={"invoice_id": invoice.id if invoice else None, "new_status": quote.status},
    )
    db.add(activity)

    return invoice


async def on_fully_paid(
    db: AsyncSession,
    invoice: Invoice,
) -> None:
    """
    Handle invoice fully paid — auto-transitions quote to 'completed'.
    """
    if not invoice.quote_id:
        return

    quote = await db.get(Quote, invoice.quote_id)
    if not quote:
        return

    # Auto-transition to completed
    if quote.status in ("pending_completion", "pour_stage", "confirmed", "accepted"):
        quote.status = "completed"
        quote.completed_date = sydney_today()

    # Update quote payment totals
    await update_quote_payment_totals(db, quote)

    # Create notification
    from app.models import Notification
    customer = await db.get(Customer, quote.customer_id)
    if customer:
        from app.core.security import decrypt_customer_pii
        decrypt_customer_pii(customer)
    customer_name = customer.name if customer else "Unknown"

    notification = Notification(
        type="job_completed",
        title="Job Completed & Paid in Full",
        message=f"Job {quote.quote_number} for {customer_name} is fully paid and complete.",
        quote_id=quote.id,
        invoice_id=invoice.id,
        priority="normal",
    )
    db.add(notification)

    # Log activity
    activity = ActivityLog(
        action="job_completed",
        description=f"Job {quote.quote_number} completed — fully paid.",
        entity_type="quote",
        entity_id=quote.id,
        extra_data={"invoice_id": invoice.id, "new_status": "completed"},
    )
    db.add(activity)

    # Send job complete email to customer (non-blocking)
    if customer:
        try:
            from app.notifications.email import send_job_complete_email
            await send_job_complete_email(
                db=db,
                quote=quote,
                customer=customer,
                portal_url="",
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to send job complete email: {e}")


# Backward-compat alias
async def on_final_paid(db: AsyncSession, invoice: Invoice) -> None:
    """Legacy alias for on_fully_paid."""
    await on_fully_paid(db, invoice)


async def get_payment_summary_stats(db: AsyncSession) -> dict:
    """
    Get summary statistics for the dashboard.

    Returns counts and totals for different invoice states.
    """
    # Total outstanding
    outstanding_query = select(
        func.sum(Invoice.total_cents - Invoice.paid_cents)
    ).where(
        Invoice.status.in_(["sent", "viewed", "partial", "overdue"])
    )
    outstanding_total = (await db.execute(outstanding_query)).scalar() or 0

    # Unpaid invoices (sent but no payments yet)
    unpaid_count_query = select(func.count(Invoice.id)).where(
        Invoice.status.in_(["sent", "viewed"]),
        Invoice.paid_cents == 0,
    )
    unpaid_count = (await db.execute(unpaid_count_query)).scalar() or 0

    # Partially paid invoices
    partial_count_query = select(func.count(Invoice.id)).where(
        Invoice.status == "partial",
    )
    partial_count = (await db.execute(partial_count_query)).scalar() or 0

    # Paid invoices
    paid_count_query = select(func.count(Invoice.id)).where(
        Invoice.status == "paid",
    )
    paid_count = (await db.execute(paid_count_query)).scalar() or 0

    # Overdue count
    overdue_count_query = select(func.count(Invoice.id)).where(
        Invoice.status == "overdue"
    )
    overdue_count = (await db.execute(overdue_count_query)).scalar() or 0

    return {
        "outstanding_total_cents": outstanding_total,
        "unpaid_count": unpaid_count,
        "partial_count": partial_count,
        "paid_count": paid_count,
        "overdue_count": overdue_count,
    }
