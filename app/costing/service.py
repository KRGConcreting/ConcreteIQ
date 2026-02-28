"""
Job Costing Service — Post-job profitability analysis.

Compares quoted revenue against actual costs to calculate
profit margin, cost per m², and hourly rate achieved.
"""

from typing import Optional
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobCosting, Quote
from app.core.dates import sydney_now


async def get_costing(db: AsyncSession, quote_id: int) -> Optional[JobCosting]:
    """Get job costing for a quote."""
    result = await db.execute(
        select(JobCosting).where(JobCosting.quote_id == quote_id)
    )
    return result.scalar_one_or_none()


async def get_or_create_costing(db: AsyncSession, quote: Quote) -> JobCosting:
    """Get existing costing or create a new one pre-filled from the quote."""
    existing = await get_costing(db, quote.id)
    if existing:
        return existing

    costing = JobCosting(
        quote_id=quote.id,
        quoted_total_cents=quote.total_cents,
    )
    db.add(costing)
    await db.flush()
    return costing


async def save_costing(
    db: AsyncSession,
    quote: Quote,
    data: dict,
) -> JobCosting:
    """
    Save or update job costing data.

    Automatically calculates actual_total_cents, profit_cents, and margin_percent.
    """
    costing = await get_or_create_costing(db, quote)

    # Update cost fields
    costing.actual_concrete_cents = data.get("actual_concrete_cents", 0)
    costing.actual_concrete_m3 = data.get("actual_concrete_m3") or None
    costing.actual_labour_cents = data.get("actual_labour_cents", 0)
    costing.actual_labour_hours = data.get("actual_labour_hours") or None
    costing.actual_materials_cents = data.get("actual_materials_cents", 0)
    costing.actual_pump_cents = data.get("actual_pump_cents", 0)
    costing.actual_other_cents = data.get("actual_other_cents", 0)
    costing.other_description = data.get("other_description") or None
    costing.notes = data.get("notes") or None

    # Snapshot quoted total (in case quote was amended)
    costing.quoted_total_cents = quote.total_cents

    # Calculate totals
    costing.actual_total_cents = (
        costing.actual_concrete_cents
        + costing.actual_labour_cents
        + costing.actual_materials_cents
        + costing.actual_pump_cents
        + costing.actual_other_cents
    )

    costing.profit_cents = costing.quoted_total_cents - costing.actual_total_cents

    if costing.quoted_total_cents > 0:
        costing.margin_percent = Decimal(
            (costing.profit_cents / costing.quoted_total_cents) * 100
        ).quantize(Decimal("0.01"))
    else:
        costing.margin_percent = Decimal("0.00")

    costing.updated_at = sydney_now()
    return costing


def calculate_analysis(costing: JobCosting, quote: Quote) -> dict:
    """
    Calculate detailed analysis metrics from costing data.

    Returns dict with all the useful ratios and breakdowns.
    """
    result = {
        "quoted_total_cents": costing.quoted_total_cents,
        "actual_total_cents": costing.actual_total_cents,
        "profit_cents": costing.profit_cents,
        "margin_percent": float(costing.margin_percent or 0),
    }

    # Cost per m² (if we have area from calculator input)
    area = None
    if quote.calculator_input:
        area = quote.calculator_input.get("slab_area")
    if area and area > 0 and costing.actual_total_cents > 0:
        result["cost_per_m2_cents"] = int(costing.actual_total_cents / area)
        result["revenue_per_m2_cents"] = int(costing.quoted_total_cents / area)
        result["area_m2"] = area
    else:
        result["cost_per_m2_cents"] = 0
        result["revenue_per_m2_cents"] = 0
        result["area_m2"] = 0

    # Hourly rate achieved
    if costing.actual_labour_hours and costing.actual_labour_hours > 0:
        result["hourly_rate_achieved_cents"] = int(
            costing.profit_cents / float(costing.actual_labour_hours)
        )
        result["labour_hours"] = float(costing.actual_labour_hours)
    else:
        result["hourly_rate_achieved_cents"] = 0
        result["labour_hours"] = 0

    # Cost breakdown percentages
    if costing.actual_total_cents > 0:
        total = costing.actual_total_cents
        result["concrete_pct"] = round(costing.actual_concrete_cents / total * 100, 1)
        result["labour_pct"] = round(costing.actual_labour_cents / total * 100, 1)
        result["materials_pct"] = round(costing.actual_materials_cents / total * 100, 1)
        result["pump_pct"] = round(costing.actual_pump_cents / total * 100, 1)
        result["other_pct"] = round(costing.actual_other_cents / total * 100, 1)
    else:
        result["concrete_pct"] = result["labour_pct"] = result["materials_pct"] = 0
        result["pump_pct"] = result["other_pct"] = 0

    return result


async def get_costing_summary_by_type(db: AsyncSession) -> list[dict]:
    """
    Get aggregate costing data grouped by job_type for reporting.

    Returns list of dicts with job_type, count, avg_margin, total_profit, etc.
    """
    result = await db.execute(
        select(
            Quote.job_type,
            func.count(JobCosting.id).label("job_count"),
            func.sum(JobCosting.quoted_total_cents).label("total_revenue"),
            func.sum(JobCosting.actual_total_cents).label("total_cost"),
            func.sum(JobCosting.profit_cents).label("total_profit"),
            func.avg(JobCosting.margin_percent).label("avg_margin"),
        )
        .join(Quote, JobCosting.quote_id == Quote.id)
        .where(JobCosting.actual_total_cents > 0)  # Only jobs with actual costs entered
        .group_by(Quote.job_type)
        .order_by(func.sum(JobCosting.profit_cents).desc())
    )

    return [
        {
            "job_type": row.job_type or "Unclassified",
            "job_count": row.job_count,
            "total_revenue_cents": int(row.total_revenue or 0),
            "total_cost_cents": int(row.total_cost or 0),
            "total_profit_cents": int(row.total_profit or 0),
            "avg_margin": round(float(row.avg_margin or 0), 1),
        }
        for row in result.all()
    ]
