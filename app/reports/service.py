"""
Reports service — Business analytics and reporting.

Calculates KPIs, conversion rates, revenue metrics, and generates reports.
"""

from datetime import date, datetime, timedelta
from typing import Optional
from decimal import Decimal
import csv
import io

from sqlalchemy import select, func, and_, or_, case, extract
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Customer, Quote, Invoice, Payment, ActivityLog,
    Worker, JobAssignment, Expense
)
from app.core.dates import sydney_now, sydney_today, SYDNEY_TZ


# =============================================================================
# DATE HELPERS
# =============================================================================

def get_period_dates(period: str = "month") -> tuple[date, date]:
    """
    Get start and end dates for a period.

    Periods:
    - 'today': Today only
    - 'week': Current week (Mon-Sun)
    - 'month': Current month
    - 'quarter': Current quarter
    - 'year': Current year
    - 'last_month': Previous month
    - 'last_quarter': Previous quarter
    """
    today = sydney_today()

    if period == "today":
        return today, today

    elif period == "week":
        # Start of week (Monday)
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return start, end

    elif period == "month":
        start = today.replace(day=1)
        # End of month
        if today.month == 12:
            end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        return start, end

    elif period == "quarter":
        quarter = (today.month - 1) // 3
        start = today.replace(month=quarter * 3 + 1, day=1)
        end_month = quarter * 3 + 3
        if end_month == 12:
            end = today.replace(month=12, day=31)
        else:
            end = today.replace(month=end_month + 1, day=1) - timedelta(days=1)
        return start, end

    elif period == "year":
        start = today.replace(month=1, day=1)
        end = today.replace(month=12, day=31)
        return start, end

    elif period == "last_month":
        first_of_month = today.replace(day=1)
        end = first_of_month - timedelta(days=1)
        start = end.replace(day=1)
        return start, end

    elif period == "last_quarter":
        quarter = (today.month - 1) // 3
        if quarter == 0:
            # Previous year Q4
            start = today.replace(year=today.year - 1, month=10, day=1)
            end = today.replace(year=today.year - 1, month=12, day=31)
        else:
            start_month = (quarter - 1) * 3 + 1
            end_month = quarter * 3
            start = today.replace(month=start_month, day=1)
            end = today.replace(month=end_month + 1, day=1) - timedelta(days=1)
        return start, end

    else:
        # Default to month
        return get_period_dates("month")


def get_previous_period_dates(period: str = "month") -> tuple[date, date]:
    """Get the previous equivalent period for comparison."""
    start, end = get_period_dates(period)
    duration = (end - start).days + 1

    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=duration - 1)

    return prev_start, prev_end


# =============================================================================
# DASHBOARD STATS
# =============================================================================

async def get_dashboard_stats(db: AsyncSession, period: str = "month") -> dict:
    """
    Get all KPIs for the dashboard.

    Returns:
        dict with keys:
        - quotes_this_month: int (count)
        - quotes_value_cents: int (total value)
        - conversion_rate: float (percentage)
        - revenue_this_month_cents: int
        - outstanding_cents: int
        - jobs_this_week: int
        - overdue_count: int
        - trends: dict with comparison to previous period
    """
    today = sydney_today()
    start_date, end_date = get_period_dates(period)
    prev_start, prev_end = get_previous_period_dates(period)

    # Quotes this period
    quotes_result = await db.execute(
        select(
            func.count(Quote.id),
            func.coalesce(func.sum(Quote.total_cents), 0)
        ).where(
            and_(
                func.date(Quote.created_at) >= start_date,
                func.date(Quote.created_at) <= end_date
            )
        )
    )
    quotes_count, quotes_value = quotes_result.one()

    # Quotes sent and accepted (for conversion rate)
    sent_result = await db.execute(
        select(func.count(Quote.id)).where(
            and_(
                Quote.sent_at.isnot(None),
                func.date(Quote.sent_at) >= start_date,
                func.date(Quote.sent_at) <= end_date
            )
        )
    )
    sent_count = sent_result.scalar() or 0

    accepted_result = await db.execute(
        select(func.count(Quote.id)).where(
            and_(
                Quote.accepted_at.isnot(None),
                func.date(Quote.accepted_at) >= start_date,
                func.date(Quote.accepted_at) <= end_date
            )
        )
    )
    accepted_count = accepted_result.scalar() or 0

    conversion_rate = (accepted_count / sent_count * 100) if sent_count > 0 else 0.0

    # Revenue this period (from paid invoices)
    revenue_result = await db.execute(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            and_(
                Payment.payment_date >= start_date,
                Payment.payment_date <= end_date
            )
        )
    )
    revenue_cents = revenue_result.scalar() or 0

    # Outstanding invoices
    outstanding_result = await db.execute(
        select(
            func.count(Invoice.id),
            func.coalesce(func.sum(Invoice.total_cents - Invoice.paid_cents), 0)
        ).where(
            Invoice.status.in_(["sent", "viewed", "partial", "overdue"])
        )
    )
    unpaid_count, outstanding_cents = outstanding_result.one()

    # Overdue invoices
    overdue_result = await db.execute(
        select(func.count(Invoice.id)).where(
            and_(
                Invoice.status.in_(["sent", "viewed", "partial"]),
                Invoice.due_date < today
            )
        )
    )
    overdue_count = overdue_result.scalar() or 0

    # Jobs this week (confirmed quotes)
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    jobs_result = await db.execute(
        select(func.count(Quote.id)).where(
            and_(
                Quote.confirmed_start_date >= week_start,
                Quote.confirmed_start_date <= week_end,
                Quote.status.in_(["accepted", "confirmed", "pour_stage"])
            )
        )
    )
    jobs_this_week = jobs_result.scalar() or 0

    # Previous period for trends
    prev_revenue_result = await db.execute(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            and_(
                Payment.payment_date >= prev_start,
                Payment.payment_date <= prev_end
            )
        )
    )
    prev_revenue_cents = prev_revenue_result.scalar() or 0

    prev_quotes_result = await db.execute(
        select(func.count(Quote.id)).where(
            and_(
                func.date(Quote.created_at) >= prev_start,
                func.date(Quote.created_at) <= prev_end
            )
        )
    )
    prev_quotes_count = prev_quotes_result.scalar() or 0

    # Calculate trends
    revenue_trend = 0
    if prev_revenue_cents > 0:
        revenue_trend = ((revenue_cents - prev_revenue_cents) / prev_revenue_cents) * 100
    elif revenue_cents > 0:
        revenue_trend = 100

    quotes_trend = 0
    if prev_quotes_count > 0:
        quotes_trend = ((quotes_count - prev_quotes_count) / prev_quotes_count) * 100
    elif quotes_count > 0:
        quotes_trend = 100

    # Expenses this period
    expense_result = await db.execute(
        select(func.coalesce(func.sum(Expense.amount_cents + Expense.gst_cents), 0)).where(
            and_(
                Expense.expense_date >= start_date,
                Expense.expense_date <= end_date
            )
        )
    )
    expense_cents = expense_result.scalar() or 0

    # Profit calculation
    profit_cents = revenue_cents - expense_cents
    profit_margin = (profit_cents / revenue_cents * 100) if revenue_cents > 0 else 0

    # YTD Revenue
    year_start = today.replace(month=1, day=1)
    ytd_result = await db.execute(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.payment_date >= year_start
        )
    )
    ytd_revenue_cents = ytd_result.scalar() or 0

    # Overdue amount
    overdue_amount_result = await db.execute(
        select(func.coalesce(func.sum(Invoice.total_cents - Invoice.paid_cents), 0)).where(
            and_(
                Invoice.status.in_(["sent", "viewed", "partial"]),
                Invoice.due_date < today
            )
        )
    )
    overdue_cents = overdue_amount_result.scalar() or 0

    return {
        "quotes_this_month": quotes_count,
        "quotes_value_cents": quotes_value,
        "quotes_sent": sent_count,
        "quotes_accepted": accepted_count,
        "conversion_rate": round(conversion_rate, 1),
        "revenue_this_month_cents": revenue_cents,
        "expense_cents": expense_cents,
        "profit_cents": profit_cents,
        "profit_margin": round(profit_margin, 1),
        "outstanding_cents": outstanding_cents,
        "invoices_unpaid": unpaid_count,
        "overdue_count": overdue_count,
        "overdue_cents": overdue_cents,
        "jobs_this_week": jobs_this_week,
        "ytd_revenue_cents": ytd_revenue_cents,
        "trends": {
            "revenue_percent": round(revenue_trend, 1),
            "quotes_percent": round(quotes_trend, 1),
        },
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
    }


# =============================================================================
# QUOTE STATS
# =============================================================================

async def get_quote_stats(
    db: AsyncSession,
    start_date: date,
    end_date: date
) -> dict:
    """
    Get quote statistics for conversion analysis.

    Returns:
        dict with:
        - funnel: dict with draft, sent, viewed, accepted, declined counts
        - conversion_rate: float
        - avg_time_to_accept_days: float
        - decline_reasons: list of {reason, count}
        - by_suburb: list of {suburb, count, value_cents}
        - by_month: list of {month, sent, accepted, rate}
    """
    # Funnel counts
    funnel_result = await db.execute(
        select(
            Quote.status,
            func.count(Quote.id),
            func.coalesce(func.sum(Quote.total_cents), 0)
        ).where(
            and_(
                func.date(Quote.created_at) >= start_date,
                func.date(Quote.created_at) <= end_date
            )
        ).group_by(Quote.status)
    )

    funnel = {
        "draft": {"count": 0, "value_cents": 0},
        "sent": {"count": 0, "value_cents": 0},
        "viewed": {"count": 0, "value_cents": 0},
        "accepted": {"count": 0, "value_cents": 0},
        "declined": {"count": 0, "value_cents": 0},
        "expired": {"count": 0, "value_cents": 0},
        "confirmed": {"count": 0, "value_cents": 0},
        "pour_stage": {"count": 0, "value_cents": 0},
        "pending_completion": {"count": 0, "value_cents": 0},
        "completed": {"count": 0, "value_cents": 0},
    }

    total_sent = 0
    total_accepted = 0

    for status, count, value in funnel_result.all():
        if status in funnel:
            funnel[status] = {"count": count, "value_cents": value}
        if status in ["sent", "viewed", "accepted", "declined", "expired", "confirmed", "pour_stage", "pending_completion", "completed"]:
            total_sent += count
        if status in ["accepted", "confirmed", "pour_stage", "pending_completion", "completed"]:
            total_accepted += count

    conversion_rate = (total_accepted / total_sent * 100) if total_sent > 0 else 0.0

    # Average time to accept
    time_result = await db.execute(
        select(
            func.avg(
                extract('epoch', Quote.accepted_at) - extract('epoch', Quote.sent_at)
            )
        ).where(
            and_(
                Quote.accepted_at.isnot(None),
                Quote.sent_at.isnot(None),
                func.date(Quote.accepted_at) >= start_date,
                func.date(Quote.accepted_at) <= end_date
            )
        )
    )
    avg_seconds = time_result.scalar()
    avg_days = (avg_seconds / 86400) if avg_seconds else 0

    # Decline reasons
    decline_result = await db.execute(
        select(
            Quote.decline_reason,
            func.count(Quote.id)
        ).where(
            and_(
                Quote.declined_at.isnot(None),
                func.date(Quote.declined_at) >= start_date,
                func.date(Quote.declined_at) <= end_date
            )
        ).group_by(Quote.decline_reason)
        .order_by(func.count(Quote.id).desc())
    )

    decline_reasons = []
    for reason, count in decline_result.all():
        decline_reasons.append({
            "reason": reason or "No reason given",
            "count": count
        })

    # By suburb (extract from job_address)
    # We'll use the city field from the customer for simplicity
    suburb_result = await db.execute(
        select(
            Customer.city,
            func.count(Quote.id),
            func.coalesce(func.sum(Quote.total_cents), 0)
        ).join(Customer, Quote.customer_id == Customer.id)
        .where(
            and_(
                func.date(Quote.created_at) >= start_date,
                func.date(Quote.created_at) <= end_date
            )
        ).group_by(Customer.city)
        .order_by(func.count(Quote.id).desc())
        .limit(15)
    )

    by_suburb = []
    for suburb, count, value in suburb_result.all():
        by_suburb.append({
            "suburb": suburb or "Unknown",
            "count": count,
            "value_cents": value
        })

    # By month
    by_month = await _get_quotes_by_month(db, start_date, end_date)

    return {
        "funnel": funnel,
        "total_sent": total_sent,
        "total_accepted": total_accepted,
        "conversion_rate": round(conversion_rate, 1),
        "avg_time_to_accept_days": round(avg_days, 1),
        "decline_reasons": decline_reasons,
        "by_suburb": by_suburb,
        "by_month": by_month,
    }


async def _get_quotes_by_month(
    db: AsyncSession,
    start_date: date,
    end_date: date
) -> list[dict]:
    """Get monthly quote stats for trends."""
    # Get sent quotes by month
    sent_result = await db.execute(
        select(
            extract('year', Quote.sent_at).label('year'),
            extract('month', Quote.sent_at).label('month'),
            func.count(Quote.id)
        ).where(
            and_(
                Quote.sent_at.isnot(None),
                func.date(Quote.sent_at) >= start_date,
                func.date(Quote.sent_at) <= end_date
            )
        ).group_by('year', 'month')
        .order_by('year', 'month')
    )

    sent_by_month = {}
    for year, month, count in sent_result.all():
        key = f"{int(year)}-{int(month):02d}"
        sent_by_month[key] = count

    # Get accepted quotes by month
    accepted_result = await db.execute(
        select(
            extract('year', Quote.accepted_at).label('year'),
            extract('month', Quote.accepted_at).label('month'),
            func.count(Quote.id)
        ).where(
            and_(
                Quote.accepted_at.isnot(None),
                func.date(Quote.accepted_at) >= start_date,
                func.date(Quote.accepted_at) <= end_date
            )
        ).group_by('year', 'month')
        .order_by('year', 'month')
    )

    accepted_by_month = {}
    for year, month, count in accepted_result.all():
        key = f"{int(year)}-{int(month):02d}"
        accepted_by_month[key] = count

    # Combine
    all_months = sorted(set(sent_by_month.keys()) | set(accepted_by_month.keys()))
    by_month = []
    for month_key in all_months:
        sent = sent_by_month.get(month_key, 0)
        accepted = accepted_by_month.get(month_key, 0)
        rate = (accepted / sent * 100) if sent > 0 else 0
        by_month.append({
            "month": month_key,
            "sent": sent,
            "accepted": accepted,
            "rate": round(rate, 1)
        })

    return by_month


# =============================================================================
# REVENUE STATS
# =============================================================================

async def get_revenue_stats(
    db: AsyncSession,
    start_date: date,
    end_date: date
) -> dict:
    """
    Get revenue statistics.

    Returns:
        dict with:
        - total_revenue_cents: int
        - by_month: list of {month, revenue_cents}
        - by_stage: dict with booking, prepour, completion, variation totals
        - outstanding_cents: int
        - collected_cents: int
        - avg_job_value_cents: int
        - top_customers: list of {customer_id, name, revenue_cents}
    """
    # Total revenue in period
    revenue_result = await db.execute(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            and_(
                Payment.payment_date >= start_date,
                Payment.payment_date <= end_date
            )
        )
    )
    total_revenue_cents = revenue_result.scalar() or 0

    # Revenue by month
    monthly_result = await db.execute(
        select(
            extract('year', Payment.payment_date).label('year'),
            extract('month', Payment.payment_date).label('month'),
            func.sum(Payment.amount_cents)
        ).where(
            and_(
                Payment.payment_date >= start_date,
                Payment.payment_date <= end_date
            )
        ).group_by('year', 'month')
        .order_by('year', 'month')
    )

    by_month = []
    for year, month, amount in monthly_result.all():
        by_month.append({
            "month": f"{int(year)}-{int(month):02d}",
            "revenue_cents": int(amount or 0)
        })

    # Revenue by stage
    stage_result = await db.execute(
        select(
            Invoice.stage,
            func.sum(Payment.amount_cents)
        ).join(Payment, Payment.invoice_id == Invoice.id)
        .where(
            and_(
                Payment.payment_date >= start_date,
                Payment.payment_date <= end_date
            )
        ).group_by(Invoice.stage)
    )

    by_stage = {
        "booking": 0,
        "prepour": 0,
        "completion": 0,
        "variation": 0,
        "manual": 0,
        "other": 0,
    }
    for stage, amount in stage_result.all():
        if stage in by_stage:
            by_stage[stage] = int(amount or 0)
        else:
            by_stage["other"] += int(amount or 0)

    # Outstanding vs collected (all time)
    outstanding_result = await db.execute(
        select(func.coalesce(func.sum(Invoice.total_cents - Invoice.paid_cents), 0)).where(
            Invoice.status.in_(["sent", "viewed", "partial", "overdue"])
        )
    )
    outstanding_cents = outstanding_result.scalar() or 0

    collected_result = await db.execute(
        select(func.coalesce(func.sum(Invoice.paid_cents), 0)).where(
            Invoice.paid_cents > 0
        )
    )
    collected_cents = collected_result.scalar() or 0

    # Average job value (accepted quotes)
    avg_result = await db.execute(
        select(func.avg(Quote.total_cents)).where(
            and_(
                Quote.status.in_(["accepted", "confirmed", "pour_stage", "pending_completion", "completed"]),
                func.date(Quote.accepted_at) >= start_date,
                func.date(Quote.accepted_at) <= end_date
            )
        )
    )
    avg_job_value = avg_result.scalar() or 0

    # Top customers by revenue
    top_result = await db.execute(
        select(
            Customer.id,
            Customer.name,
            func.sum(Payment.amount_cents).label('total')
        ).join(Invoice, Invoice.customer_id == Customer.id)
        .join(Payment, Payment.invoice_id == Invoice.id)
        .where(
            and_(
                Payment.payment_date >= start_date,
                Payment.payment_date <= end_date
            )
        ).group_by(Customer.id, Customer.name)
        .order_by(func.sum(Payment.amount_cents).desc())
        .limit(10)
    )

    top_customers = []
    for customer_id, name, total in top_result.all():
        top_customers.append({
            "customer_id": customer_id,
            "name": name,
            "revenue_cents": int(total or 0)
        })

    return {
        "total_revenue_cents": total_revenue_cents,
        "by_month": by_month,
        "by_stage": by_stage,
        "outstanding_cents": outstanding_cents,
        "collected_cents": collected_cents,
        "avg_job_value_cents": int(avg_job_value),
        "top_customers": top_customers,
    }


# =============================================================================
# JOB STATS
# =============================================================================

async def get_job_stats(
    db: AsyncSession,
    start_date: date,
    end_date: date
) -> dict:
    """
    Get job statistics.

    Returns:
        dict with:
        - completed_count: int
        - completed_value_cents: int
        - by_suburb: list of {suburb, count, value_cents}
        - by_worker: list of {worker_id, name, job_count}
        - upcoming: list of upcoming jobs
        - avg_duration_days: float (quote to completion)
    """
    # Completed jobs
    completed_result = await db.execute(
        select(
            func.count(Quote.id),
            func.coalesce(func.sum(Quote.total_cents), 0)
        ).where(
            and_(
                Quote.completed_date >= start_date,
                Quote.completed_date <= end_date
            )
        )
    )
    completed_count, completed_value = completed_result.one()

    # Jobs by suburb
    suburb_result = await db.execute(
        select(
            Customer.city,
            func.count(Quote.id),
            func.coalesce(func.sum(Quote.total_cents), 0)
        ).join(Customer, Quote.customer_id == Customer.id)
        .where(
            and_(
                Quote.status.in_(["confirmed", "pour_stage", "pending_completion", "completed"]),
                or_(
                    and_(
                        Quote.confirmed_start_date >= start_date,
                        Quote.confirmed_start_date <= end_date
                    ),
                    and_(
                        Quote.completed_date >= start_date,
                        Quote.completed_date <= end_date
                    )
                )
            )
        ).group_by(Customer.city)
        .order_by(func.count(Quote.id).desc())
        .limit(15)
    )

    by_suburb = []
    for suburb, count, value in suburb_result.all():
        by_suburb.append({
            "suburb": suburb or "Unknown",
            "count": count,
            "value_cents": value
        })

    # Jobs by worker
    worker_result = await db.execute(
        select(
            Worker.id,
            Worker.name,
            func.count(JobAssignment.id)
        ).join(JobAssignment, JobAssignment.worker_id == Worker.id)
        .join(Quote, Quote.id == JobAssignment.quote_id)
        .where(
            and_(
                Quote.status.in_(["confirmed", "pour_stage", "pending_completion", "completed"]),
                or_(
                    and_(
                        Quote.confirmed_start_date >= start_date,
                        Quote.confirmed_start_date <= end_date
                    ),
                    and_(
                        Quote.completed_date >= start_date,
                        Quote.completed_date <= end_date
                    )
                )
            )
        ).group_by(Worker.id, Worker.name)
        .order_by(func.count(JobAssignment.id).desc())
    )

    by_worker = []
    for worker_id, name, count in worker_result.all():
        by_worker.append({
            "worker_id": worker_id,
            "name": name,
            "job_count": count
        })

    # Upcoming jobs
    today = sydney_today()
    upcoming_result = await db.execute(
        select(Quote)
        .join(Customer, Quote.customer_id == Customer.id)
        .where(
            and_(
                Quote.confirmed_start_date >= today,
                Quote.status.in_(["accepted", "confirmed", "pour_stage"])
            )
        ).order_by(Quote.confirmed_start_date)
        .limit(10)
    )

    upcoming = []
    for quote in upcoming_result.scalars().all():
        upcoming.append({
            "quote_id": quote.id,
            "quote_number": quote.quote_number,
            "customer_id": quote.customer_id,
            "job_name": quote.job_name,
            "scheduled_date": quote.confirmed_start_date,
            "total_cents": quote.total_cents,
        })

    # Average duration (quote creation to completion)
    duration_result = await db.execute(
        select(
            func.avg(Quote.completed_date - func.date(Quote.created_at))
        ).where(
            and_(
                Quote.completed_date.isnot(None),
                Quote.completed_date >= start_date,
                Quote.completed_date <= end_date
            )
        )
    )
    avg_duration = duration_result.scalar()
    avg_duration_days = float(avg_duration.days) if avg_duration else 0

    return {
        "completed_count": completed_count,
        "completed_value_cents": completed_value,
        "by_suburb": by_suburb,
        "by_worker": by_worker,
        "upcoming": upcoming,
        "avg_duration_days": round(avg_duration_days, 1),
    }


# =============================================================================
# CUSTOMER STATS
# =============================================================================

async def get_customer_stats(
    db: AsyncSession,
    start_date: date,
    end_date: date
) -> dict:
    """
    Get customer statistics.

    Returns:
        dict with:
        - new_count: int (new customers in period)
        - total_count: int (all customers)
        - repeat_count: int (customers with 2+ quotes)
        - top_by_revenue: list of {customer_id, name, revenue_cents, quote_count}
        - by_location: list of {city, count}
        - by_source: list of {source, count}
    """
    # New customers in period
    new_result = await db.execute(
        select(func.count(Customer.id)).where(
            and_(
                func.date(Customer.created_at) >= start_date,
                func.date(Customer.created_at) <= end_date
            )
        )
    )
    new_count = new_result.scalar() or 0

    # Total customers
    total_result = await db.execute(select(func.count(Customer.id)))
    total_count = total_result.scalar() or 0

    # Repeat customers (2+ quotes)
    repeat_result = await db.execute(
        select(func.count()).select_from(
            select(Customer.id)
            .join(Quote, Quote.customer_id == Customer.id)
            .group_by(Customer.id)
            .having(func.count(Quote.id) >= 2)
            .subquery()
        )
    )
    repeat_count = repeat_result.scalar() or 0

    # Top customers by lifetime revenue
    top_result = await db.execute(
        select(
            Customer.id,
            Customer.name,
            func.coalesce(func.sum(Payment.amount_cents), 0).label('revenue'),
            func.count(func.distinct(Quote.id)).label('quote_count')
        ).outerjoin(Invoice, Invoice.customer_id == Customer.id)
        .outerjoin(Payment, Payment.invoice_id == Invoice.id)
        .outerjoin(Quote, Quote.customer_id == Customer.id)
        .group_by(Customer.id, Customer.name)
        .order_by(func.coalesce(func.sum(Payment.amount_cents), 0).desc())
        .limit(10)
    )

    top_by_revenue = []
    for customer_id, name, revenue, quote_count in top_result.all():
        top_by_revenue.append({
            "customer_id": customer_id,
            "name": name,
            "revenue_cents": int(revenue or 0),
            "quote_count": quote_count or 0
        })

    # By location
    location_result = await db.execute(
        select(
            Customer.city,
            func.count(Customer.id)
        ).group_by(Customer.city)
        .order_by(func.count(Customer.id).desc())
        .limit(15)
    )

    by_location = []
    for city, count in location_result.all():
        by_location.append({
            "city": city or "Unknown",
            "count": count
        })

    # By source
    source_result = await db.execute(
        select(
            Customer.source,
            func.count(Customer.id)
        ).where(Customer.source.isnot(None))
        .group_by(Customer.source)
        .order_by(func.count(Customer.id).desc())
    )

    by_source = []
    for source, count in source_result.all():
        by_source.append({
            "source": source or "Unknown",
            "count": count
        })

    return {
        "new_count": new_count,
        "total_count": total_count,
        "repeat_count": repeat_count,
        "top_by_revenue": top_by_revenue,
        "by_location": by_location,
        "by_source": by_source,
    }


# =============================================================================
# CSV EXPORT
# =============================================================================

async def export_quotes_csv(
    db: AsyncSession,
    start_date: date,
    end_date: date
) -> str:
    """Export quotes to CSV string."""
    result = await db.execute(
        select(Quote, Customer)
        .join(Customer, Quote.customer_id == Customer.id)
        .where(
            and_(
                func.date(Quote.created_at) >= start_date,
                func.date(Quote.created_at) <= end_date
            )
        ).order_by(Quote.created_at.desc())
    )

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Quote Number",
        "Date",
        "Customer",
        "Job Name",
        "Job Address",
        "Status",
        "Subtotal",
        "GST",
        "Total",
        "Sent Date",
        "Accepted Date",
        "Declined Date",
        "Decline Reason",
    ])

    # Data
    for quote, customer in result.all():
        writer.writerow([
            quote.quote_number,
            quote.quote_date.isoformat() if quote.quote_date else "",
            customer.name,
            quote.job_name or "",
            quote.job_address or "",
            quote.status,
            f"{quote.subtotal_cents / 100:.2f}",
            f"{quote.gst_cents / 100:.2f}",
            f"{quote.total_cents / 100:.2f}",
            quote.sent_at.date().isoformat() if quote.sent_at else "",
            quote.accepted_at.date().isoformat() if quote.accepted_at else "",
            quote.declined_at.date().isoformat() if quote.declined_at else "",
            quote.decline_reason or "",
        ])

    return output.getvalue()


async def export_invoices_csv(
    db: AsyncSession,
    start_date: date,
    end_date: date
) -> str:
    """Export invoices to CSV string."""
    result = await db.execute(
        select(Invoice, Customer)
        .join(Customer, Invoice.customer_id == Customer.id)
        .where(
            and_(
                func.date(Invoice.created_at) >= start_date,
                func.date(Invoice.created_at) <= end_date
            )
        ).order_by(Invoice.created_at.desc())
    )

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Invoice Number",
        "Issue Date",
        "Due Date",
        "Customer",
        "Description",
        "Stage",
        "Status",
        "Subtotal",
        "GST",
        "Total",
        "Paid",
        "Balance",
        "Paid Date",
    ])

    # Data
    for invoice, customer in result.all():
        balance = invoice.total_cents - invoice.paid_cents
        writer.writerow([
            invoice.invoice_number,
            invoice.issue_date.isoformat() if invoice.issue_date else "",
            invoice.due_date.isoformat() if invoice.due_date else "",
            customer.name,
            invoice.description or "",
            invoice.stage or "",
            invoice.status,
            f"{invoice.subtotal_cents / 100:.2f}",
            f"{invoice.gst_cents / 100:.2f}",
            f"{invoice.total_cents / 100:.2f}",
            f"{invoice.paid_cents / 100:.2f}",
            f"{balance / 100:.2f}",
            invoice.paid_date.isoformat() if invoice.paid_date else "",
        ])

    return output.getvalue()


async def export_payments_csv(
    db: AsyncSession,
    start_date: date,
    end_date: date
) -> str:
    """Export payments to CSV string."""
    result = await db.execute(
        select(Payment, Invoice, Customer)
        .join(Invoice, Payment.invoice_id == Invoice.id)
        .join(Customer, Invoice.customer_id == Customer.id)
        .where(
            and_(
                Payment.payment_date >= start_date,
                Payment.payment_date <= end_date
            )
        ).order_by(Payment.payment_date.desc())
    )

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Payment Date",
        "Invoice Number",
        "Customer",
        "Amount",
        "Method",
        "Reference",
        "Notes",
    ])

    # Data
    for payment, invoice, customer in result.all():
        writer.writerow([
            payment.payment_date.isoformat() if payment.payment_date else "",
            invoice.invoice_number,
            customer.name,
            f"{payment.amount_cents / 100:.2f}",
            payment.method or "",
            payment.reference or "",
            payment.notes or "",
        ])

    return output.getvalue()


# =============================================================================
# UPCOMING JOBS (for dashboard)
# =============================================================================

async def get_upcoming_jobs(db: AsyncSession, limit: int = 5) -> list[dict]:
    """Get upcoming confirmed jobs for dashboard."""
    today = sydney_today()

    result = await db.execute(
        select(Quote, Customer)
        .join(Customer, Quote.customer_id == Customer.id)
        .where(
            and_(
                Quote.confirmed_start_date >= today,
                Quote.status.in_(["accepted", "confirmed", "pour_stage"])
            )
        ).order_by(Quote.confirmed_start_date)
        .limit(limit)
    )

    jobs = []
    for quote, customer in result.all():
        jobs.append({
            "quote_id": quote.id,
            "quote_number": quote.quote_number,
            "customer_name": customer.name,
            "job_name": quote.job_name,
            "scheduled_date": quote.confirmed_start_date,
            "total_cents": quote.total_cents,
        })

    return jobs


async def get_recent_activity(db: AsyncSession, limit: int = 10) -> list[dict]:
    """Get recent activity for dashboard."""
    result = await db.execute(
        select(ActivityLog)
        .order_by(ActivityLog.created_at.desc())
        .limit(limit)
    )

    activity = []
    for log in result.scalars().all():
        activity.append({
            "id": log.id,
            "action": log.action,
            "description": log.description,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "created_at": log.created_at,
        })

    return activity


# =============================================================================
# PROFITABILITY BY JOB TYPE
# =============================================================================

def _estimate_cost_from_calculator(calculator_result: Optional[dict]) -> int:
    """
    Estimate total cost from calculator_result JSON when JobCosting is unavailable.

    Sums concrete, subbase, labour cost, and other material line items.
    Returns 0 if no data.
    """
    if not calculator_result:
        return 0

    cost = 0
    cost += calculator_result.get("concrete_cost_cents", 0)
    cost += calculator_result.get("subbase_cost_cents", 0)
    cost += calculator_result.get("labour_cost_cents", 0)
    cost += calculator_result.get("excavation_cost_cents", 0)
    cost += calculator_result.get("removal_cost_cents", 0)
    cost += calculator_result.get("reo_cost_cents", 0)
    cost += calculator_result.get("setup_materials_cents", 0)
    cost += calculator_result.get("pour_materials_cents", 0)
    cost += calculator_result.get("finish_materials_cents", 0)
    cost += calculator_result.get("overhead_cents", 0)
    cost += calculator_result.get("travel_cents", 0)
    return cost


async def get_profit_by_job_type(
    db: AsyncSession,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> list[dict]:
    """
    Calculate profitability grouped by job_type.

    For each job_type, returns:
    - job_type: str
    - job_count: int
    - total_revenue_cents: int (sum of total_cents for completed quotes)
    - total_cost_cents: int (from JobCosting if available, else estimate from calculator_result)
    - total_profit_cents: int
    - avg_margin_percent: float
    - avg_quote_value_cents: int

    Only includes completed jobs. Date filter on completed_date.
    """
    from app.models import JobCosting

    # Build base filter for completed quotes
    filters = [Quote.status == "completed"]
    if start_date:
        filters.append(Quote.completed_date >= start_date)
    if end_date:
        filters.append(Quote.completed_date <= end_date)

    # Fetch completed quotes with optional JobCosting
    result = await db.execute(
        select(Quote, JobCosting)
        .outerjoin(JobCosting, JobCosting.quote_id == Quote.id)
        .where(and_(*filters))
        .order_by(Quote.completed_date)
    )

    # Group by job_type
    grouped: dict[str, list[dict]] = {}
    for quote, costing in result.all():
        jt = quote.job_type or "Other"

        if costing:
            cost = costing.actual_total_cents or 0
            profit = costing.profit_cents or 0
            margin = float(costing.margin_percent) if costing.margin_percent is not None else 0.0
        else:
            revenue = quote.total_cents or 0
            cost = _estimate_cost_from_calculator(quote.calculator_result)
            profit = revenue - cost
            margin = (profit / revenue * 100) if revenue > 0 else 0.0

        if jt not in grouped:
            grouped[jt] = []

        grouped[jt].append({
            "revenue": quote.total_cents or 0,
            "cost": cost,
            "profit": profit,
            "margin": margin,
        })

    # Build summary list
    summary = []
    for jt, jobs in grouped.items():
        job_count = len(jobs)
        total_revenue = sum(j["revenue"] for j in jobs)
        total_cost = sum(j["cost"] for j in jobs)
        total_profit = sum(j["profit"] for j in jobs)
        avg_margin = sum(j["margin"] for j in jobs) / job_count if job_count > 0 else 0.0
        avg_quote_value = total_revenue // job_count if job_count > 0 else 0

        summary.append({
            "job_type": jt,
            "job_count": job_count,
            "total_revenue_cents": total_revenue,
            "total_cost_cents": total_cost,
            "total_profit_cents": total_profit,
            "avg_margin_percent": round(avg_margin, 1),
            "avg_quote_value_cents": avg_quote_value,
        })

    # Sort by total revenue descending
    summary.sort(key=lambda x: x["total_revenue_cents"], reverse=True)
    return summary


async def get_job_type_breakdown(
    db: AsyncSession,
    job_type: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> list[dict]:
    """
    Get individual job details for a specific job_type.

    Returns list of completed jobs with:
    - quote_id, quote_number, job_name, customer_name
    - total_cents, cost_cents, profit_cents, margin_percent
    - completed_date
    """
    from app.models import JobCosting

    # Normalise "Other" back to None for DB filter
    filters = [Quote.status == "completed"]
    if job_type == "Other":
        filters.append(or_(Quote.job_type.is_(None), Quote.job_type == ""))
    else:
        filters.append(Quote.job_type == job_type)

    if start_date:
        filters.append(Quote.completed_date >= start_date)
    if end_date:
        filters.append(Quote.completed_date <= end_date)

    result = await db.execute(
        select(Quote, Customer, JobCosting)
        .join(Customer, Quote.customer_id == Customer.id)
        .outerjoin(JobCosting, JobCosting.quote_id == Quote.id)
        .where(and_(*filters))
        .order_by(Quote.completed_date.desc())
    )

    jobs = []
    for quote, customer, costing in result.all():
        revenue = quote.total_cents or 0
        if costing:
            cost = costing.actual_total_cents or 0
            profit = costing.profit_cents or 0
            margin = float(costing.margin_percent) if costing.margin_percent is not None else 0.0
        else:
            cost = _estimate_cost_from_calculator(quote.calculator_result)
            profit = revenue - cost
            margin = (profit / revenue * 100) if revenue > 0 else 0.0

        jobs.append({
            "quote_id": quote.id,
            "quote_number": quote.quote_number,
            "job_name": quote.job_name or quote.job_address or "Untitled",
            "customer_name": customer.name,
            "total_cents": revenue,
            "cost_cents": cost,
            "profit_cents": profit,
            "margin_percent": round(margin, 1),
            "completed_date": quote.completed_date,
        })

    return jobs
