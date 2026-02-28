"""
Job Costing Routes — Post-job profitability analysis.
"""

from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.auth import require_login, verify_csrf
from app.core.templates import templates, flash
from app.costing import service as costing_service
from app.quotes.service import get_quote
from app.models import Customer

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


@router.get("/{quote_id}", name="costing:detail")
async def costing_page(
    request: Request,
    quote_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Job costing form for a completed/in-progress job."""
    quote = await get_quote(db, quote_id)
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")

    costing = await costing_service.get_or_create_costing(db, quote)
    await db.commit()

    analysis = costing_service.calculate_analysis(costing, quote)

    # Get customer for display
    customer = await db.get(Customer, quote.customer_id) if quote.customer_id else None

    return templates.TemplateResponse("costing/detail.html", {
        "request": request,
        "quote": quote,
        "customer": customer,
        "costing": costing,
        "analysis": analysis,
    })


@router.post("/{quote_id}", name="costing:save")
async def costing_save(
    request: Request,
    quote_id: int,
    actual_concrete_dollars: float = Form(0),
    actual_concrete_m3: float = Form(0),
    actual_labour_dollars: float = Form(0),
    actual_labour_hours: float = Form(0),
    actual_materials_dollars: float = Form(0),
    actual_pump_dollars: float = Form(0),
    actual_other_dollars: float = Form(0),
    other_description: str = Form(""),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Save job costing data."""
    quote = await get_quote(db, quote_id)
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")

    # Convert dollars to cents
    data = {
        "actual_concrete_cents": int(round(actual_concrete_dollars * 100)),
        "actual_concrete_m3": actual_concrete_m3 if actual_concrete_m3 > 0 else None,
        "actual_labour_cents": int(round(actual_labour_dollars * 100)),
        "actual_labour_hours": actual_labour_hours if actual_labour_hours > 0 else None,
        "actual_materials_cents": int(round(actual_materials_dollars * 100)),
        "actual_pump_cents": int(round(actual_pump_dollars * 100)),
        "actual_other_cents": int(round(actual_other_dollars * 100)),
        "other_description": other_description,
        "notes": notes,
    }

    await costing_service.save_costing(db, quote, data)
    await db.commit()

    flash(request, "Job costing saved successfully.", "success")
    return RedirectResponse(url=f"/costing/{quote_id}", status_code=302)
