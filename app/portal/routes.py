"""
Portal routes — Customer-facing quote and invoice viewing.

NO AUTHENTICATION REQUIRED - These routes use secure hashed tokens for access.
The raw token is in the URL, we hash it and look up the entity.
"""

from urllib.parse import quote as url_quote
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Customer, Invoice, Payment, Quote, Photo
from app.schemas import QuoteAcceptRequest, QuoteDateSelectRequest, QuoteDeclineRequest
from app.core.templates import templates
from app.core.dates import sydney_today
from app.config import settings
from app.quotes import service
from app.quotes.service import generate_portal_token, hash_portal_token
from app.quotes.pdf import generate_quote_pdf, generate_invoice_pdf
from app.core.security import decrypt_customer_pii
from app.invoices import service as invoice_service
from app.payments import service as payment_service

# NO require_login dependency - portal is public
router = APIRouter()


# =============================================================================
# CUSTOMER DASHBOARD
# =============================================================================

@router.get("/dashboard/{customer_token}", name="portal:dashboard")
async def customer_dashboard(
    request: Request,
    customer_token: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """
    Customer portal dashboard — unified view of all quotes, invoices, payments.

    Access via a customer-specific token (not quote-specific).
    Customer token is hashed and stored on the Customer model.
    """
    # Look up customer by hashed token
    hashed = hash_portal_token(customer_token)
    result = await db.execute(
        select(Customer).where(Customer.portal_access_token == hashed)
    )
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    decrypt_customer_pii(customer)

    # Get all quotes for this customer
    quotes_result = await db.execute(
        select(Quote)
        .where(Quote.customer_id == customer.id)
        .where(Quote.status != "draft")
        .order_by(Quote.created_at.desc())
    )
    quotes = quotes_result.scalars().all()

    # Get all invoices for this customer
    invoices_result = await db.execute(
        select(Invoice)
        .where(Invoice.customer_id == customer.id)
        .where(Invoice.status != "draft")
        .order_by(Invoice.created_at.desc())
    )
    invoices = invoices_result.scalars().all()

    # Get all payments across all invoices
    invoice_ids = [inv.id for inv in invoices]
    payments = []
    if invoice_ids:
        payments_result = await db.execute(
            select(Payment)
            .where(Payment.invoice_id.in_(invoice_ids))
            .order_by(Payment.created_at.desc())
        )
        payments = payments_result.scalars().all()

    # Build invoice lookup for payments (so we can show invoice number)
    invoice_map = {inv.id: inv for inv in invoices}

    # Calculate summary totals
    total_quoted_cents = sum(q.total_cents or 0 for q in quotes)
    total_invoiced_cents = sum(inv.total_cents or 0 for inv in invoices if inv.status != "voided")
    total_paid_cents = sum(inv.paid_cents or 0 for inv in invoices if inv.status != "voided")
    outstanding_cents = sum(
        max(0, (inv.total_cents or 0) - (inv.paid_cents or 0))
        for inv in invoices
        if inv.status not in ("voided", "paid")
    )

    # Build quote token map for "View" links — we need raw tokens but only
    # have hashed ones. Portal URLs use the raw token. Since we cannot reverse
    # the hash, we store the raw token only in the URL sent to the customer.
    # For the dashboard, we just link to the quote detail by including the
    # portal_token hash — but that won't work because the portal route hashes
    # the incoming token. We need a different approach: link to nothing or
    # generate new tokens. The simplest approach: the dashboard shows quote
    # info inline, and for invoices that have portal tokens, we just note
    # that the customer already has those links from their emails.
    #
    # Actually, looking at the invoice portal route, it uses the same pattern.
    # The dashboard itself IS the unified view, so we don't strictly need
    # individual quote/invoice links. But for UX, let's include links that
    # point to the same portal routes using the tokens the customer already has.
    # Since we can't derive raw tokens from hashes, we'll skip the View links
    # for individual items — the dashboard IS the comprehensive view.

    return templates.TemplateResponse("portal/dashboard.html", {
        "request": request,
        "customer": customer,
        "quotes": quotes,
        "invoices": invoices,
        "payments": payments,
        "invoice_map": invoice_map,
        "total_quoted_cents": total_quoted_cents,
        "total_invoiced_cents": total_invoiced_cents,
        "total_paid_cents": total_paid_cents,
        "outstanding_cents": outstanding_cents,
        "today": sydney_today(),
        "business": {
            "name": settings.business_name,
            "trading_as": settings.trading_as,
            "phone": settings.business_phone,
            "email": settings.business_email,
            "abn": settings.abn,
            "bank_name": settings.bank_name,
            "bsb": settings.bank_bsb,
            "account": settings.bank_account,
        },
    })


@router.get("/my/{phone_last4}", name="portal:lookup")
async def customer_lookup(
    request: Request,
    phone_last4: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Customer lookup — find customer by last 4 digits of phone.
    Then redirect to their dashboard.
    This is a simple identity check (not full auth).
    """
    # Validate: must be exactly 4 digits
    if not phone_last4.isdigit() or len(phone_last4) != 4:
        raise HTTPException(400, "Please provide the last 4 digits of your phone number")

    # Search all customers whose phone ends with these 4 digits
    result = await db.execute(select(Customer))
    all_customers = result.scalars().all()

    matched = None
    for cust in all_customers:
        decrypt_customer_pii(cust)
        phone = (cust.phone or "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if phone and phone[-4:] == phone_last4:
            matched = cust
            break

    if not matched:
        raise HTTPException(404, "No customer found with that phone number")

    # Ensure customer has a portal access token
    if not matched.portal_access_token:
        raw_token, hashed_token = generate_portal_token()
        matched.portal_access_token = hashed_token
        await db.commit()
        # Redirect using the raw token
        return RedirectResponse(url=f"/p/dashboard/{raw_token}", status_code=303)

    # Customer already has a token — but we only have the hash, not the raw token.
    # Generate a new token so we can redirect with it.
    raw_token, hashed_token = generate_portal_token()
    matched.portal_access_token = hashed_token
    await db.commit()

    return RedirectResponse(url=f"/p/dashboard/{raw_token}", status_code=303)


# =============================================================================
# QUOTE PORTAL
# =============================================================================

@router.get("/{token}", name="portal:view_quote")
async def view_quote(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Customer views quote via portal link.

    - Looks up quote by hashing the raw token
    - Marks as viewed on first access
    - Shows expired page if past expiry date
    """
    quote = await service.get_quote_by_token(db, token)
    if not quote:
        raise HTTPException(404, "Quote not found")

    # Get customer
    customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None
    if customer:
        decrypt_customer_pii(customer)

    # Check if expired
    if quote.status == "expired":
        return templates.TemplateResponse("portal/expired.html", {
            "request": request,
            "quote": quote,
            "customer": customer,
            "business": {
                "name": settings.business_name,
                "trading_as": settings.trading_as,
                "phone": settings.business_phone,
                "email": settings.business_email,
            },
        })

    if quote.expiry_date and quote.expiry_date < sydney_today():
        # Mark as expired if not already
        if quote.status in ("draft", "sent", "viewed"):
            quote.status = "expired"
            await db.commit()

        return templates.TemplateResponse("portal/expired.html", {
            "request": request,
            "quote": quote,
            "customer": customer,
            "business": {
                "name": settings.business_name,
                "trading_as": settings.trading_as,
                "phone": settings.business_phone,
                "email": settings.business_email,
            },
        })

    # Mark as viewed on first access (only if status is 'sent')
    if quote.status == "sent":
        ip_address = request.client.host if request.client else None
        await service.mark_quote_viewed(db, quote, ip_address)
        await db.commit()
        await db.refresh(quote)

    # Get terms PDF path from settings (use default if not configured)
    from app.settings import service as settings_service
    quotation_settings = await settings_service.get_settings_by_category(db, 'quotation')
    terms_pdf_path = quotation_settings.get('terms_pdf_path') or '/static/KRG_Terms_and_Conditions_v3.0.pdf'

    return templates.TemplateResponse("portal/quote.html", {
        "request": request,
        "quote": quote,
        "customer": customer,
        "business": {
            "name": settings.business_name,
            "trading_as": settings.trading_as,
            "phone": settings.business_phone,
            "email": settings.business_email,
        },
        "today": sydney_today(),
        "token": token,  # Pass raw token for form submissions
        "terms_pdf_path": terms_pdf_path,
    })


@router.post("/{token}/accept", name="portal:accept_quote")
async def accept_quote_endpoint(
    request: Request,
    token: str,
    data: QuoteAcceptRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Customer accepts quote with signature.

    - Validates quote exists and can be accepted
    - Stores signature data (Base64 PNG)
    - Updates status to 'accepted'
    - Idempotent: double-accept returns success
    """
    quote = await service.get_quote_by_token(db, token)
    if not quote:
        raise HTTPException(404, "Quote not found")

    # Validate terms accepted
    if not data.terms_accepted:
        raise HTTPException(400, "You must accept the terms and conditions")

    # Check expiry (but allow if already accepted - idempotent case)
    if quote.status != "accepted" and quote.expiry_date and quote.expiry_date < sydney_today():
        raise HTTPException(400, "Quote has expired")

    ip_address = request.client.host if request.client else None

    try:
        _, was_already_signed = await service.sign_quote(
            db=db,
            quote=quote,
            signature_data=data.signature_data,
            signature_name=data.signer_name,
            ip_address=ip_address,
            signature_type=data.signature_type,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    if was_already_signed:
        return {
            "status": "signed",
            "message": "Quote was already signed",
            "quote_number": quote.quote_number,
            "redirect_url": f"/p/{token}/select-date",
        }

    return {
        "status": "signed",
        "message": "Quote signed successfully. First payment invoice sent to your email.",
        "quote_number": quote.quote_number,
        "redirect_url": f"/p/{token}/select-date",
    }


@router.post("/{token}/decline", name="portal:decline_quote")
async def decline_quote_endpoint(
    request: Request,
    token: str,
    data: QuoteDeclineRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Customer declines quote.

    - Validates quote exists and can be declined
    - Stores optional decline reason
    - Updates status to 'declined'
    - Idempotent: double-decline returns success
    """
    quote = await service.get_quote_by_token(db, token)
    if not quote:
        raise HTTPException(404, "Quote not found")

    ip_address = request.client.host if request.client else None

    try:
        _, was_already_declined = await service.decline_quote(
            db=db,
            quote=quote,
            reason=data.reason,
            ip_address=ip_address,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    if was_already_declined:
        return {
            "status": "declined",
            "message": "Quote was already declined",
            "quote_number": quote.quote_number,
        }

    return {
        "status": "declined",
        "message": "Quote declined",
        "quote_number": quote.quote_number,
    }


@router.post("/{token}/select-date", name="portal:select_date")
async def select_date_endpoint(
    request: Request,
    token: str,
    data: QuoteDateSelectRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Customer selects preferred start date.

    Does NOT change quote status - admin confirms date later.
    """
    quote = await service.get_quote_by_token(db, token)
    if not quote:
        raise HTTPException(404, "Quote not found")

    # Allow date selection for sent, viewed, or accepted quotes
    if quote.status not in ("sent", "viewed", "accepted"):
        raise HTTPException(400, f"Cannot select date for quote in '{quote.status}' status")

    # Validate date is in the future
    if data.requested_date < sydney_today():
        raise HTTPException(400, "Requested date must be in the future")

    ip_address = request.client.host if request.client else None

    await service.select_date(
        db=db,
        quote=quote,
        requested_date=data.requested_date,
        ip_address=ip_address,
    )
    await db.commit()

    return {
        "status": "success",
        "message": "Date preference recorded",
        "requested_date": str(data.requested_date),
    }


@router.get("/{token}/accept", name="portal:accept_page")
async def accept_page(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """
    Dedicated quote acceptance page with signature pad and T&Cs.

    Progressive workflow: View -> Accept -> Select Date -> Success
    """
    quote = await service.get_quote_by_token(db, token)
    if not quote:
        raise HTTPException(404, "Quote not found")

    # Already signed? Redirect to date selection
    if quote.signed_at:
        return RedirectResponse(url=f"/p/{token}/select-date", status_code=303)

    # Can only accept if sent or viewed
    if quote.status not in ("sent", "viewed"):
        return RedirectResponse(url=f"/p/{token}", status_code=303)

    # Check expiry
    if quote.expiry_date and quote.expiry_date < sydney_today():
        return RedirectResponse(url=f"/p/{token}", status_code=303)

    customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None
    if customer:
        decrypt_customer_pii(customer)

    # Read deposit from stored payment schedule (first payment)
    payments = (quote.calculator_result or {}).get("payments", [])
    if payments:
        deposit_amount = payments[0]["amount_cents"] / 100
        deposit_percent = int(round(payments[0]["percent"] * 100))
    else:
        deposit_amount = (quote.total_cents or 0) * 0.30 / 100
        deposit_percent = 30

    # Get terms PDF path
    from app.settings import service as settings_service
    quotation_settings = await settings_service.get_settings_by_category(db, 'quotation')
    terms_pdf_path = quotation_settings.get('terms_pdf_path') or '/static/KRG_Terms_and_Conditions_v3.0.pdf'

    return templates.TemplateResponse("portal/accept.html", {
        "request": request,
        "quote": quote,
        "customer": customer,
        "token": token,
        "deposit_amount": deposit_amount,
        "deposit_percent": deposit_percent,
        "terms_pdf_path": terms_pdf_path,
        "business": {
            "name": settings.business_name,
            "trading_as": settings.trading_as,
            "phone": settings.business_phone,
            "email": settings.business_email,
            "abn": settings.abn,
        },
    })


@router.get("/{token}/select-date", name="portal:select_date_page")
async def select_date_page(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """
    Dedicated date selection page with interactive calendar.

    Shows busy dates from confirmed bookings, allows customer to pick start date.
    Only accessible after quote is signed.
    """
    quote = await service.get_quote_by_token(db, token)
    if not quote:
        raise HTTPException(404, "Quote not found")

    # Must be signed first
    if not quote.signed_at:
        return RedirectResponse(url=f"/p/{token}/accept", status_code=303)

    # Already has a date? Show it but allow changing
    customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None
    if customer:
        decrypt_customer_pii(customer)

    # Get busy dates from confirmed bookings
    busy_dates = await _get_busy_dates(db)

    payments = (quote.calculator_result or {}).get("payments", [])
    if payments:
        deposit_amount = payments[0]["amount_cents"] / 100
    else:
        deposit_amount = (quote.total_cents or 0) * 0.30 / 100

    return templates.TemplateResponse("portal/select_date.html", {
        "request": request,
        "quote": quote,
        "customer": customer,
        "token": token,
        "busy_dates": [d.isoformat() for d in busy_dates],
        "deposit_amount": deposit_amount,
        "business": {
            "name": settings.business_name,
            "trading_as": settings.trading_as,
            "phone": settings.business_phone,
            "email": settings.business_email,
            "abn": settings.abn,
        },
    })


@router.post("/{token}/confirm-date", name="portal:confirm_date")
async def confirm_date_endpoint(
    request: Request,
    token: str,
    data: QuoteDateSelectRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Customer confirms their selected date from the calendar page.

    Saves date preference and notifies Kyle.
    """
    quote = await service.get_quote_by_token(db, token)
    if not quote:
        raise HTTPException(404, "Quote not found")

    if quote.status not in ("sent", "viewed", "accepted"):
        raise HTTPException(400, f"Cannot select date for quote in '{quote.status}' status")

    if data.requested_date < sydney_today():
        raise HTTPException(400, "Requested date must be in the future")

    ip_address = request.client.host if request.client else None

    await service.select_date(
        db=db,
        quote=quote,
        requested_date=data.requested_date,
        ip_address=ip_address,
    )

    # Notify Kyle about the date selection
    from app.notifications.service import notify_date_selected
    customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None
    await notify_date_selected(db, quote, customer, data.requested_date)

    await db.commit()

    return {
        "status": "success",
        "message": "Date confirmed! We'll be in touch soon.",
        "redirect_url": f"/p/{token}/success",
    }


@router.get("/{token}/success", name="portal:success_page")
async def success_page(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """
    Success confirmation page after quote acceptance and date selection.
    """
    quote = await service.get_quote_by_token(db, token)
    if not quote:
        raise HTTPException(404, "Quote not found")

    customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None
    if customer:
        decrypt_customer_pii(customer)

    payments = (quote.calculator_result or {}).get("payments", [])
    if payments:
        deposit_amount = payments[0]["amount_cents"] / 100
    else:
        deposit_amount = (quote.total_cents or 0) * 0.30 / 100

    return templates.TemplateResponse("portal/success.html", {
        "request": request,
        "quote": quote,
        "customer": customer,
        "token": token,
        "deposit_amount": deposit_amount,
        "business": {
            "name": settings.business_name,
            "trading_as": settings.trading_as,
            "phone": settings.business_phone,
            "email": settings.business_email,
            "abn": settings.abn,
        },
    })


async def _get_busy_dates(db: AsyncSession, days_ahead: int = 90) -> list:
    """
    Get dates that are unavailable for booking.

    Checks confirmed_start_date on quotes with active statuses.
    """
    from datetime import timedelta
    start = sydney_today()
    end = start + timedelta(days=days_ahead)

    result = await db.execute(
        select(Quote.confirmed_start_date)
        .where(Quote.confirmed_start_date.isnot(None))
        .where(Quote.confirmed_start_date.between(start, end))
        .where(Quote.status.in_(["accepted", "confirmed", "pour_stage"]))
    )

    busy = [row[0] for row in result.all() if row[0]]
    return sorted(set(busy))


@router.get("/{token}/pdf", name="portal:download_pdf")
async def download_pdf(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Customer downloads quote PDF.
    """
    quote = await service.get_quote_by_token(db, token)
    if not quote:
        raise HTTPException(404, "Quote not found")

    customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None
    if customer:
        decrypt_customer_pii(customer)

    # Convert to dicts for PDF generator
    quote_dict = {
        "quote_number": quote.quote_number,
        "quote_date": quote.quote_date,
        "expiry_date": quote.expiry_date,
        "job_name": quote.job_name,
        "job_address": quote.job_address,
        "line_items": quote.line_items or [],
        "customer_line_items": quote.customer_line_items,
        "subtotal_cents": quote.subtotal_cents,
        "discount_cents": quote.discount_cents,
        "gst_cents": quote.gst_cents,
        "total_cents": quote.total_cents,
        "notes": quote.notes,
        "calculator_result": quote.calculator_result,
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
    }

    pdf_bytes = generate_quote_pdf(quote_dict, customer_dict, business_dict)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{url_quote(f'{quote.quote_number}.pdf')}"
        }
    )


# =============================================================================
# AMENDMENT PORTAL
# =============================================================================

@router.get("/amendment/{token}", name="portal:view_amendment")
async def view_amendment(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """
    Customer views an amendment/variation via portal link.

    Shows amendment details, price impact, and accept/decline options.
    """
    from app.quotes import amendments as amendments_service

    amendment = await amendments_service.get_amendment_by_token(db, token)
    if not amendment:
        raise HTTPException(404, "Amendment not found")

    # Load parent quote and customer
    quote = await db.get(Quote, amendment.quote_id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None
    if customer:
        decrypt_customer_pii(customer)

    # Calculate price impact
    original_total = quote.total_cents or 0
    variation_amount = amendment.amount_cents or 0
    adjusted_total = original_total + variation_amount

    return templates.TemplateResponse("portal/amendment.html", {
        "request": request,
        "amendment": amendment,
        "quote": quote,
        "customer": customer,
        "token": token,
        "original_total": original_total,
        "variation_amount": variation_amount,
        "adjusted_total": adjusted_total,
        "business": {
            "name": settings.business_name,
            "trading_as": settings.trading_as,
            "phone": settings.business_phone,
            "email": settings.business_email,
            "abn": settings.abn,
        },
    })


@router.post("/amendment/{token}/accept", name="portal:accept_amendment")
async def accept_amendment_endpoint(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Customer accepts an amendment with signature.

    - Validates amendment exists and status is 'sent'
    - Stores signature data
    - Updates status to 'accepted'
    - Creates variation invoice for positive amounts
    - Notifies admin
    """
    from app.quotes import amendments as amendments_service
    from app.schemas import AmendmentAccept

    amendment = await amendments_service.get_amendment_by_token(db, token)
    if not amendment:
        raise HTTPException(404, "Amendment not found")

    if amendment.status != "sent":
        return {
            "status": amendment.status,
            "message": f"Amendment is already {amendment.status}",
        }

    # Parse request body
    body = await request.json()
    signature_data = body.get("signature_data")
    signature_name = body.get("signature_name")

    try:
        amendment = await amendments_service.accept_amendment(
            db,
            amendment_id=amendment.id,
            signature_data=signature_data,
            signature_name=signature_name,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Load quote + customer
    quote = await db.get(Quote, amendment.quote_id)
    customer = await db.get(Customer, quote.customer_id) if quote else None

    # Create variation invoice for positive amounts
    if quote and amendment.amount_cents > 0:
        try:
            await amendments_service.create_variation_invoice(db, amendment, quote, request)
        except Exception:
            pass  # Invoice creation failure shouldn't block acceptance

    # Notify admin
    if quote:
        try:
            from app.notifications.service import notify_amendment_accepted
            await notify_amendment_accepted(db, amendment, quote, customer)
        except Exception:
            pass

    await db.commit()

    return {
        "status": "accepted",
        "message": "Variation accepted successfully",
    }


@router.post("/amendment/{token}/decline", name="portal:decline_amendment")
async def decline_amendment_endpoint(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Customer declines an amendment with optional reason.
    """
    from app.quotes import amendments as amendments_service

    amendment = await amendments_service.get_amendment_by_token(db, token)
    if not amendment:
        raise HTTPException(404, "Amendment not found")

    if amendment.status != "sent":
        return {
            "status": amendment.status,
            "message": f"Amendment is already {amendment.status}",
        }

    body = await request.json()
    reason = body.get("reason")

    try:
        amendment = await amendments_service.decline_amendment(
            db,
            amendment_id=amendment.id,
            reason=reason,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Notify admin
    quote = await db.get(Quote, amendment.quote_id)
    customer = await db.get(Customer, quote.customer_id) if quote else None

    if quote:
        try:
            from app.notifications.service import notify_amendment_declined
            await notify_amendment_declined(db, amendment, quote, customer)
        except Exception:
            pass

    await db.commit()

    return {
        "status": "declined",
        "message": "Variation declined",
    }


# =============================================================================
# INVOICE PORTAL
# =============================================================================

@router.get("/invoice/{token}", name="portal:view_invoice")
async def view_invoice(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Customer views invoice via portal link.

    - Looks up invoice by hashing the raw token
    - Marks as viewed on first access
    - Shows payment options if unpaid
    """
    invoice = await invoice_service.get_invoice_by_token(db, token)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    # Get customer
    customer = await db.get(Customer, invoice.customer_id)
    if customer:
        decrypt_customer_pii(customer)

    # Mark as viewed on first access (only if status is 'sent')
    if invoice.status == "sent":
        ip_address = request.client.host if request.client else None
        await invoice_service.mark_invoice_viewed(db, invoice, ip_address)
        await db.commit()
        await db.refresh(invoice)

    # Get payments
    payments_result = await db.execute(
        select(Payment)
        .where(Payment.invoice_id == invoice.id)
        .order_by(Payment.created_at.desc())
    )
    payments = payments_result.scalars().all()

    return templates.TemplateResponse("portal/invoice.html", {
        "request": request,
        "invoice": invoice,
        "customer": customer,
        "payments": payments,
        "balance_cents": invoice.total_cents - invoice.paid_cents,
        "business": {
            "name": settings.business_name,
            "trading_as": settings.trading_as,
            "phone": settings.business_phone,
            "email": settings.business_email,
            "bank_name": settings.bank_name,
            "bsb": settings.bank_bsb,
            "account": settings.bank_account,
        },
        "token": token,
        "stripe_enabled": bool(settings.stripe_secret_key),
        "stripe_publishable_key": settings.stripe_publishable_key,
    })


@router.get("/invoice/{token}/pdf", name="portal:invoice_pdf")
async def download_invoice_pdf(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Customer downloads invoice PDF.
    """
    invoice = await invoice_service.get_invoice_by_token(db, token)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    customer = await db.get(Customer, invoice.customer_id)
    if customer:
        decrypt_customer_pii(customer)

    invoice_dict = {
        "invoice_number": invoice.invoice_number,
        "issue_date": invoice.issue_date,
        "due_date": invoice.due_date,
        "description": invoice.description,
        "line_items": invoice.line_items or [],
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
        "bsb": settings.bank_bsb,
        "account": settings.bank_account,
    }

    pdf_bytes = generate_invoice_pdf(invoice_dict, customer_dict, business_dict)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{url_quote(f'{invoice.invoice_number}.pdf')}"
        }
    )


@router.post("/invoice/{token}/pay", name="portal:pay_invoice")
async def create_payment_session(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Create Stripe checkout session from portal.

    Customer clicks "Pay Now" button -> redirects to Stripe Checkout.
    """
    invoice = await invoice_service.get_invoice_by_token(db, token)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    if invoice.status in ("paid", "voided"):
        raise HTTPException(400, f"Invoice is {invoice.status}")

    balance = invoice.total_cents - invoice.paid_cents
    if balance <= 0:
        raise HTTPException(400, "Invoice is already paid")

    # Build success/cancel URLs with portal token
    success_url = f"{settings.app_url}/p/invoice/{token}?payment=success"
    cancel_url = f"{settings.app_url}/p/invoice/{token}?payment=cancelled"

    try:
        result = await payment_service.create_checkout_session(
            db, invoice, success_url, cancel_url
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


# =============================================================================
# QUOTE PORTFOLIO PHOTOS
# =============================================================================

@router.get("/{token}/photos", name="portal:quote_photos")
async def get_quote_photos(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> list:
    """
    Get photos shared with customer for a quote.

    Returns list of photo dicts with URLs for the photo gallery.
    """
    quote = await service.get_quote_by_token(db, token)
    if not quote:
        raise HTTPException(404, "Quote not found")

    result = await db.execute(
        select(Photo)
        .where(Photo.quote_id == quote.id)
        .where(Photo.shared_with_customer == True)
        .order_by(Photo.category, Photo.created_at.desc())
    )
    photos = result.scalars().all()

    return [
        {
            "id": p.id,
            "url": p.storage_url,
            "thumbnail": p.thumbnail_url or p.storage_url,
            "caption": p.caption,
            "category": p.category,
            "taken_at": p.taken_at.isoformat() if p.taken_at else None,
        }
        for p in photos
    ]
