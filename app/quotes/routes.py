"""
Quotes routes — Calculator and CRUD.

Follow the pattern from app/customers/routes.py for full CRUD.
"""

from typing import Optional
from urllib.parse import quote as url_quote
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from app.database import get_db
from app.models import Quote, Customer, ActivityLog, Worker, CommunicationLog, ProgressUpdate, Photo
from app.schemas import (
    QuoteCreate, QuoteUpdate, QuoteResponse,
    LabourQuoteCreate, CustomQuoteCreate,
    PaginatedResponse, SuccessResponse, ConfirmBookingRequest,
    QuotePreviewUpdate,
    AmendmentCreate, AmendmentUpdate, AmendmentResponse, AmendmentDecline,
)
from app.quotes import amendments as amendments_service
from app.core.auth import require_login, verify_csrf
from app.core.templates import templates
from app.core.dates import sydney_now, sydney_today
from app.config import settings
from app.quotes.calculator import calculate_quote
from app.quotes.pdf import generate_quote_pdf
from app.quotes import service

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


# =============================================================================
# API — Calculator
# =============================================================================

@router.get("/api/next-number")
async def api_next_quote_number(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get the next quote number for display in the form.

    Note: This is a preview only - the actual number is assigned on creation.
    """
    quote_number = await service.get_next_quote_number(db)
    # Rollback so we don't consume the number
    await db.rollback()
    return {"quote_number": quote_number}


@router.get("/api/places/autocomplete")
async def api_places_autocomplete(
    q: str = Query("", min_length=2),
):
    """
    Proxy Google Places Autocomplete (New) for address suggestions.

    Returns list of place predictions restricted to Australia.
    """
    if not settings.google_places_api_key:
        raise HTTPException(501, "Google Places API key not configured")

    import httpx
    url = "https://maps.googleapis.com/maps/api/place/autocomplete/json"
    params = {
        "input": q,
        "components": "country:au",
        "types": "address",
        "key": settings.google_places_api_key,
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=5)
            data = resp.json()
    except (httpx.RequestError, httpx.TimeoutException):
        raise HTTPException(502, "Google Places API unavailable")

    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        raise HTTPException(502, f"Google Places API error: {data.get('status')}")

    predictions = []
    for p in data.get("predictions", []):
        predictions.append({
            "place_id": p["place_id"],
            "description": p["description"],
        })
    return predictions


@router.get("/api/places/details")
async def api_places_details(
    place_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Fetch lat/lng for a place_id, then calculate driving distances
    from business base and concrete yard.
    """
    if not settings.google_places_api_key:
        raise HTTPException(501, "Google Places API key not configured")

    import httpx

    # 1. Get place details (lat/lng + formatted address)
    details_url = "https://maps.googleapis.com/maps/api/place/details/json"
    details_params = {
        "place_id": place_id,
        "fields": "geometry,formatted_address",
        "key": settings.google_places_api_key,
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(details_url, params=details_params, timeout=5)
            details = resp.json()
    except (httpx.RequestError, httpx.TimeoutException):
        raise HTTPException(502, "Google Places API unavailable")

    result = details.get("result", {})
    location = result.get("geometry", {}).get("location", {})
    lat = location.get("lat")
    lng = location.get("lng")
    formatted_address = result.get("formatted_address", "")

    if not lat or not lng:
        raise HTTPException(400, "Could not resolve place location")

    # 2. Calculate driving distances (base → job, concrete yard → job)
    from app.settings.service import get_setting
    base_address = settings.business_address  # "Thurgoona NSW 2640"
    yard_address = await get_setting(db, "pricing", "concrete_yard", default="225 Jude Road, Howlong NSW")

    distance_url = "https://maps.googleapis.com/maps/api/distancematrix/json"
    distance_params = {
        "origins": f"{base_address}|{yard_address}",
        "destinations": f"{lat},{lng}",
        "units": "metric",
        "key": settings.google_places_api_key,
    }
    distance_from_base = None
    distance_from_yard = None

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(distance_url, params=distance_params, timeout=5)
            dm = resp.json()
    except (httpx.RequestError, httpx.TimeoutException):
        # Distance lookup failed but we still have the place details
        dm = {}

    rows = dm.get("rows", [])
    if len(rows) >= 1:
        elements = rows[0].get("elements", [])
        if elements and elements[0].get("status") == "OK":
            distance_from_base = round(elements[0]["distance"]["value"] / 1000)  # km
    if len(rows) >= 2:
        elements = rows[1].get("elements", [])
        if elements and elements[0].get("status") == "OK":
            distance_from_yard = round(elements[0]["distance"]["value"] / 1000)  # km

    return {
        "formatted_address": formatted_address,
        "lat": lat,
        "lng": lng,
        "distance_from_base_km": distance_from_base,
        "distance_from_yard_km": distance_from_yard,
    }


@router.post("/api/calculate")
async def api_calculate(
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    """
    Run calculator and return result.

    Accepts all CalculatorInput fields as JSON.
    Fetches pricing from DB so calculator uses current crew/team rates.
    """
    from app.quotes.pricing import get_pricing_async
    pricing = await get_pricing_async(db)
    result = calculate_quote(data, pricing=pricing)
    return result


# =============================================================================
# API — CRUD
# =============================================================================

@router.post("/api/create")
async def api_create_quote(
    request: Request,
    data: QuoteCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Create a new quote with calculated values.

    Returns quote data with raw portal token for URL.
    """
    try:
        quote, raw_token = await service.create_quote(db, data, request)
        await db.commit()
        await db.refresh(quote)

        return {
            "id": quote.id,
            "quote_number": quote.quote_number,
            "status": quote.status,
            "total_cents": quote.total_cents,
            "portal_token": raw_token,
            "portal_url": f"{settings.app_url}/p/{raw_token}",
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/create-labour")
async def api_create_labour_quote(
    request: Request,
    data: LabourQuoteCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create a new labour invoice quote."""
    try:
        quote, raw_token = await service.create_labour_quote(db, data, request)
        await db.commit()
        await db.refresh(quote)
        return {
            "id": quote.id,
            "quote_number": quote.quote_number,
            "status": quote.status,
            "total_cents": quote.total_cents,
            "portal_token": raw_token,
            "portal_url": f"{settings.app_url}/p/{raw_token}",
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/create-custom")
async def api_create_custom_quote(
    request: Request,
    data: CustomQuoteCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create a new custom/freeform quote."""
    try:
        quote, raw_token = await service.create_custom_quote(db, data, request)
        await db.commit()
        await db.refresh(quote)
        return {
            "id": quote.id,
            "quote_number": quote.quote_number,
            "status": quote.status,
            "total_cents": quote.total_cents,
            "portal_token": raw_token,
            "portal_url": f"{settings.app_url}/p/{raw_token}",
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/list")
async def api_list_quotes(
    db: AsyncSession = Depends(get_db),
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PaginatedResponse:
    """API: List quotes with pagination."""
    quotes, total = await service.get_quotes(db, status, page, page_size)

    # Build response items (without raw tokens - those are only given at creation/send)
    items = []
    for q in quotes:
        items.append({
            "id": q.id,
            "quote_number": q.quote_number,
            "customer_id": q.customer_id,
            "job_name": q.job_name,
            "status": q.status,
            "total_cents": q.total_cents,
            "quote_date": q.quote_date,
            "expiry_date": q.expiry_date,
            "created_at": q.created_at,
        })

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size if total > 0 else 0,
    )


@router.get("/api/{id}")
async def api_get_quote(
    id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """API: Get a single quote."""
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None

    return {
        "id": quote.id,
        "quote_number": quote.quote_number,
        "customer_id": quote.customer_id,
        "customer_name": customer.name if customer else None,
        "job_name": quote.job_name,
        "job_address": quote.job_address,
        "calculator_input": quote.calculator_input,
        "calculator_result": quote.calculator_result,
        "line_items": quote.line_items,
        "customer_line_items": quote.customer_line_items,
        "subtotal_cents": quote.subtotal_cents,
        "discount_cents": quote.discount_cents,
        "gst_cents": quote.gst_cents,
        "total_cents": quote.total_cents,
        "status": quote.status,
        "quote_date": quote.quote_date,
        "expiry_date": quote.expiry_date,
        "sent_at": quote.sent_at,
        "viewed_at": quote.viewed_at,
        "accepted_at": quote.accepted_at,
        "requested_start_date": quote.requested_start_date,
        "confirmed_start_date": quote.confirmed_start_date,
        "signature_name": quote.signature_name,
        "signed_at": quote.signed_at,
        "notes": quote.notes,
        "internal_notes": quote.internal_notes,
        "created_at": quote.created_at,
        "updated_at": quote.updated_at,
    }


@router.put("/api/{id}")
async def api_update_quote(
    request: Request,
    id: int,
    data: QuoteUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """API: Update a draft quote."""
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    try:
        quote = await service.update_quote(db, quote, data, request)
        await db.commit()
        await db.refresh(quote)

        return {
            "id": quote.id,
            "quote_number": quote.quote_number,
            "status": quote.status,
            "total_cents": quote.total_cents,
            "updated_at": quote.updated_at,
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/api/{id}/preview")
async def api_update_preview(
    request: Request,
    id: int,
    data: QuotePreviewUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """API: Update customer-facing line items from preview page."""
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    try:
        customer_line_items = [item.model_dump() for item in data.customer_line_items]
        quote = await service.update_quote_preview(
            db, quote, customer_line_items, data.notes, request
        )
        await db.commit()
        await db.refresh(quote)

        # Return profit comparison
        from app.quotes.customer_lines import calculate_profit_comparison
        comparison = calculate_profit_comparison(
            quote.calculator_result, quote.customer_line_items
        )

        return {
            "id": quote.id,
            "subtotal_cents": quote.subtotal_cents,
            "gst_cents": quote.gst_cents,
            "total_cents": quote.total_cents,
            "comparison": comparison,
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/api/{id}")
async def api_delete_quote(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    """API: Delete a draft quote."""
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    try:
        await service.delete_quote(db, quote, request)
        await db.commit()
        return SuccessResponse(message=f"Quote {quote.quote_number} deleted")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/{id}/duplicate")
async def api_duplicate_quote(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """API: Duplicate quote to new draft."""
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    new_quote, raw_token = await service.duplicate_quote(db, quote, request)
    await db.commit()
    await db.refresh(new_quote)

    return {
        "id": new_quote.id,
        "quote_number": new_quote.quote_number,
        "status": new_quote.status,
        "total_cents": new_quote.total_cents,
        "portal_token": raw_token,
        "portal_url": f"{settings.app_url}/p/{raw_token}",
        "source_quote_number": quote.quote_number,
    }


@router.get("/api/{id}/pdf")
async def api_get_pdf(
    id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """API: Generate and return quote PDF."""
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None

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
            "Content-Disposition": f"attachment; filename*=UTF-8''{url_quote(f'{quote.quote_number}.pdf')}"
        }
    )


@router.post("/api/{id}/send")
async def api_send_quote(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    API: Mark quote as sent and send email to customer.
    """
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    try:
        raw_token = await service.send_quote(db, quote, request)
        await db.commit()
        await db.refresh(quote)

        return {
            "id": quote.id,
            "quote_number": quote.quote_number,
            "status": quote.status,
            "sent_at": quote.sent_at,
            "portal_token": raw_token,
            "portal_url": f"{settings.app_url}/p/{raw_token}",
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/{id}/confirm-booking")
async def api_confirm_booking(
    request: Request,
    id: int,
    data: ConfirmBookingRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    API: Confirm booking date.

    Requires quote to be in 'accepted' status.
    First payment invoice is already created at sign time.
    """
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    try:
        quote = await service.confirm_booking(
            db, quote, data.confirmed_date, request
        )
        await db.commit()
        await db.refresh(quote)

        return {
            "success": True,
            "quote": {
                "id": quote.id,
                "quote_number": quote.quote_number,
                "status": quote.status,
                "confirmed_start_date": str(quote.confirmed_start_date) if quote.confirmed_start_date else None,
            },
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/{id}/create-progress-invoices")
async def api_create_progress_invoices(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    API: Create all progress payment invoices for a quote.

    Creates first payment (30%), progress (60%), and final (10%) invoices.
    Only the first payment invoice is sent immediately.
    """
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    if quote.status not in ("sent", "viewed", "accepted", "confirmed"):
        raise HTTPException(400, f"Quote must be signed/accepted to create invoices (current status: {quote.status})")

    # Check if invoices already exist
    from app.invoices.service import get_invoices_for_quote
    existing = await get_invoices_for_quote(db, id)
    if existing:
        raise HTTPException(400, f"Invoices already exist for this quote ({len(existing)} found)")

    try:
        from app.invoices.service import create_progress_invoices, send_invoice
        invoices = await create_progress_invoices(db, quote, request)

        # Send the first payment invoice immediately
        deposit_invoice = next(
            (inv for inv in invoices if inv.stage in ("deposit", "booking")),
            None
        )
        raw_token = None
        if deposit_invoice:
            raw_token = await send_invoice(db, deposit_invoice, request)

        await db.commit()

        return {
            "success": True,
            "invoice_count": len(invoices),
            "invoices": [
                {
                    "id": inv.id,
                    "invoice_number": inv.invoice_number,
                    "stage": inv.stage,
                    "stage_percent": inv.stage_percent,
                    "total_cents": inv.total_cents,
                    "status": inv.status,
                }
                for inv in invoices
            ],
            "deposit_portal_url": f"{settings.app_url}/p/invoice/{raw_token}" if raw_token else None,
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/{id}/send-prepour-invoice")
async def api_send_prepour_invoice(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    API: Send the 60% pre-pour invoice. Transitions quote to pour_stage.
    """
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    if quote.status != "confirmed":
        raise HTTPException(400, f"Quote must be confirmed to send pre-pour invoice (current: {quote.status})")

    from app.invoices.service import on_job_scheduled
    invoice = await on_job_scheduled(db, quote, quote.confirmed_start_date, request=request)

    if not invoice:
        raise HTTPException(400, "No pre-pour invoice found to send (may already be sent)")

    await db.commit()
    await db.refresh(quote)

    return {
        "success": True,
        "quote_status": quote.status,
        "invoice": {
            "id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "total_cents": invoice.total_cents,
            "status": invoice.status,
        },
    }


@router.post("/api/{id}/send-final-invoice")
async def api_send_final_invoice(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    API: Send the 10% final invoice. Transitions quote to pending_completion.
    """
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    if quote.status != "pour_stage":
        raise HTTPException(400, f"Quote must be in pour_stage to send final invoice (current: {quote.status})")

    from app.invoices.service import on_job_completed
    invoice = await on_job_completed(db, quote, request=request)

    if not invoice:
        raise HTTPException(400, "No final invoice found to send (may already be sent)")

    await db.commit()
    await db.refresh(quote)

    return {
        "success": True,
        "quote_status": quote.status,
        "invoice": {
            "id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "total_cents": invoice.total_cents,
            "status": invoice.status,
        },
    }


@router.get("/api/{id}/activity")
async def api_quote_activity(
    id: int,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
):
    """API: Get quote activity log."""
    result = await db.execute(
        select(ActivityLog)
        .where(ActivityLog.entity_type == "quote")
        .where(ActivityLog.entity_id == id)
        .order_by(ActivityLog.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


# =============================================================================
# API — Review Request, Followup, Progress Update
# =============================================================================

@router.post("/api/{id}/request-review")
async def api_request_review(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    API: Send Google review request to customer (email + SMS).

    Guard: quote must be 'completed' and review_requested must be False.
    """
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    if quote.status != "completed":
        raise HTTPException(400, f"Can only request reviews for completed jobs (current: {quote.status})")

    if quote.review_requested:
        raise HTTPException(400, "Review has already been requested for this job")

    customer = await db.get(Customer, quote.customer_id)
    if not customer:
        raise HTTPException(400, "No customer linked to this quote")

    # Send email
    from app.notifications.email import send_review_request_email
    email_sent = await send_review_request_email(db, quote, customer)

    # Send SMS
    from app.notifications.sms import send_review_request_sms
    sms_result = await send_review_request_sms(db, quote, customer)

    # Mark as requested
    quote.review_requested = True

    # Log activity
    activity = ActivityLog(
        action="review_requested",
        description=f"Sent Google review request for {quote.quote_number}",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=request.client.host if request.client else None,
    )
    db.add(activity)
    await db.commit()

    return {
        "success": True,
        "email_sent": email_sent,
        "sms_sent": sms_result.get("success", False),
    }


@router.post("/api/{id}/send-followup")
async def api_send_followup(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    API: Send quote followup email + SMS.

    Guard: quote must be in 'sent' or 'viewed' status.
    Generates a fresh portal token and increments followup_count.
    """
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    if quote.status not in ("sent", "viewed"):
        raise HTTPException(400, f"Can only follow up on sent/viewed quotes (current: {quote.status})")

    customer = await db.get(Customer, quote.customer_id)
    if not customer:
        raise HTTPException(400, "No customer linked to this quote")

    # Regenerate portal token (fresh link for the customer)
    from app.quotes.service import generate_portal_token
    raw_token, hashed_token = generate_portal_token()
    quote.portal_token = hashed_token
    portal_url = f"{settings.app_url}/p/{raw_token}"

    # Increment followup count
    quote.followup_count = (quote.followup_count or 0) + 1
    followup_number = quote.followup_count

    # Send email
    from app.notifications.email import send_quote_followup_email
    email_sent = await send_quote_followup_email(
        db, quote, customer, portal_url, followup_number
    )

    # Send SMS
    from app.notifications.sms import send_quote_followup_sms
    sms_result = await send_quote_followup_sms(db, quote, customer, portal_url)

    # Log activity
    activity = ActivityLog(
        action="quote_followup_sent",
        description=f"Sent followup #{followup_number} for {quote.quote_number}",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=request.client.host if request.client else None,
        extra_data={"followup_number": followup_number},
    )
    db.add(activity)
    await db.commit()

    return {
        "success": True,
        "followup_number": followup_number,
        "email_sent": email_sent,
        "sms_sent": sms_result.get("success", False),
        "portal_url": portal_url,
    }


class ProgressUpdateRequest(BaseModel):
    title: str
    message: str
    photo_ids: list[int] = []


@router.post("/api/{id}/send-progress-update")
async def api_send_progress_update(
    request: Request,
    id: int,
    data: ProgressUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    API: Send progress update with photos to customer.

    Guard: quote must be in 'confirmed', 'pour_stage', or 'pending_completion' status.
    """
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    if quote.status not in ("confirmed", "pour_stage", "pending_completion"):
        raise HTTPException(400, f"Can only send progress updates for active jobs (current: {quote.status})")

    customer = await db.get(Customer, quote.customer_id)
    if not customer:
        raise HTTPException(400, "No customer linked to this quote")

    # Fetch selected photos (validate they belong to this quote)
    photos = []
    if data.photo_ids:
        result = await db.execute(
            select(Photo).where(
                Photo.id.in_(data.photo_ids),
                Photo.quote_id == id,
            )
        )
        photos = list(result.scalars().all())

    # Create ProgressUpdate record
    progress_update = ProgressUpdate(
        quote_id=quote.id,
        customer_id=customer.id,
        title=data.title,
        message=data.message,
        photo_ids=data.photo_ids if data.photo_ids else None,
        sent_at=sydney_now(),
        created_at=sydney_now(),
    )
    db.add(progress_update)

    # Send email
    from app.notifications.email import send_progress_update_email
    email_sent = await send_progress_update_email(
        db, quote, customer, data.title, data.message, photos
    )

    # Send SMS
    from app.notifications.sms import send_progress_update_sms
    sms_result = await send_progress_update_sms(db, quote, customer)

    # Log activity
    activity = ActivityLog(
        action="progress_update_sent",
        description=f"Sent progress update '{data.title}' for {quote.quote_number}",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=request.client.host if request.client else None,
        extra_data={"title": data.title, "photo_count": len(photos)},
    )
    db.add(activity)
    await db.commit()

    return {
        "success": True,
        "email_sent": email_sent,
        "sms_sent": sms_result.get("success", False),
        "photo_count": len(photos),
    }


# =============================================================================
# HTML PAGES
# =============================================================================

@router.get("", name="quotes:list")
async def quote_list_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
):
    """Quote list page."""
    page_size = 20

    quotes, total = await service.get_quotes(db, status, page, page_size)

    return templates.TemplateResponse("quotes/list.html", {
        "request": request,
        "quotes": quotes,
        "status_filter": status,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    })


@router.get("/new", name="quotes:new")
async def quote_type_selection_page(
    request: Request,
):
    """Quote type selection page — choose calculator, labour, or custom."""
    return templates.TemplateResponse("quotes/new.html", {
        "request": request,
    })


@router.get("/new/calculator", name="quotes:new_calculator")
async def quote_new_calculator_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    customer_id: Optional[int] = None,
):
    """New quote calculator page."""
    customer = None
    if customer_id:
        customer = await db.get(Customer, customer_id)

    result = await db.execute(select(Customer).order_by(Customer.name))
    customers = result.scalars().all()

    from app.quotes.pricing import get_season_from_month
    default_season = get_season_from_month(sydney_today().month)

    return templates.TemplateResponse("quotes/form.html", {
        "request": request,
        "quote": None,
        "customer": customer,
        "customers": customers,
        "is_new": True,
        "default_season": default_season,
    })


@router.get("/new/labour", name="quotes:new_labour")
async def quote_new_labour_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    customer_id: Optional[int] = None,
):
    """New labour invoice quote page."""
    customer = None
    if customer_id:
        customer = await db.get(Customer, customer_id)

    result = await db.execute(select(Customer).order_by(Customer.name))
    customers = result.scalars().all()

    from app.models import Worker
    worker_result = await db.execute(
        select(Worker).where(Worker.active == True).order_by(Worker.name)
    )
    workers = worker_result.scalars().all()

    from app.quotes.pricing import TEAM_RATES
    return templates.TemplateResponse("quotes/labour_form.html", {
        "request": request,
        "quote": None,
        "customer": customer,
        "customers": customers,
        "workers": workers,
        "is_new": True,
        "team_rates": TEAM_RATES,
    })


@router.get("/new/custom", name="quotes:new_custom")
async def quote_new_custom_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    customer_id: Optional[int] = None,
):
    """New custom/freeform quote page."""
    customer = None
    if customer_id:
        customer = await db.get(Customer, customer_id)

    result = await db.execute(select(Customer).order_by(Customer.name))
    customers = result.scalars().all()

    return templates.TemplateResponse("quotes/custom_form.html", {
        "request": request,
        "quote": None,
        "customer": customer,
        "customers": customers,
        "is_new": True,
    })


@router.get("/{id}/edit", name="quotes:edit")
async def quote_edit_page(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Edit page for draft quotes — dispatches to correct form by quote_type."""
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    if quote.status != "draft":
        raise HTTPException(400, "Only draft quotes can be edited")

    customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None

    result = await db.execute(select(Customer).order_by(Customer.name))
    customers = result.scalars().all()

    quote_type = quote.quote_type or "calculator"

    if quote_type == "labour":
        from app.quotes.pricing import TEAM_RATES
        return templates.TemplateResponse("quotes/labour_form.html", {
            "request": request,
            "quote": quote,
            "customer": customer,
            "customers": customers,
            "is_new": False,
            "team_rates": TEAM_RATES,
        })
    elif quote_type == "custom":
        return templates.TemplateResponse("quotes/custom_form.html", {
            "request": request,
            "quote": quote,
            "customer": customer,
            "customers": customers,
            "is_new": False,
        })
    else:
        from app.quotes.pricing import get_season_from_month
        default_season = get_season_from_month(sydney_today().month)
        return templates.TemplateResponse("quotes/form.html", {
            "request": request,
            "quote": quote,
            "customer": customer,
            "customers": customers,
            "is_new": False,
            "default_season": default_season,
        })


@router.get("/{id}/preview", name="quotes:preview")
async def quote_preview_page(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Preview and edit customer-facing line items."""
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    if quote.status != "draft":
        raise HTTPException(400, "Only draft quotes can be previewed for editing")

    # Auto-generate customer_line_items if not yet set (calculator quotes only)
    if not quote.customer_line_items and (quote.quote_type or "calculator") == "calculator":
        from app.quotes.customer_lines import generate_customer_line_items
        quote.customer_line_items = generate_customer_line_items(
            quote.calculator_result or {}, quote.calculator_input or {}
        )
        await db.commit()
        await db.refresh(quote)

    customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None

    # Compute profit comparison for sidebar
    from app.quotes.customer_lines import calculate_profit_comparison
    comparison = calculate_profit_comparison(
        quote.calculator_result or {}, quote.customer_line_items or []
    )

    return templates.TemplateResponse("quotes/preview.html", {
        "request": request,
        "quote": quote,
        "customer": customer,
        "comparison": comparison,
    })


@router.get("/{id}", name="quotes:detail")
async def quote_detail_page(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Quote detail page."""
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None

    # Get job assignments
    from app.workers.service import get_job_assignments
    assignments = await get_job_assignments(db, id)

    # Get all active workers for assignment picker
    workers_result = await db.execute(
        select(Worker).where(Worker.active == True).order_by(Worker.name)
    )
    workers = workers_result.scalars().all()

    # Get invoices for this quote (for payment progress section)
    from app.invoices.service import get_invoices_for_quote, update_quote_payment_totals
    invoices = await get_invoices_for_quote(db, id)

    # Update cached payment totals if they're stale
    if invoices:
        await update_quote_payment_totals(db, quote)
        await db.commit()
        await db.refresh(quote)

    # Get activity log
    result = await db.execute(
        select(ActivityLog)
        .where(ActivityLog.entity_type == "quote")
        .where(ActivityLog.entity_id == id)
        .order_by(ActivityLog.created_at.desc())
        .limit(20)
    )
    activity = result.scalars().all()

    # Get amendments/variations
    quote_amendments = await amendments_service.get_amendments_for_quote(db, id)
    accepted_variations_total = await amendments_service.get_accepted_amendments_total(db, id)

    # Get communications log for this quote
    comms_result = await db.execute(
        select(CommunicationLog)
        .where(CommunicationLog.quote_id == id)
        .order_by(CommunicationLog.created_at.desc())
        .limit(50)
    )
    communications = comms_result.scalars().all()

    # Get progress update history
    progress_result = await db.execute(
        select(ProgressUpdate)
        .where(ProgressUpdate.quote_id == id)
        .order_by(ProgressUpdate.created_at.desc())
    )
    progress_updates = progress_result.scalars().all()

    # Get photos for progress update modal
    photos_result = await db.execute(
        select(Photo)
        .where(Photo.quote_id == id)
        .order_by(Photo.created_at.desc())
    )
    quote_photos = photos_result.scalars().all()

    # Get job costing summary (if costs have been entered)
    from app.costing.service import get_costing, calculate_analysis
    job_costing = await get_costing(db, id)
    costing_analysis = None
    if job_costing and job_costing.actual_total_cents > 0:
        costing_analysis = calculate_analysis(job_costing, quote)

    return templates.TemplateResponse("quotes/detail.html", {
        "request": request,
        "quote": quote,
        "customer": customer,
        "assignments": assignments,
        "workers": workers,
        "invoices": invoices,
        "activity": activity,
        "amendments": quote_amendments,
        "accepted_variations_total": accepted_variations_total,
        "communications": communications,
        "progress_updates": progress_updates,
        "quote_photos": quote_photos,
        "costing_analysis": costing_analysis,
    })


# =============================================================================
# API — Quote Amendments / Variations
# =============================================================================

@router.post("/api/amendments", response_model=AmendmentResponse)
async def api_create_amendment(
    data: AmendmentCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new quote amendment."""
    try:
        amendment = await amendments_service.create_amendment(
            db,
            quote_id=data.quote_id,
            description=data.description,
            amount_cents=data.amount_cents,
        )
        return amendment
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/{quote_id}/amendments")
async def api_get_quote_amendments(
    quote_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get all amendments for a quote."""
    amendments_list = await amendments_service.get_amendments_for_quote(db, quote_id)
    return [AmendmentResponse.model_validate(a) for a in amendments_list]


@router.get("/api/amendments/{amendment_id}", response_model=AmendmentResponse)
async def api_get_amendment(
    amendment_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get a single amendment."""
    amendment = await amendments_service.get_amendment(db, amendment_id)
    if not amendment:
        raise HTTPException(status_code=404, detail="Amendment not found")
    return amendment


@router.patch("/api/amendments/{amendment_id}", response_model=AmendmentResponse)
async def api_update_amendment(
    amendment_id: int,
    data: AmendmentUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update amendment (draft only)."""
    try:
        amendment = await amendments_service.update_amendment(
            db,
            amendment_id=amendment_id,
            description=data.description,
            amount_cents=data.amount_cents,
        )
        return amendment
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/amendments/{amendment_id}/send", response_model=AmendmentResponse)
async def api_send_amendment(
    amendment_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Send amendment to customer with email + SMS notifications."""
    try:
        amendment = await amendments_service.send_amendment(db, amendment_id)

        # Load quote and customer for notifications
        quote = await db.get(Quote, amendment.quote_id)
        customer = await db.get(Customer, quote.customer_id) if quote else None

        if customer and quote:
            from app.core.security import decrypt_customer_pii
            decrypt_customer_pii(customer)

            # Generate fresh raw token for the URL; store hash in DB
            from app.quotes.amendments import generate_amendment_token
            raw_token, hashed_token = generate_amendment_token()
            amendment.portal_token = hashed_token
            await db.flush()
            portal_url = f"{settings.app_url}/p/amendment/{raw_token}"

            # Send email
            try:
                from app.notifications.email import send_amendment_email
                await send_amendment_email(db, amendment, quote, customer, portal_url)
            except Exception:
                pass  # Email failure shouldn't block the send

            # Send SMS
            try:
                from app.notifications.sms import send_amendment_sms
                await send_amendment_sms(db, amendment, quote, customer, portal_url)
            except Exception:
                pass  # SMS failure shouldn't block the send

            # Create notification for admin
            try:
                from app.notifications.service import notify_amendment_sent
                await notify_amendment_sent(db, amendment, quote, customer)
            except Exception:
                pass

            await db.commit()

        return amendment
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/amendments/{amendment_id}")
async def api_delete_amendment(
    amendment_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete amendment (draft only)."""
    try:
        await amendments_service.delete_amendment(db, amendment_id)
        return {"message": "Amendment deleted"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/amendments/{amendment_id}/decline")
async def api_decline_amendment(
    amendment_id: int,
    data: AmendmentDecline,
    db: AsyncSession = Depends(get_db),
):
    """Decline amendment (admin-initiated)."""
    try:
        amendment = await amendments_service.decline_amendment(
            db, amendment_id, reason=data.reason
        )

        # Notify
        try:
            quote = await db.get(Quote, amendment.quote_id)
            customer = await db.get(Customer, quote.customer_id) if quote else None
            if quote:
                from app.notifications.service import notify_amendment_declined
                await notify_amendment_declined(db, amendment, quote, customer)
                await db.commit()
        except Exception:
            pass

        return AmendmentResponse.model_validate(amendment)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# TIME TRACKING — Routes & API
# =============================================================================

from app.models import TimeEntry


class TimeEntryCreate(BaseModel):
    worker_id: int
    work_date: str  # YYYY-MM-DD
    hours: float
    stage: Optional[str] = None
    notes: Optional[str] = None


class TimeEntryUpdate(BaseModel):
    worker_id: Optional[int] = None
    work_date: Optional[str] = None  # YYYY-MM-DD
    hours: Optional[float] = None
    stage: Optional[str] = None
    notes: Optional[str] = None


@router.get("/{id}/time", name="quotes:time_tracking")
async def quote_time_tracking_page(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Time tracking page for a quote/job."""
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None

    # Get active workers for dropdown
    workers_result = await db.execute(
        select(Worker).where(Worker.active == True).order_by(Worker.name)
    )
    workers = workers_result.scalars().all()

    return templates.TemplateResponse("quotes/time_tracking.html", {
        "request": request,
        "quote": quote,
        "customer": customer,
        "workers": workers,
    })


@router.get("/api/{id}/time-entries")
async def api_get_time_entries(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """API: Get all time entries for a quote."""
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(TimeEntry)
        .where(TimeEntry.quote_id == id)
        .options(selectinload(TimeEntry.worker))
        .order_by(TimeEntry.work_date.desc(), TimeEntry.created_at.desc())
    )
    entries = result.scalars().all()

    return [
        {
            "id": entry.id,
            "quote_id": entry.quote_id,
            "worker_id": entry.worker_id,
            "worker_name": entry.worker.name if entry.worker else "Unknown",
            "worker_role": entry.worker.role if entry.worker else None,
            "worker_rate_cents": entry.worker.hourly_rate_cents if entry.worker else 0,
            "work_date": str(entry.work_date),
            "hours": float(entry.hours),
            "stage": entry.stage,
            "notes": entry.notes,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
        }
        for entry in entries
    ]


@router.post("/api/{id}/time-entries")
async def api_create_time_entry(
    id: int,
    data: TimeEntryCreate,
    db: AsyncSession = Depends(get_db),
):
    """API: Add a time entry for a quote."""
    from datetime import date as date_type
    from decimal import Decimal

    # Validate quote exists
    quote = await service.get_quote(db, id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    # Validate worker exists
    worker = await db.get(Worker, data.worker_id)
    if not worker:
        raise HTTPException(404, "Worker not found")

    # Parse date
    try:
        work_date = date_type.fromisoformat(data.work_date)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")

    # Validate hours
    if data.hours <= 0 or data.hours > 24:
        raise HTTPException(400, "Hours must be between 0 and 24")

    # Validate stage
    valid_stages = [None, "", "setup", "pour", "finish", "cleanup"]
    if data.stage and data.stage not in valid_stages:
        raise HTTPException(400, f"Invalid stage. Must be one of: setup, pour, finish, cleanup")

    entry = TimeEntry(
        quote_id=id,
        worker_id=data.worker_id,
        work_date=work_date,
        hours=Decimal(str(data.hours)),
        stage=data.stage if data.stage else None,
        notes=data.notes if data.notes else None,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    # Reload with worker relationship
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(TimeEntry)
        .where(TimeEntry.id == entry.id)
        .options(selectinload(TimeEntry.worker))
    )
    entry = result.scalar_one()

    return {
        "id": entry.id,
        "quote_id": entry.quote_id,
        "worker_id": entry.worker_id,
        "worker_name": entry.worker.name if entry.worker else "Unknown",
        "worker_role": entry.worker.role if entry.worker else None,
        "worker_rate_cents": entry.worker.hourly_rate_cents if entry.worker else 0,
        "work_date": str(entry.work_date),
        "hours": float(entry.hours),
        "stage": entry.stage,
        "notes": entry.notes,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


@router.put("/api/time-entries/{entry_id}")
async def api_update_time_entry(
    entry_id: int,
    data: TimeEntryUpdate,
    db: AsyncSession = Depends(get_db),
):
    """API: Update a time entry."""
    from datetime import date as date_type
    from decimal import Decimal

    entry = await db.get(TimeEntry, entry_id)
    if not entry:
        raise HTTPException(404, "Time entry not found")

    if data.worker_id is not None:
        worker = await db.get(Worker, data.worker_id)
        if not worker:
            raise HTTPException(404, "Worker not found")
        entry.worker_id = data.worker_id

    if data.work_date is not None:
        try:
            entry.work_date = date_type.fromisoformat(data.work_date)
        except ValueError:
            raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")

    if data.hours is not None:
        if data.hours <= 0 or data.hours > 24:
            raise HTTPException(400, "Hours must be between 0 and 24")
        entry.hours = Decimal(str(data.hours))

    if data.stage is not None:
        valid_stages = ["", "setup", "pour", "finish", "cleanup"]
        if data.stage and data.stage not in valid_stages:
            raise HTTPException(400, f"Invalid stage. Must be one of: setup, pour, finish, cleanup")
        entry.stage = data.stage if data.stage else None

    if data.notes is not None:
        entry.notes = data.notes if data.notes else None

    await db.commit()
    await db.refresh(entry)

    # Reload with worker relationship
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(TimeEntry)
        .where(TimeEntry.id == entry.id)
        .options(selectinload(TimeEntry.worker))
    )
    entry = result.scalar_one()

    return {
        "id": entry.id,
        "quote_id": entry.quote_id,
        "worker_id": entry.worker_id,
        "worker_name": entry.worker.name if entry.worker else "Unknown",
        "worker_role": entry.worker.role if entry.worker else None,
        "worker_rate_cents": entry.worker.hourly_rate_cents if entry.worker else 0,
        "work_date": str(entry.work_date),
        "hours": float(entry.hours),
        "stage": entry.stage,
        "notes": entry.notes,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


@router.delete("/api/time-entries/{entry_id}")
async def api_delete_time_entry(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
):
    """API: Delete a time entry."""
    entry = await db.get(TimeEntry, entry_id)
    if not entry:
        raise HTTPException(404, "Time entry not found")

    await db.delete(entry)
    await db.commit()

    return {"message": "Time entry deleted"}


@router.get("/api/{id}/time-summary")
async def api_time_summary(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """API: Get time tracking summary for a quote — totals by worker and stage."""
    from sqlalchemy.orm import selectinload

    # Get all entries with worker data
    result = await db.execute(
        select(TimeEntry)
        .where(TimeEntry.quote_id == id)
        .options(selectinload(TimeEntry.worker))
    )
    entries = result.scalars().all()

    # Build summaries
    by_worker = {}
    by_stage = {}
    total_hours = 0
    total_cost_cents = 0

    for entry in entries:
        hours = float(entry.hours)
        rate = entry.worker.hourly_rate_cents if entry.worker else 0
        cost_cents = int(hours * rate)
        total_hours += hours
        total_cost_cents += cost_cents

        # By worker
        wname = entry.worker.name if entry.worker else "Unknown"
        wid = entry.worker_id
        if wid not in by_worker:
            by_worker[wid] = {
                "worker_id": wid,
                "worker_name": wname,
                "worker_role": entry.worker.role if entry.worker else None,
                "rate_cents": rate,
                "hours": 0,
                "cost_cents": 0,
            }
        by_worker[wid]["hours"] += hours
        by_worker[wid]["cost_cents"] += cost_cents

        # By stage
        stage = entry.stage or "unassigned"
        if stage not in by_stage:
            by_stage[stage] = {"stage": stage, "hours": 0, "cost_cents": 0}
        by_stage[stage]["hours"] += hours
        by_stage[stage]["cost_cents"] += cost_cents

    return {
        "total_hours": round(total_hours, 2),
        "total_cost_cents": total_cost_cents,
        "entry_count": len(entries),
        "by_worker": list(by_worker.values()),
        "by_stage": list(by_stage.values()),
    }
