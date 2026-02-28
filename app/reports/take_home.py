"""
Calculate proprietor's earnings (take-home earnings).

Proprietor's Earnings = Revenue (ex GST) - Materials (actual cost) - Worker Labour (wages + super + PAYG) - Expenses

Key points:
- GST collected is NOT yours - it goes to the ATO
- Materials are tracked at YOUR actual cost, so markup profit is automatically included
- Worker costs include wages + super (12.5%) + PAYG withheld
- Owner's own labour is NOT deducted - that's part of what they take home
"""

from datetime import date, timedelta
from typing import Optional
from decimal import Decimal

from sqlalchemy import select, func, and_, or_, extract
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import Invoice, Payment, Expense, TimeEntry, Worker, Quote
from app.core.dates import sydney_today


# Default rates for Australian payroll
DEFAULT_SUPER_RATE = 0.125  # 12.5% as of 2025-26 (SG rate effective 1 Jul 2025)
DEFAULT_PAYG_RATE = 0.17    # PAYG withholding rate


async def get_period_dates(period_type: str, reference_date: date = None) -> tuple[date, date]:
    """Get start and end dates for a period."""
    today = reference_date or sydney_today()

    if period_type == "weekly":
        # Week starts Monday
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    elif period_type == "fortnightly":
        # Fortnight - use ISO week, start on even weeks
        week_num = today.isocalendar()[1]
        start_week = week_num if week_num % 2 == 0 else week_num - 1
        # Handle year boundary
        try:
            start = date.fromisocalendar(today.year, start_week, 1)
        except ValueError:
            # If week 0 or invalid, use previous year's last even week
            start = date.fromisocalendar(today.year - 1, 52, 1)
        end = start + timedelta(days=13)
    elif period_type == "monthly":
        start = today.replace(day=1)
        # Last day of month
        if today.month == 12:
            end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
    else:
        raise ValueError(f"Unknown period type: {period_type}")

    return start, end


async def calculate_take_home(
    db: AsyncSession,
    start_date: date,
    end_date: date,
) -> dict:
    """
    Calculate owner's draw for a date range.

    Owner's Draw = Revenue (ex GST) - Materials - Labour (total) - Expenses

    GST collected is NOT included in take-home as it belongs to the ATO.
    Labour includes: wages + super (12.5%) + PAYG withheld.
    Materials are tracked at actual cost (markup profit is automatically in take-home).

    Returns:
        {
            "revenue_inc_gst_cents": int,
            "gst_collected_cents": int,
            "revenue_ex_gst_cents": int,
            "materials_cents": int,
            "labour_wages_cents": int,
            "labour_super_cents": int,
            "labour_payg_cents": int,
            "labour_total_cents": int,
            "expenses_cents": int,
            "take_home_cents": int,
            "jobs_completed": int,
        }
    """
    # Revenue: Payments received in this period (this is inc GST)
    revenue_result = await db.execute(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            and_(
                Payment.payment_date >= start_date,
                Payment.payment_date <= end_date
            )
        )
    )
    revenue_inc_gst_cents = revenue_result.scalar() or 0

    # GST is 1/11th of the inc GST amount (Australian GST = 10%)
    gst_collected_cents = revenue_inc_gst_cents // 11
    revenue_ex_gst_cents = revenue_inc_gst_cents - gst_collected_cents

    # Get quote IDs for payments in this period (to link expenses and labour)
    paid_jobs_result = await db.execute(
        select(Invoice.quote_id)
        .join(Payment, Payment.invoice_id == Invoice.id)
        .where(
            and_(
                Payment.payment_date >= start_date,
                Payment.payment_date <= end_date,
                Invoice.quote_id.isnot(None)
            )
        ).distinct()
    )
    paid_job_ids = [r[0] for r in paid_jobs_result.fetchall() if r[0] is not None]

    # Materials & job expenses: Expenses linked to these jobs (at actual cost)
    materials_cents = 0
    if paid_job_ids:
        expenses_result = await db.execute(
            select(
                func.coalesce(func.sum(Expense.amount_cents), 0)
            ).where(Expense.quote_id.in_(paid_job_ids))
        )
        materials_cents = expenses_result.scalar() or 0

    # General expenses in the period (not linked to specific jobs)
    # Use ex-GST amount since GST on expenses is reclaimable via BAS
    general_expenses_result = await db.execute(
        select(func.coalesce(func.sum(Expense.amount_cents), 0))
        .where(
            and_(
                Expense.expense_date >= start_date,
                Expense.expense_date <= end_date,
                Expense.quote_id.is_(None)
            )
        )
    )
    general_expenses_cents = general_expenses_result.scalar() or 0

    # Labour: Worker hours for these jobs (not the owner)
    # Includes wages + super (12.5%) + PAYG withholding
    labour_wages_cents = 0
    labour_super_cents = 0
    labour_payg_cents = 0

    if paid_job_ids:
        # Get time entries with worker info
        time_result = await db.execute(
            select(TimeEntry, Worker)
            .join(Worker, TimeEntry.worker_id == Worker.id)
            .where(
                and_(
                    TimeEntry.quote_id.in_(paid_job_ids),
                    Worker.role != "owner"  # Exclude owner - their "cost" isn't deducted
                )
            )
        )

        for time_entry, worker in time_result.all():
            # Use cost_rate (actual gross wage), not hourly_rate (sell rate to customer)
            rate = worker.cost_rate_cents or worker.hourly_rate_cents
            if time_entry.hours and rate:
                # Base wages (what the worker earns before tax)
                wages = int(float(time_entry.hours) * rate)
                labour_wages_cents += wages

                # Super contribution (12.5% employer contribution to their super fund)
                super_amount = int(wages * DEFAULT_SUPER_RATE)
                labour_super_cents += super_amount

                # PAYG withholding (tax you withhold and pay to ATO on their behalf)
                # This is an estimate - actual depends on worker's tax declaration
                payg_amount = int(wages * DEFAULT_PAYG_RATE)
                labour_payg_cents += payg_amount

    # PAYG is withheld FROM gross wages, not an additional cost. Only super is extra.
    labour_total_cents = labour_wages_cents + labour_super_cents

    # Calculate owner's draw (take-home)
    # Revenue (ex GST) - Materials - Labour (wages+super) - General Expenses
    take_home_cents = revenue_ex_gst_cents - materials_cents - labour_total_cents - general_expenses_cents

    return {
        "revenue_inc_gst_cents": revenue_inc_gst_cents,
        "gst_collected_cents": gst_collected_cents,
        "revenue_ex_gst_cents": revenue_ex_gst_cents,
        "materials_cents": materials_cents,
        "labour_wages_cents": labour_wages_cents,
        "labour_super_cents": labour_super_cents,
        "labour_payg_cents": labour_payg_cents,
        "labour_total_cents": labour_total_cents,
        "expenses_cents": general_expenses_cents,
        "take_home_cents": take_home_cents,
        "jobs_completed": len(paid_job_ids),
        # Backwards compatibility aliases
        "revenue_cents": revenue_ex_gst_cents,
        "labour_cents": labour_total_cents,
    }


async def get_goal_progress(db: AsyncSession) -> dict:
    """
    Get goal progress for dashboard display.

    Returns:
        {
            "goal_type": str,
            "goal_amount_cents": int,
            "current_cents": int,
            "percentage": float,
            "remaining_cents": int,
            "days_elapsed": int,
            "days_remaining": int,
            "daily_rate_needed_cents": int,
            "status": str,  # "on_track", "behind", "achieved"
            "jobs_completed": int,
            "period_start": date,
            "period_end": date,
        }
    """
    from app.settings.service import get_setting

    # Get goal settings
    goal_type = await get_setting(db, "goals", "goal_type") or "weekly"
    goal_amount_cents = await get_setting(db, "goals", "goal_amount_cents")
    if goal_amount_cents is None:
        goal_amount_cents = 180000  # Default $1,800

    # Get period dates
    start_date, end_date = await get_period_dates(goal_type)
    today = sydney_today()

    # Calculate current take-home
    take_home = await calculate_take_home(db, start_date, end_date)
    current_cents = take_home["take_home_cents"]

    # Calculate progress
    percentage = (current_cents / goal_amount_cents * 100) if goal_amount_cents > 0 else 0
    remaining_cents = max(0, goal_amount_cents - current_cents)

    # Days calculation
    total_days = (end_date - start_date).days + 1
    days_elapsed = min(total_days, (today - start_date).days + 1)
    days_remaining = max(0, (end_date - today).days + 1)

    # Daily rate needed to hit goal
    if days_remaining > 0 and remaining_cents > 0:
        daily_rate_needed_cents = remaining_cents // days_remaining
    else:
        daily_rate_needed_cents = 0

    # Determine status
    if current_cents >= goal_amount_cents:
        status = "achieved"
    else:
        # Are we on track based on days elapsed?
        expected_progress = (days_elapsed / total_days) * goal_amount_cents
        if current_cents >= expected_progress * 0.8:  # Within 80% of expected
            status = "on_track"
        else:
            status = "behind"

    return {
        "goal_type": goal_type,
        "goal_amount_cents": goal_amount_cents,
        "current_cents": current_cents,
        "percentage": min(100, round(percentage, 1)),
        "remaining_cents": remaining_cents,
        "days_elapsed": days_elapsed,
        "days_remaining": days_remaining,
        "daily_rate_needed_cents": daily_rate_needed_cents,
        "status": status,
        "jobs_completed": take_home["jobs_completed"],
        "period_start": start_date,
        "period_end": end_date,
        "breakdown": take_home,  # Include full breakdown for debugging
    }


async def get_annual_projection(db: AsyncSession) -> dict:
    """
    Calculate projected annual owner's draw based on current performance.

    Uses weighted approach:
    - If 3+ months data: 60% recent 3-month average, 40% YTD average
    - If <3 months: YTD average only (with low confidence warning)

    Returns:
        {
            "projected_annual_cents": int,
            "monthly_average_cents": int,
            "ytd_total_cents": int,
            "months_remaining": int,
            "projected_remaining_cents": int,
            "confidence": str,  # "high", "medium", "low", "none"
            "months_data": int,
        }
    """
    from app.models import EarningsSnapshot

    today = sydney_today()
    year = today.year
    current_month = today.month

    # Get all monthly snapshots for this year
    result = await db.execute(
        select(EarningsSnapshot)
        .where(EarningsSnapshot.period_type == "monthly")
        .where(extract('year', EarningsSnapshot.period_start) == year)
        .order_by(EarningsSnapshot.period_start)
    )
    snapshots = result.scalars().all()

    if not snapshots:
        return {
            "projected_annual_cents": 0,
            "monthly_average_cents": 0,
            "ytd_total_cents": 0,
            "months_remaining": 12 - current_month,
            "projected_remaining_cents": 0,
            "confidence": "none",
            "months_data": 0,
        }

    # YTD total and average
    ytd_total = sum(s.take_home_cents for s in snapshots)
    months_with_data = len(snapshots)
    ytd_avg = ytd_total // months_with_data if months_with_data > 0 else 0

    # Recent 3-month average (if available)
    recent_snapshots = snapshots[-3:] if len(snapshots) >= 3 else snapshots
    recent_avg = sum(s.take_home_cents for s in recent_snapshots) // len(recent_snapshots)

    # Blended projection calculation
    if months_with_data >= 3:
        # Weight recent performance more heavily (60% recent, 40% YTD average)
        blended_avg = int(recent_avg * 0.6 + ytd_avg * 0.4)
        confidence = "high" if months_with_data >= 6 else "medium"
    else:
        blended_avg = ytd_avg
        confidence = "low"

    # Project for full year
    months_remaining = 12 - current_month
    projected_remaining = blended_avg * months_remaining
    projected_annual = ytd_total + projected_remaining

    return {
        "projected_annual_cents": projected_annual,
        "monthly_average_cents": blended_avg,
        "ytd_total_cents": ytd_total,
        "months_remaining": months_remaining,
        "projected_remaining_cents": projected_remaining,
        "confidence": confidence,
        "months_data": months_with_data,
    }


# =============================================================================
# SYNCHRONOUS VERSIONS (for Celery tasks)
# =============================================================================

def get_period_dates_sync(period_type: str, reference_date: date = None) -> tuple[date, date]:
    """Synchronous version of get_period_dates."""
    today = reference_date or sydney_today()

    if period_type == "weekly":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    elif period_type == "fortnightly":
        week_num = today.isocalendar()[1]
        start_week = week_num if week_num % 2 == 0 else week_num - 1
        try:
            start = date.fromisocalendar(today.year, start_week, 1)
        except ValueError:
            start = date.fromisocalendar(today.year - 1, 52, 1)
        end = start + timedelta(days=13)
    elif period_type == "monthly":
        start = today.replace(day=1)
        if today.month == 12:
            end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
    else:
        raise ValueError(f"Unknown period type: {period_type}")

    return start, end


def calculate_take_home_sync(
    db: Session,
    start_date: date,
    end_date: date,
) -> dict:
    """
    Synchronous version of calculate_take_home for Celery tasks.

    Owner's Draw = Revenue (ex GST) - Materials - Labour (total) - Expenses
    """
    # Revenue: Payments received in this period (inc GST)
    revenue_result = db.execute(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            and_(
                Payment.payment_date >= start_date,
                Payment.payment_date <= end_date
            )
        )
    )
    revenue_inc_gst_cents = revenue_result.scalar() or 0

    # GST is 1/11th of the inc GST amount
    gst_collected_cents = revenue_inc_gst_cents // 11
    revenue_ex_gst_cents = revenue_inc_gst_cents - gst_collected_cents

    # Get quote IDs for payments in this period
    paid_jobs_result = db.execute(
        select(Invoice.quote_id)
        .join(Payment, Payment.invoice_id == Invoice.id)
        .where(
            and_(
                Payment.payment_date >= start_date,
                Payment.payment_date <= end_date,
                Invoice.quote_id.isnot(None)
            )
        ).distinct()
    )
    paid_job_ids = [r[0] for r in paid_jobs_result.fetchall() if r[0] is not None]

    # Materials & job expenses (at actual cost)
    materials_cents = 0
    if paid_job_ids:
        expenses_result = db.execute(
            select(
                func.coalesce(func.sum(Expense.amount_cents), 0)
            ).where(Expense.quote_id.in_(paid_job_ids))
        )
        materials_cents = expenses_result.scalar() or 0

    # General expenses
    general_expenses_result = db.execute(
        select(func.coalesce(func.sum(Expense.amount_cents), 0))
        .where(
            and_(
                Expense.expense_date >= start_date,
                Expense.expense_date <= end_date,
                Expense.quote_id.is_(None)
            )
        )
    )
    general_expenses_cents = general_expenses_result.scalar() or 0

    # Labour costs (excluding owner) - includes wages + super + PAYG
    labour_wages_cents = 0
    labour_super_cents = 0
    labour_payg_cents = 0

    if paid_job_ids:
        time_result = db.execute(
            select(TimeEntry, Worker)
            .join(Worker, TimeEntry.worker_id == Worker.id)
            .where(
                and_(
                    TimeEntry.quote_id.in_(paid_job_ids),
                    Worker.role != "owner"
                )
            )
        )

        for time_entry, worker in time_result.all():
            rate = worker.cost_rate_cents or worker.hourly_rate_cents
            if time_entry.hours and rate:
                wages = int(float(time_entry.hours) * rate)
                labour_wages_cents += wages
                labour_super_cents += int(wages * DEFAULT_SUPER_RATE)
                labour_payg_cents += int(wages * DEFAULT_PAYG_RATE)

    # PAYG is withheld FROM gross wages, not an additional cost. Only super is extra.
    labour_total_cents = labour_wages_cents + labour_super_cents

    # Calculate owner's draw
    take_home_cents = revenue_ex_gst_cents - materials_cents - labour_total_cents - general_expenses_cents

    return {
        "revenue_inc_gst_cents": revenue_inc_gst_cents,
        "gst_collected_cents": gst_collected_cents,
        "revenue_ex_gst_cents": revenue_ex_gst_cents,
        "materials_cents": materials_cents,
        "labour_wages_cents": labour_wages_cents,
        "labour_super_cents": labour_super_cents,
        "labour_payg_cents": labour_payg_cents,
        "labour_total_cents": labour_total_cents,
        "expenses_cents": general_expenses_cents,
        "take_home_cents": take_home_cents,
        "jobs_completed": len(paid_job_ids),
        # Backwards compatibility
        "revenue_cents": revenue_ex_gst_cents,
        "labour_cents": labour_total_cents,
    }
