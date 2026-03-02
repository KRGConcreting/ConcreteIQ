"""
Invoice routes - CRUD and PDF generation.

Follows the established pattern from customers/routes.py.
"""

import logging
from datetime import date

logger = logging.getLogger(__name__)
from typing import Optional
from urllib.parse import quote as url_quote
from fastapi import APIRouter, Depends, HTTPException, Request, Query, Form
from fastapi.responses import Response, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Invoice, Customer, Quote, Payment, ActivityLog
from app.schemas import (
    InvoiceCreate, InvoiceResponse,
    PaginatedResponse, SuccessResponse
)
from app.core.auth import require_login, verify_csrf
from app.core.templates import templates
from app.config import settings
from app.invoices import service
from app.quotes.pdf import generate_invoice_pdf, generate_receipt_pdf
from app.core.security import decrypt_customer_pii

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


# =============================================================================
# HTML PAGES
# =============================================================================

@router.get("", name="invoices:list")
async def invoice_list_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: Optional[str] = None,
    view: str = Query("list", pattern="^(list|jobs)$"),
    page: int = Query(1, ge=1),
):
    """
    Invoice list page.

    Supports two views:
    - list: Traditional flat list of invoices
    - jobs: Grouped by job/quote with progress tracking
    """
    page_size = 20

    # Get payment summary stats for the header
    stats = await service.get_payment_summary_stats(db)

    if view == "jobs":
        # Grouped by job view
        jobs, total = await service.get_jobs_with_invoices(db, page=page, page_size=page_size)

        return templates.TemplateResponse("invoices/list_jobs.html", {
            "request": request,
            "jobs": jobs,
            "status_filter": status,
            "view": view,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": (total + page_size - 1) // page_size if total > 0 else 0,
            "stats": stats,
        })

    # Standard list view with stage filters
    invoices, total = await service.get_invoices_by_payment_stage(
        db, stage_filter=status, page=page, page_size=page_size
    )

    # Load customers for display
    customer_ids = {inv.customer_id for inv in invoices}
    customers = {}
    for cid in customer_ids:
        customer = await db.get(Customer, cid)
        if customer:
            decrypt_customer_pii(customer)
            customers[cid] = customer

    # Load quotes for display
    quote_ids = {inv.quote_id for inv in invoices if inv.quote_id}
    quotes = {}
    for qid in quote_ids:
        quote = await db.get(Quote, qid)
        if quote:
            quotes[qid] = quote

    return templates.TemplateResponse("invoices/list.html", {
        "request": request,
        "invoices": invoices,
        "customers": customers,
        "quotes": quotes,
        "status_filter": status,
        "view": view,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
        "stats": stats,
    })


@router.get("/new", name="invoices:new")
async def invoice_new_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    quote_id: Optional[int] = None,
):
    """New invoice form - select customer/quote or create standalone."""
    # Get customers for dropdown
    customers_result = await db.execute(
        select(Customer).order_by(Customer.name)
    )
    customers = customers_result.scalars().all()

    # Get accepted quotes that don't have all invoices yet
    quotes_result = await db.execute(
        select(Quote)
        .where(Quote.status.in_(["accepted", "confirmed", "pour_stage", "pending_completion"]))
        .order_by(Quote.created_at.desc())
        .limit(50)
    )
    quotes = quotes_result.scalars().all()

    # If quote_id provided, preload that quote
    selected_quote = None
    if quote_id:
        selected_quote = await db.get(Quote, quote_id)

    return templates.TemplateResponse("invoices/new.html", {
        "request": request,
        "customers": customers,
        "quotes": quotes,
        "selected_quote": selected_quote,
    })


@router.post("/manual", name="invoices:create_manual")
async def create_manual_invoice(
    request: Request,
    db: AsyncSession = Depends(get_db),
    customer_id: int = Form(...),
    description: str = Form(...),
    amount: float = Form(...),
    due_date: date = Form(...),
    notes: Optional[str] = Form(None),
):
    """Create a manual invoice (not from a quote)."""
    # Verify customer exists
    customer = await db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(400, "Customer not found")

    # Create invoice data
    from app.core.money import dollars_to_cents, calculate_gst
    subtotal_cents = dollars_to_cents(amount)
    gst_cents = calculate_gst(subtotal_cents)  # 10% GST

    from app.schemas import InvoiceCreate
    invoice_data = InvoiceCreate(
        customer_id=customer_id,
        description=description,
        stage="manual",
        subtotal_cents=subtotal_cents,
        due_date=due_date,
        notes=notes,
    )

    try:
        invoice, raw_token = await service.create_invoice(db, invoice_data, request)

        # Log activity
        activity = ActivityLog(
            action="invoice_created",
            description=f"Manual invoice {invoice.invoice_number} created for {customer.name}",
            entity_type="invoice",
            entity_id=invoice.id,
            extra_data={"customer_id": customer_id},
            ip_address=request.client.host if request.client else None,
        )
        db.add(activity)

        await db.commit()
        await db.refresh(invoice)

        return RedirectResponse(f"/invoices/{invoice.id}", status_code=302)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{id}", name="invoices:detail")
async def invoice_detail_page(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Invoice detail page."""
    invoice = await service.get_invoice(db, id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    customer = await db.get(Customer, invoice.customer_id)
    if customer:
        decrypt_customer_pii(customer)
    quote = await db.get(Quote, invoice.quote_id) if invoice.quote_id else None

    # Get payments
    payments_result = await db.execute(
        select(Payment)
        .where(Payment.invoice_id == id)
        .order_by(Payment.created_at.desc())
    )
    payments = payments_result.scalars().all()

    # Get activity
    activity_result = await db.execute(
        select(ActivityLog)
        .where(ActivityLog.entity_type == "invoice")
        .where(ActivityLog.entity_id == id)
        .order_by(ActivityLog.created_at.desc())
        .limit(20)
    )
    activity = activity_result.scalars().all()

    return templates.TemplateResponse("invoices/detail.html", {
        "request": request,
        "invoice": invoice,
        "customer": customer,
        "quote": quote,
        "payments": payments,
        "activity": activity,
        "balance_cents": invoice.total_cents - invoice.paid_cents,
        "stripe_enabled": bool(settings.stripe_secret_key),
    })


# =============================================================================
# API ENDPOINTS
# =============================================================================

@router.get("/api/list")
async def api_list_invoices(
    db: AsyncSession = Depends(get_db),
    status: Optional[str] = None,
    customer_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PaginatedResponse:
    """API: List invoices with pagination."""
    invoices, total = await service.get_invoices(
        db, status, customer_id, page, page_size
    )

    items = []
    for inv in invoices:
        items.append({
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "customer_id": inv.customer_id,
            "quote_id": inv.quote_id,
            "description": inv.description,
            "stage": inv.stage,
            "subtotal_cents": inv.subtotal_cents,
            "gst_cents": inv.gst_cents,
            "total_cents": inv.total_cents,
            "paid_cents": inv.paid_cents,
            "balance_cents": inv.total_cents - inv.paid_cents,
            "status": inv.status,
            "issue_date": inv.issue_date,
            "due_date": inv.due_date,
            "created_at": inv.created_at,
        })

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size if total > 0 else 0,
    )


@router.post("/api/create")
async def api_create_invoice(
    request: Request,
    data: InvoiceCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """API: Create a new invoice."""
    try:
        invoice, raw_token = await service.create_invoice(db, data, request)
        await db.commit()
        await db.refresh(invoice)

        return {
            "id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "status": invoice.status,
            "subtotal_cents": invoice.subtotal_cents,
            "gst_cents": invoice.gst_cents,
            "total_cents": invoice.total_cents,
            "portal_token": raw_token,
            "portal_url": f"{settings.app_url}/p/invoice/{raw_token}",
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/create-from-quote/{quote_id}")
async def api_create_from_quote(
    request: Request,
    quote_id: int,
    stage: str = Query("progress", pattern="^(progress|variation|manual)$"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    API: Create invoice from quote.

    Creates a single job invoice for the full quote amount.
    """
    quote = await db.get(Quote, quote_id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    if quote.status not in ("accepted", "confirmed", "completed"):
        raise HTTPException(400, "Quote must be accepted to create invoices")

    try:
        invoice = await service.create_job_invoice(db, quote, request)
        await db.commit()
        await db.refresh(invoice)

        return {
            "id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "stage": invoice.stage,
            "status": invoice.status,
            "subtotal_cents": invoice.subtotal_cents,
            "gst_cents": invoice.gst_cents,
            "total_cents": invoice.total_cents,
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/{id}")
async def api_get_invoice(
    id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """API: Get single invoice."""
    invoice = await service.get_invoice(db, id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    customer = await db.get(Customer, invoice.customer_id)
    if customer:
        decrypt_customer_pii(customer)

    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "quote_id": invoice.quote_id,
        "customer_id": invoice.customer_id,
        "customer_name": customer.name if customer else None,
        "description": invoice.description,
        "stage": invoice.stage,
        "line_items": invoice.line_items,
        "subtotal_cents": invoice.subtotal_cents,
        "gst_cents": invoice.gst_cents,
        "total_cents": invoice.total_cents,
        "paid_cents": invoice.paid_cents,
        "balance_cents": invoice.total_cents - invoice.paid_cents,
        "status": invoice.status,
        "issue_date": invoice.issue_date,
        "due_date": invoice.due_date,
        "paid_date": invoice.paid_date,
        "notes": invoice.notes,
        "portal_token": invoice.portal_token,
        "created_at": invoice.created_at,
        "updated_at": invoice.updated_at,
    }


@router.post("/api/{id}/send")
async def api_send_invoice(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """API: Mark invoice as sent, return portal URL."""
    invoice = await service.get_invoice(db, id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    try:
        raw_token = await service.send_invoice(db, invoice, request)
        await db.commit()
        await db.refresh(invoice)

        return {
            "id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "status": invoice.status,
            "portal_token": raw_token,
            "portal_url": f"{settings.app_url}/p/invoice/{raw_token}",
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/{id}/resend")
async def api_resend_invoice(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    API: Resend invoice email to customer.

    Works for invoices in sent, viewed, partial, or overdue status.
    Generates a fresh portal token and resends the email.
    Does not change the invoice status.
    """
    invoice = await service.get_invoice(db, id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    if invoice.status not in ("sent", "viewed", "partial", "overdue"):
        raise HTTPException(400, f"Cannot resend an invoice in '{invoice.status}' status")

    customer = await db.get(Customer, invoice.customer_id)
    if not customer:
        raise HTTPException(400, "No customer linked to this invoice")
    decrypt_customer_pii(customer)

    # Generate fresh portal token
    from app.invoices.service import generate_portal_token
    raw_token, hashed_token = generate_portal_token()
    invoice.portal_token = hashed_token
    portal_url = f"{settings.app_url}/p/invoice/{raw_token}"

    # Send email
    from app.notifications.email import send_invoice_email
    email_sent = await send_invoice_email(db, invoice, customer, portal_url)

    # Log activity
    activity = ActivityLog(
        action="invoice_resent",
        description=f"Resent invoice {invoice.invoice_number}",
        entity_type="invoice",
        entity_id=invoice.id,
        ip_address=request.client.host if request.client else None,
        extra_data={"email_sent": email_sent},
    )
    db.add(activity)
    await db.commit()

    return {"success": True, "email_sent": email_sent, "portal_url": portal_url}


@router.post("/api/{id}/resend-receipt")
async def api_resend_receipt(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    API: Resend payment receipt email for the most recent payment on this invoice.

    Works for invoices with at least one payment recorded.
    """
    invoice = await service.get_invoice(db, id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    customer = await db.get(Customer, invoice.customer_id)
    if not customer:
        raise HTTPException(400, "No customer linked to this invoice")
    decrypt_customer_pii(customer)

    # Get most recent payment
    result = await db.execute(
        select(Payment)
        .where(Payment.invoice_id == invoice.id)
        .order_by(Payment.paid_at.desc())
        .limit(1)
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(400, "No payments recorded for this invoice")

    # Get linked quote for context
    quote = await db.get(Quote, invoice.quote_id) if invoice.quote_id else None

    # Send receipt email
    from app.notifications.email import send_payment_receipt_email
    email_sent = await send_payment_receipt_email(
        db, payment, invoice, customer, quote
    )

    # Log activity
    activity = ActivityLog(
        action="receipt_resent",
        description=f"Resent receipt for payment on {invoice.invoice_number}",
        entity_type="invoice",
        entity_id=invoice.id,
        ip_address=request.client.host if request.client else None,
        extra_data={"email_sent": email_sent, "payment_id": payment.id},
    )
    db.add(activity)
    await db.commit()

    return {"success": True, "email_sent": email_sent}


@router.post("/api/{id}/void")
async def api_void_invoice(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    """API: Void an invoice."""
    invoice = await service.get_invoice(db, id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    try:
        await service.void_invoice(db, invoice, request)
        await db.commit()
        return SuccessResponse(message=f"Invoice {invoice.invoice_number} voided")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/{id}/send-reminder")
async def api_send_reminder(
    request: Request,
    id: int,
    tier: str = Query("friendly"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    API: Send payment reminder for an invoice.

    Query param `tier` selects the email tone:
    - friendly: Polite tone (default)
    - firm: Firm tone, mentions late fees
    - final: Final notice, mentions debt collection
    """
    if tier not in ("friendly", "firm", "final"):
        tier = "friendly"

    invoice = await service.get_invoice(db, id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    if invoice.status not in ("sent", "viewed", "partial", "overdue"):
        raise HTTPException(400, f"Cannot send reminder for invoice in '{invoice.status}' status")

    customer = await db.get(Customer, invoice.customer_id)
    if customer:
        decrypt_customer_pii(customer)
    if not customer or not customer.email:
        raise HTTPException(400, "Customer has no email address")

    # Generate fresh portal URL
    raw_token, hashed_token = service.generate_portal_token()
    invoice.portal_token = hashed_token
    portal_url = f"{settings.app_url}/p/invoice/{raw_token}"

    # Calculate days overdue
    days_overdue = 0
    if invoice.due_date:
        from app.core.dates import sydney_today
        today = sydney_today()
        due = invoice.due_date if not hasattr(invoice.due_date, 'date') else invoice.due_date.date()
        delta = (today - due).days
        if delta > 0:
            days_overdue = delta

    # Format amounts
    total_formatted = f"${invoice.total_cents / 100:,.2f}"
    paid_formatted = f"${invoice.paid_cents / 100:,.2f}"
    balance_cents = invoice.total_cents - invoice.paid_cents
    balance_formatted = f"${balance_cents / 100:,.2f}"
    due_date_formatted = invoice.due_date.strftime("%d %B %Y") if invoice.due_date else "On receipt"

    # Build template context
    from app.core.templates import templates
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
        business_licence=settings.licence_number,
        business_address=settings.business_address,
        bank_name=settings.bank_name,
        bank_bsb=settings.bank_bsb,
        bank_account=settings.bank_account,
    )

    # Select template & subject by tier
    tier_labels = {
        "friendly": "Friendly Reminder",
        "firm": "Payment Overdue",
        "final": "FINAL NOTICE",
    }

    if tier == "friendly":
        template_name = "emails/payment_reminder_friendly.html"
        subject = f"Friendly Reminder — Invoice {invoice.invoice_number}"
    elif tier == "firm":
        template_name = "emails/payment_reminder_firm.html"
        subject = f"Payment Overdue — Invoice {invoice.invoice_number}"
    else:  # final
        template_name = "emails/payment_reminder_final.html"
        subject = f"FINAL NOTICE — Invoice {invoice.invoice_number}"

    # Render template
    try:
        html_content = templates.get_template(template_name).render(**template_ctx)
    except Exception as e:
        raise HTTPException(500, f"Failed to render email template: {str(e)}")

    # Plain text fallback
    text_content = f"Hi {customer.name}, this is a payment reminder for invoice {invoice.invoice_number}. Balance due: {balance_formatted}. Pay online: {portal_url}"

    # Send email
    from app.notifications.email import send_email
    email_sent = await send_email(
        to=customer.email,
        subject=subject,
        html_body=html_content,
        text_body=text_content,
        db=db,
        invoice_id=invoice.id,
        customer_id=customer.id,
        template_name=f"payment_{tier}",
    )

    # Log activity
    activity = ActivityLog(
        action="payment_reminder_sent",
        description=f"{tier_labels[tier]} sent for {invoice.invoice_number}",
        entity_type="invoice",
        entity_id=invoice.id,
        ip_address=request.client.host if request.client else None,
        extra_data={"tier": tier, "days_overdue": days_overdue},
    )
    db.add(activity)
    await db.commit()

    return {
        "success": email_sent,
        "message": f"{tier_labels[tier]} sent" if email_sent else "Failed to send reminder",
        "tier": tier,
    }


@router.post("/api/{id}/record-payment")
async def api_record_payment(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """API: Record a manual payment against an invoice."""
    invoice = await service.get_invoice(db, id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    data = await request.json()
    amount_cents = data.get("amount_cents")
    method = data.get("method", "bank_transfer")
    reference = data.get("reference")

    if not amount_cents or amount_cents <= 0:
        raise HTTPException(400, "Invalid payment amount")

    balance = invoice.total_cents - invoice.paid_cents
    if amount_cents > balance:
        raise HTTPException(400, f"Payment amount exceeds balance of ${balance/100:.2f}")

    try:
        payment = await service.record_payment(
            db, invoice, amount_cents, method, reference,
            ip_address=request.client.host if request.client else None,
        )

        # Note: on_deposit_paid / on_final_paid are called inside record_payment()

        # Update quote payment totals if linked
        if invoice.quote_id:
            quote = await db.get(Quote, invoice.quote_id)
            if quote:
                await service.update_quote_payment_totals(db, quote)

        await db.commit()

        return {
            "success": True,
            "payment_id": payment.id,
            "new_status": invoice.status,
            "paid_cents": invoice.paid_cents,
            "balance_cents": invoice.total_cents - invoice.paid_cents,
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/quote/{quote_id}/invoices")
async def api_get_quote_invoices(
    quote_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """API: Get all invoices for a quote with payment progress."""
    quote = await db.get(Quote, quote_id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    invoices = await service.get_invoices_for_quote(db, quote_id)

    return {
        "quote_id": quote_id,
        "quote_number": quote.quote_number,
        "total_cents": quote.total_cents,
        "total_invoiced_cents": sum(inv.total_cents for inv in invoices),
        "total_paid_cents": sum(inv.paid_cents for inv in invoices),
        "invoices": [
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "stage": inv.stage,
                "stage_percent": inv.stage_percent,
                "total_cents": inv.total_cents,
                "paid_cents": inv.paid_cents,
                "balance_cents": inv.total_cents - inv.paid_cents,
                "status": inv.status,
                "due_date": inv.due_date.isoformat() if inv.due_date else None,
            }
            for inv in invoices
        ],
    }


@router.get("/api/stats")
async def api_get_stats(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """API: Get payment summary statistics for dashboard."""
    return await service.get_payment_summary_stats(db)


@router.get("/api/{id}/pdf")
async def api_get_pdf(
    id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """API: Generate and return invoice PDF."""
    invoice = await service.get_invoice(db, id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    customer = await db.get(Customer, invoice.customer_id)
    if customer:
        decrypt_customer_pii(customer)

    # Get payments for the invoice
    payments_result = await db.execute(
        select(Payment)
        .where(Payment.invoice_id == id)
        .order_by(Payment.payment_date.asc())
    )
    payments = payments_result.scalars().all()

    invoice_dict = {
        "invoice_number": invoice.invoice_number,
        "issue_date": invoice.issue_date,
        "due_date": invoice.due_date,
        "description": invoice.description,
        "line_items": invoice.line_items or [],
        "payment_schedule": invoice.payment_schedule or [],
        "subtotal_cents": invoice.subtotal_cents,
        "gst_cents": invoice.gst_cents,
        "total_cents": invoice.total_cents,
        "paid_cents": invoice.paid_cents,
        "balance_cents": invoice.total_cents - invoice.paid_cents,
        "status": invoice.status,
        "notes": invoice.notes,
    }

    customer_dict = None
    if customer:
        customer_dict = {
            "name": customer.name,
            "email": customer.email,
            "phone": customer.phone,
            "street": customer.street,
            "city": customer.city,
            "state": customer.state,
            "postcode": customer.postcode,
        }

    business_dict = {
        "name": settings.business_name,
        "trading_as": settings.trading_as,
        "abn": settings.abn,
        "address": settings.business_address,
        "phone": settings.business_phone,
        "email": settings.business_email,
        "license": settings.licence_number,
        "bank_name": settings.bank_name,
        "bank_account_name": getattr(settings, 'bank_account_name', ''),
        "bank_bsb": settings.bank_bsb,
        "bsb": settings.bank_bsb,
        "bank_account": settings.bank_account,
        "account": settings.bank_account,
    }

    try:
        payment_dicts = [
            {
                "amount_cents": p.amount_cents,
                "method": p.method,
                "reference": p.reference,
                "payment_date": p.payment_date,
            }
            for p in payments
        ]
        pdf_bytes = generate_invoice_pdf(invoice_dict, customer_dict, business_dict, payment_dicts)
    except (RuntimeError, OSError) as e:
        logger.error(f"PDF generation failed for invoice {invoice.invoice_number}: {e}")
        return Response(
            content=f"PDF generation is temporarily unavailable. WeasyPrint requires GTK/Pango libraries to be installed on the server. Error: {e}",
            media_type="text/plain",
            status_code=503,
        )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{url_quote(f'{invoice.invoice_number}.pdf')}"
        }
    )


@router.get("/api/payment/{payment_id}/receipt-pdf")
async def api_get_receipt_pdf(
    payment_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """API: Generate and return payment receipt PDF."""
    payment = await db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Payment not found")

    invoice = await db.get(Invoice, payment.invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    customer = await db.get(Customer, invoice.customer_id)
    if customer:
        decrypt_customer_pii(customer)

    payment_dict = {
        "amount_cents": payment.amount_cents,
        "method": payment.method,
        "reference": payment.reference,
        "payment_date": payment.payment_date,
    }

    invoice_dict = {
        "invoice_number": invoice.invoice_number,
        "description": invoice.description,
        "total_cents": invoice.total_cents,
        "paid_cents": invoice.paid_cents,
        "balance_cents": invoice.total_cents - invoice.paid_cents,
    }

    customer_dict = None
    if customer:
        customer_dict = {
            "name": customer.name,
            "email": customer.email,
            "phone": customer.phone,
        }

    business_dict = {
        "name": settings.business_name,
        "trading_as": settings.trading_as,
        "abn": settings.abn,
        "address": settings.business_address,
        "phone": settings.business_phone,
        "email": settings.business_email,
        "license": settings.licence_number,
    }

    try:
        pdf_bytes = generate_receipt_pdf(payment_dict, invoice_dict, customer_dict, business_dict)
    except (RuntimeError, OSError) as e:
        logger.error(f"PDF generation failed for receipt (payment {payment_id}): {e}")
        return Response(
            content=f"PDF generation is temporarily unavailable. WeasyPrint requires GTK/Pango libraries. Error: {e}",
            media_type="text/plain",
            status_code=503,
        )

    filename = f"Receipt-{invoice.invoice_number}-{payment_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{url_quote(filename)}"
        }
    )
