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

    if stage == "booking":
        return split["booking"]  # 30%
    elif stage == "prepour":
        return split["prepour"]  # 60%
    elif stage == "completion":
        return split["completion"]  # 10%
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
    if stage not in ("booking", "prepour", "completion"):
        raise ValueError(f"Invalid stage: {stage}. Must be booking, prepour, or completion.")

    # Calculate amount for this stage (use subtotal ex-GST, not total inc-GST)
    subtotal_cents = calculate_stage_amount(quote.subtotal_cents, stage)

    # Determine description
    stage_names = {
        "booking": "First Payment (30%)",
        "prepour": "Progress Payment (60%)",
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

    # Check if this was a deposit payment — trigger workflow
    if invoice.stage == "booking" and invoice.status == "paid":
        from app.invoices.service import on_deposit_paid
        await on_deposit_paid(db, invoice)

    # Check if this was the final payment — auto-complete the job
    if invoice.stage in ("final", "completion") and invoice.status == "paid":
        from app.invoices.service import on_final_paid
        await on_final_paid(db, invoice)

    return payment


async def void_invoice(
    db: AsyncSession,
    invoice: Invoice,
    request: Request,
) -> Invoice:
    """Void an invoice. Also voids in Xero if synced."""
    if invoice.status == "paid":
        raise ValueError("Cannot void a fully paid invoice")

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

DEFAULT_PAYMENT_SCHEDULE = {
    "deposit": {"percent": 30, "due": "on_acceptance"},
    "prepour": {"percent": 60, "due": "before_pour"},
    "final": {"percent": 10, "due": "on_completion"},
}

STAGE_SUFFIXES = {
    "deposit": "A",
    "prepour": "B",
    "final": "C",
}


def get_payment_schedule(quote: Quote) -> dict:
    """Get the payment schedule for a quote, using defaults if not set."""
    return quote.payment_schedule or DEFAULT_PAYMENT_SCHEDULE


async def create_progress_invoices(
    db: AsyncSession,
    quote: Quote,
    request: Request = None,
    ip_address: Optional[str] = None,
) -> list[Invoice]:
    """
    Create all progress payment invoices for an accepted quote.

    Creates invoices for deposit (30%), pre-pour (60%), and final (10%)
    based on the quote's payment_schedule configuration.

    Returns:
        list of created Invoice objects
    """
    # Guard against duplicate invoice creation (e.g. double-click on sign button)
    existing = await get_invoices_for_quote(db, quote.id)
    if existing:
        return existing

    schedule = get_payment_schedule(quote)
    invoices = []

    for stage, config in schedule.items():
        percent = config["percent"]
        if percent <= 0:
            continue

        # Calculate amounts based on percentage
        # Use subtotal (ex GST) for calculation, then add GST
        subtotal_cents = int(round(quote.subtotal_cents * percent / 100))
        gst_cents = calculate_gst(subtotal_cents)
        total_cents = subtotal_cents + gst_cents

        # Generate invoice number with stage suffix
        base_number = await get_next_invoice_number(db)
        suffix = STAGE_SUFFIXES.get(stage, "")
        # Note: We use the base number since get_next_invoice_number already increments
        # Just add suffix to differentiate stages visually

        # Generate portal token
        raw_token, hashed_token = generate_portal_token()

        # Determine description
        stage_names = {
            "deposit": f"First Payment ({percent}%)",
            "prepour": f"Progress Payment ({percent}%)",
            "final": f"Final Payment ({percent}%)",
        }
        description = f"{stage_names.get(stage, stage.title())} - {quote.quote_number}"

        # Determine due date based on schedule config
        due_date = None
        issue_date = None
        initial_status = "draft"

        if config["due"] == "on_acceptance":
            # First payment: due 7 days from now, sent immediately
            due_date = sydney_today() + timedelta(days=7)
            issue_date = sydney_today()
            initial_status = "draft"  # Will be sent separately
        elif config["due"] == "before_pour":
            # Pre-pour: due 3 days before pour date (set when scheduled)
            if quote.confirmed_start_date:
                due_date = quote.confirmed_start_date - timedelta(days=3)
            # Leave as draft until job is scheduled
        elif config["due"] == "on_completion":
            # Final: due 7 days after completion (set when completed)
            # Leave as draft until job is completed
            pass
        else:
            # Numeric value = days from now
            try:
                days = int(config["due"])
                due_date = sydney_today() + timedelta(days=days)
            except (ValueError, TypeError):
                due_date = sydney_today() + timedelta(days=7)

        # Build line items
        line_items = [{
            "description": description,
            "quantity": 1,
            "unit": "each",
            "unit_price_cents": subtotal_cents,
            "total_cents": subtotal_cents,
        }]

        # Create invoice
        invoice = Invoice(
            invoice_number=base_number,
            quote_id=quote.id,
            customer_id=quote.customer_id,
            description=description,
            stage=stage,
            stage_percent=percent,
            line_items=line_items,
            subtotal_cents=subtotal_cents,
            gst_cents=gst_cents,
            total_cents=total_cents,
            paid_cents=0,
            status=initial_status,
            issue_date=issue_date,
            due_date=due_date,
            portal_token=hashed_token,
        )
        db.add(invoice)
        invoices.append(invoice)

    await db.flush()

    # Update quote's total_invoiced_cents
    quote.total_invoiced_cents = sum(inv.total_cents for inv in invoices)

    # Log activity
    activity = ActivityLog(
        action="progress_invoices_created",
        description=f"Created {len(invoices)} progress invoices for {quote.quote_number}",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=ip_address or (request.client.host if request and request.client else None),
        extra_data={
            "invoice_ids": [inv.id for inv in invoices],
            "total_invoiced_cents": quote.total_invoiced_cents,
        },
    )
    db.add(activity)

    return invoices


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
    stage_order = {"deposit": 1, "booking": 1, "prepour": 2, "final": 3, "completion": 3}

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
    Get invoices filtered by payment stage status.

    stage_filter can be:
    - 'awaiting_deposit': deposit invoices that are sent but not paid
    - 'awaiting_prepour': prepour invoices that are sent but not paid
    - 'awaiting_final': final/completion invoices that are sent but not paid
    - 'overdue': any invoice that is overdue
    - 'paid': any invoice that is paid
    - None: all invoices
    """
    offset = (page - 1) * page_size

    # Base query
    query = select(Invoice).order_by(Invoice.created_at.desc())
    count_query = select(func.count(Invoice.id))

    # Apply stage filters
    if stage_filter == "awaiting_deposit":
        query = query.where(
            Invoice.stage.in_(["deposit", "booking"]),
            Invoice.status.in_(["sent", "viewed"]),
        )
        count_query = count_query.where(
            Invoice.stage.in_(["deposit", "booking"]),
            Invoice.status.in_(["sent", "viewed"]),
        )
    elif stage_filter == "awaiting_prepour":
        query = query.where(
            Invoice.stage == "prepour",
            Invoice.status.in_(["sent", "viewed"]),
        )
        count_query = count_query.where(
            Invoice.stage == "prepour",
            Invoice.status.in_(["sent", "viewed"]),
        )
    elif stage_filter == "awaiting_final":
        query = query.where(
            Invoice.stage.in_(["final", "completion"]),
            Invoice.status.in_(["sent", "viewed"]),
        )
        count_query = count_query.where(
            Invoice.stage.in_(["final", "completion"]),
            Invoice.status.in_(["sent", "viewed"]),
        )
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
    Handle quote acceptance - create progress invoices.

    This is called when a quote transitions to 'accepted' status.
    Creates all progress invoices but only sends the first payment invoice.
    """
    # Create all progress invoices
    invoices = await create_progress_invoices(db, quote, request)

    # Find and send the first payment invoice immediately
    deposit_invoice = next(
        (inv for inv in invoices if inv.stage in ("deposit", "booking")),
        None
    )

    if deposit_invoice:
        raw_token = await send_invoice(db, deposit_invoice, request)

        # Notify about first payment invoice
        from app.models import Notification
        notification = Notification(
            type="deposit_invoice_sent",
            title="First Payment Invoice Sent",
            message=f"First payment invoice for {quote.quote_number} sent to customer",
            quote_id=quote.id,
            invoice_id=deposit_invoice.id,
        )
        db.add(notification)

    return invoices


async def on_deposit_paid(
    db: AsyncSession,
    invoice: Invoice,
) -> None:
    """
    Handle first payment received — auto-transitions quote to 'accepted' (awaiting date confirmation).

    Updates quote status and creates notification.
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

    notification = Notification(
        type="deposit_received",
        title="💰 First Payment Received — Ready to Confirm Date",
        message=f"${invoice.total_cents/100:.2f} first payment received for {customer_name}. Confirm the start date to lock in the job.",
        quote_id=quote.id,
        invoice_id=invoice.id,
        priority="high",
    )
    db.add(notification)

    # Log activity
    activity = ActivityLog(
        action="deposit_paid",
        description=f"First payment received for {quote.quote_number}. Status → accepted (awaiting date confirmation).",
        entity_type="quote",
        entity_id=quote.id,
        extra_data={"invoice_id": invoice.id, "amount_cents": invoice.total_cents, "new_status": quote.status},
    )
    db.add(activity)


async def on_job_scheduled(
    db: AsyncSession,
    quote: Quote,
    pour_date,
    request: Request = None,
    ip_address: Optional[str] = None,
) -> Optional[Invoice]:
    """
    Send pre-pour invoice when job is scheduled.
    Auto-transitions quote: confirmed → pour_stage.

    Args:
        quote: The quote/job being scheduled
        pour_date: The confirmed pour/start date
        request: FastAPI request for logging (optional)
        ip_address: Alternative to request for IP logging
    """
    prepour_invoice = await get_invoice_by_stage(db, quote.id, "prepour")

    if prepour_invoice and prepour_invoice.status == "draft":
        # Set due date to 3 days before pour
        prepour_invoice.due_date = pour_date - timedelta(days=3)
        prepour_invoice.issue_date = sydney_today()

        # Send the invoice
        await send_invoice(db, prepour_invoice, request=request, ip_address=ip_address)

        # Auto-transition: confirmed → pour_stage
        if quote.status == "confirmed":
            quote.status = "pour_stage"

        # Log activity
        _ip = ip_address or (request.client.host if request and request.client else None)
        activity = ActivityLog(
            action="prepour_invoice_sent",
            description=f"Pre-pour invoice sent for {quote.quote_number}. Status → pour_stage.",
            entity_type="quote",
            entity_id=quote.id,
            ip_address=_ip,
            extra_data={"invoice_id": prepour_invoice.id, "pour_date": str(pour_date), "new_status": quote.status},
        )
        db.add(activity)

        return prepour_invoice

    return None


async def on_job_completed(
    db: AsyncSession,
    quote: Quote,
    request: Request = None,
    ip_address: Optional[str] = None,
) -> Optional[Invoice]:
    """
    Send final 10% invoice when job is marked complete.
    Auto-transitions quote: pour_stage → pending_completion.
    """
    final_invoice = await get_invoice_by_stage(db, quote.id, "final")

    # Also check for "completion" stage (legacy)
    if not final_invoice:
        final_invoice = await get_invoice_by_stage(db, quote.id, "completion")

    if final_invoice and final_invoice.status == "draft":
        # Set due date to 7 days from now
        final_invoice.due_date = sydney_today() + timedelta(days=7)
        final_invoice.issue_date = sydney_today()

        # Send the invoice
        await send_invoice(db, final_invoice, request=request, ip_address=ip_address)

        # Auto-transition: pour_stage → pending_completion
        if quote.status == "pour_stage":
            quote.status = "pending_completion"

        # Log activity
        _ip = ip_address or (request.client.host if request and request.client else None)
        activity = ActivityLog(
            action="final_invoice_sent",
            description=f"Final invoice sent for {quote.quote_number}. Status → pending_completion.",
            entity_type="quote",
            entity_id=quote.id,
            ip_address=_ip,
            extra_data={"invoice_id": final_invoice.id, "new_status": quote.status},
        )
        db.add(activity)

        return final_invoice

    return None


async def on_final_paid(
    db: AsyncSession,
    invoice: Invoice,
) -> None:
    """
    Handle final payment — auto-transitions quote to 'completed'.

    Called when the final/completion stage invoice is fully paid.
    """
    if not invoice.quote_id:
        return

    quote = await db.get(Quote, invoice.quote_id)
    if not quote:
        return

    # Auto-transition: pending_completion → completed
    if quote.status == "pending_completion":
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
        title="✅ Job Completed & Paid in Full",
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
                portal_url="",  # No outstanding invoice since fully paid
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to send job complete email: {e}")


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

    # Awaiting deposit count
    deposit_count_query = select(func.count(Invoice.id)).where(
        Invoice.stage.in_(["deposit", "booking"]),
        Invoice.status.in_(["sent", "viewed"]),
    )
    awaiting_deposit_count = (await db.execute(deposit_count_query)).scalar() or 0

    # Awaiting pre-pour count
    prepour_count_query = select(func.count(Invoice.id)).where(
        Invoice.stage == "prepour",
        Invoice.status.in_(["sent", "viewed"]),
    )
    awaiting_prepour_count = (await db.execute(prepour_count_query)).scalar() or 0

    # Awaiting final count
    final_count_query = select(func.count(Invoice.id)).where(
        Invoice.stage.in_(["final", "completion"]),
        Invoice.status.in_(["sent", "viewed"]),
    )
    awaiting_final_count = (await db.execute(final_count_query)).scalar() or 0

    # Overdue count
    overdue_count_query = select(func.count(Invoice.id)).where(
        Invoice.status == "overdue"
    )
    overdue_count = (await db.execute(overdue_count_query)).scalar() or 0

    return {
        "outstanding_total_cents": outstanding_total,
        "awaiting_deposit_count": awaiting_deposit_count,
        "awaiting_prepour_count": awaiting_prepour_count,
        "awaiting_final_count": awaiting_final_count,
        "overdue_count": overdue_count,
    }
