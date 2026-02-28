"""
Reports routes — Business analytics and reporting pages.

All reports require admin authentication.
"""

from datetime import date
from typing import Optional
from urllib.parse import quote as url_quote
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
import io

from app.database import get_db
from app.core.auth import require_login, verify_csrf
from app.core.templates import templates
from app.core.dates import sydney_today
from app.reports.service import (
    get_period_dates,
    get_dashboard_stats,
    get_quote_stats,
    get_revenue_stats,
    get_job_stats,
    get_customer_stats,
    get_profit_by_job_type,
    get_job_type_breakdown,
    export_quotes_csv,
    export_invoices_csv,
    export_payments_csv,
)
from app.reports.take_home import get_goal_progress, get_annual_projection

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


# =============================================================================
# HTML PAGES
# =============================================================================

@router.get("", name="reports:index")
async def reports_index(request: Request):
    """Reports dashboard - links to all reports."""
    return templates.TemplateResponse("reports/index.html", {
        "request": request,
    })


@router.get("/activity", name="reports:activity")
async def activity_log(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    days: int = Query(30, ge=1, le=365),
):
    """
    Activity log page.

    Shows recent system activity including quotes, invoices, and payments.
    """
    from datetime import timedelta
    from app.models import Quote, Invoice, Payment
    from app.core.dates import sydney_now
    from sqlalchemy import select

    page_size = 50
    offset = (page - 1) * page_size
    since = sydney_now() - timedelta(days=days)

    # Build activity list from recent quotes, invoices, payments
    activities = []

    # Get recent quotes
    quotes_result = await db.execute(
        select(Quote)
        .where(Quote.created_at >= since)
        .order_by(Quote.created_at.desc())
        .limit(20)
    )
    for quote in quotes_result.scalars():
        activities.append({
            "type": "quote",
            "icon": "document",
            "description": f"Quote {quote.quote_number} created",
            "details": f"{quote.job_name or quote.job_address or 'New quote'}",
            "created_at": quote.created_at,
        })
        if quote.sent_at:
            activities.append({
                "type": "quote_sent",
                "icon": "mail",
                "description": f"Quote {quote.quote_number} sent",
                "details": f"Sent to customer",
                "created_at": quote.sent_at,
            })

    # Get recent invoices
    invoices_result = await db.execute(
        select(Invoice)
        .where(Invoice.created_at >= since)
        .order_by(Invoice.created_at.desc())
        .limit(20)
    )
    for invoice in invoices_result.scalars():
        activities.append({
            "type": "invoice",
            "icon": "cash",
            "description": f"Invoice {invoice.invoice_number} created",
            "details": f"${invoice.total_cents / 100:,.2f}",
            "created_at": invoice.created_at,
        })

    # Get recent payments
    payments_result = await db.execute(
        select(Payment)
        .where(Payment.created_at >= since)
        .order_by(Payment.created_at.desc())
        .limit(20)
    )
    for payment in payments_result.scalars():
        activities.append({
            "type": "payment",
            "icon": "check",
            "description": f"Payment received",
            "details": f"${payment.amount_cents / 100:,.2f}",
            "created_at": payment.created_at,
        })

    # Sort by created_at descending
    activities.sort(key=lambda x: x["created_at"], reverse=True)

    # Paginate
    total = len(activities)
    activities = activities[offset:offset + page_size]

    return templates.TemplateResponse("reports/activity.html", {
        "request": request,
        "activities": activities,
        "page": page,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
        "days": days,
    })


@router.get("/quotes", name="reports:quotes")
async def reports_quotes_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: str = Query("month"),
):
    """Quote conversion report page."""
    # Parse dates or use period
    if start and end:
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError:
            start_date, end_date = get_period_dates(period)
    else:
        start_date, end_date = get_period_dates(period)

    stats = await get_quote_stats(db, start_date, end_date)

    return templates.TemplateResponse("reports/quotes.html", {
        "request": request,
        "stats": stats,
        "start_date": start_date,
        "end_date": end_date,
        "period": period,
    })


@router.get("/revenue", name="reports:revenue")
async def reports_revenue_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: str = Query("month"),
):
    """Revenue report page."""
    if start and end:
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError:
            start_date, end_date = get_period_dates(period)
    else:
        start_date, end_date = get_period_dates(period)

    stats = await get_revenue_stats(db, start_date, end_date)

    return templates.TemplateResponse("reports/revenue.html", {
        "request": request,
        "stats": stats,
        "start_date": start_date,
        "end_date": end_date,
        "period": period,
    })


@router.get("/jobs", name="reports:jobs")
async def reports_jobs_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: str = Query("month"),
):
    """Jobs report page."""
    if start and end:
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError:
            start_date, end_date = get_period_dates(period)
    else:
        start_date, end_date = get_period_dates(period)

    stats = await get_job_stats(db, start_date, end_date)

    return templates.TemplateResponse("reports/jobs.html", {
        "request": request,
        "stats": stats,
        "start_date": start_date,
        "end_date": end_date,
        "period": period,
    })


@router.get("/customers", name="reports:customers")
async def reports_customers_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: str = Query("month"),
):
    """Customer report page."""
    if start and end:
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError:
            start_date, end_date = get_period_dates(period)
    else:
        start_date, end_date = get_period_dates(period)

    stats = await get_customer_stats(db, start_date, end_date)

    return templates.TemplateResponse("reports/customers.html", {
        "request": request,
        "stats": stats,
        "start_date": start_date,
        "end_date": end_date,
        "period": period,
    })


@router.get("/earnings", name="reports:earnings")
async def earnings_report(
    request: Request,
    db: AsyncSession = Depends(get_db),
    year: Optional[int] = None,
):
    """Owner earnings report - yearly overview."""
    from sqlalchemy import select, extract, distinct, func
    from app.models import EarningsSnapshot

    # Default to current year
    today = sydney_today()
    if year is None:
        year = today.year

    current_month = today.month if year == today.year else 12

    # Get monthly snapshots for the year
    result = await db.execute(
        select(EarningsSnapshot)
        .where(EarningsSnapshot.period_type == "monthly")
        .where(extract('year', EarningsSnapshot.period_start) == year)
        .order_by(EarningsSnapshot.period_start)
    )
    monthly_snapshots = result.scalars().all()

    # Build monthly data (fill in missing months with 0)
    months_data = []
    for month in range(1, 13):
        snapshot = next(
            (s for s in monthly_snapshots if s.period_start.month == month),
            None
        )
        months_data.append({
            "month": month,
            "month_name": date(year, month, 1).strftime("%b"),
            "take_home_cents": snapshot.take_home_cents if snapshot else 0,
            "revenue_cents": snapshot.revenue_cents if snapshot else 0,
            "materials_cents": snapshot.materials_cents if snapshot else 0,
            "labour_cents": snapshot.labour_cents if snapshot else 0,
            "expenses_cents": snapshot.expenses_cents if snapshot else 0,
            "jobs": snapshot.jobs_completed if snapshot else 0,
            "has_data": snapshot is not None,
        })

    # Calculate yearly totals
    yearly_total = sum(m["take_home_cents"] for m in months_data)
    yearly_revenue = sum(m["revenue_cents"] for m in months_data)
    yearly_jobs = sum(m["jobs"] for m in months_data)

    # Find best/worst months (only months with data)
    months_with_data = [m for m in months_data if m["has_data"]]
    best_month = max(months_with_data, key=lambda m: m["take_home_cents"]) if months_with_data else None
    worst_month = min(months_with_data, key=lambda m: m["take_home_cents"]) if months_with_data else None

    # Monthly average
    avg_monthly = yearly_total // len(months_with_data) if months_with_data else 0

    # Get available years for dropdown
    years_result = await db.execute(
        select(distinct(extract('year', EarningsSnapshot.period_start)))
        .where(EarningsSnapshot.period_type == "monthly")
        .order_by(extract('year', EarningsSnapshot.period_start).desc())
    )
    available_years = [int(y[0]) for y in years_result.fetchall() if y[0] is not None]
    if year not in available_years:
        available_years.insert(0, year)
    available_years = sorted(available_years, reverse=True)

    # Get current goal progress for display
    goal = await get_goal_progress(db)

    # Get annual projection (only for current year)
    projection = None
    if year == today.year:
        projection = await get_annual_projection(db)

    # Previous year comparison (if data exists)
    prev_year = year - 1
    prev_year_result = await db.execute(
        select(func.sum(EarningsSnapshot.take_home_cents))
        .where(EarningsSnapshot.period_type == "monthly")
        .where(extract('year', EarningsSnapshot.period_start) == prev_year)
    )
    prev_year_total = prev_year_result.scalar() or 0

    # Calculate YoY change
    if prev_year_total > 0:
        yoy_change = ((yearly_total - prev_year_total) / prev_year_total) * 100
    else:
        yoy_change = None

    return templates.TemplateResponse("reports/earnings.html", {
        "request": request,
        "year": year,
        "current_year": today.year,
        "current_month": current_month,
        "available_years": available_years,
        "months_data": months_data,
        "yearly_total_cents": yearly_total,
        "yearly_revenue_cents": yearly_revenue,
        "yearly_jobs": yearly_jobs,
        "avg_monthly_cents": avg_monthly,
        "best_month": best_month,
        "worst_month": worst_month,
        "goal": goal,
        "projection": projection,
        "prev_year_total_cents": prev_year_total,
        "yoy_change": yoy_change,
    })


@router.get("/profitability", name="reports:profitability")
async def reports_profitability_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: str = Query("year"),
):
    """Profitability by job type report page."""
    if start and end:
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError:
            start_date, end_date = get_period_dates(period)
    else:
        start_date, end_date = get_period_dates(period)

    # Get profitability grouped by job type
    job_types = await get_profit_by_job_type(db, start_date, end_date)

    # Get detailed breakdowns for each job type (for expand/collapse)
    breakdowns = {}
    for jt in job_types:
        breakdowns[jt["job_type"]] = await get_job_type_breakdown(
            db, jt["job_type"], start_date, end_date
        )

    # Calculate totals for summary cards
    total_revenue = sum(jt["total_revenue_cents"] for jt in job_types)
    total_profit = sum(jt["total_profit_cents"] for jt in job_types)
    total_jobs = sum(jt["job_count"] for jt in job_types)
    avg_margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0.0

    # Find max revenue for bar chart scaling
    max_revenue = max((jt["total_revenue_cents"] for jt in job_types), default=1)
    if max_revenue == 0:
        max_revenue = 1

    return templates.TemplateResponse("reports/profitability.html", {
        "request": request,
        "job_types": job_types,
        "breakdowns": breakdowns,
        "total_revenue_cents": total_revenue,
        "total_profit_cents": total_profit,
        "total_jobs": total_jobs,
        "avg_margin": round(avg_margin, 1),
        "max_revenue": max_revenue,
        "start_date": start_date,
        "end_date": end_date,
        "period": period,
    })


# =============================================================================
# BAS (Business Activity Statement) DASHBOARD
# =============================================================================

@router.get("/bas", name="reports:bas")
async def reports_bas_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    fy: Optional[int] = None,
    quarter: Optional[int] = None,
):
    """
    BAS dashboard — GST collected vs paid, net position, BAS form fields.

    Shows quarterly GST summary for lodgement with the ATO.
    """
    from app.core.bas import (
        get_bas_quarter, get_bas_summary, get_quarter_label,
        get_quarter_dates, get_current_fy, get_fy_quarters,
        get_bas_due_status, get_missing_receipts, get_expense_breakdown,
        GST_CLAIMABLE_ITEMS,
    )
    from app.integrations.xero import get_sync_status, get_xero_connection_status

    # Default to current quarter
    if fy is None or quarter is None:
        fy, quarter = get_bas_quarter()

    # Get BAS summary for selected quarter
    summary = await get_bas_summary(db, fy, quarter)
    start, end = get_quarter_dates(fy, quarter)

    # Due date status
    due_status = get_bas_due_status(fy, quarter)

    # Missing receipts for this quarter
    receipts = await get_missing_receipts(db, start, end)

    # Expense breakdown by category
    expense_breakdown = await get_expense_breakdown(db, start, end)

    # Get all quarters for the FY (for navigation)
    all_quarters = []
    for q_fy, q_num in get_fy_quarters(fy):
        q_start, q_end = get_quarter_dates(q_fy, q_num)
        all_quarters.append({
            "fy": q_fy,
            "quarter": q_num,
            "label": get_quarter_label(q_fy, q_num),
            "start": q_start,
            "end": q_end,
            "is_current": (q_fy == fy and q_num == quarter),
        })

    # Get Xero sync status for warnings
    sync_status = await get_sync_status(db)
    xero_status = await get_xero_connection_status(db)

    # Pre-lodgement checklist
    checklist = []
    unsynced_invoices = sync_status["invoices"]["unsynced"]
    unsynced_payments = sync_status["payments"]["unsynced"]

    checklist.append({
        "label": "All invoices synced to Xero",
        "ok": unsynced_invoices == 0,
        "detail": f"{unsynced_invoices} unsynced" if unsynced_invoices > 0 else "All synced",
    })
    checklist.append({
        "label": "All payments synced to Xero",
        "ok": unsynced_payments == 0,
        "detail": f"{unsynced_payments} unsynced" if unsynced_payments > 0 else "All synced",
    })
    checklist.append({
        "label": "Xero connected and active",
        "ok": xero_status.get("connected", False) and not xero_status.get("is_expired", True),
        "detail": "Connected" if xero_status.get("connected") else "Not connected",
    })
    checklist.append({
        "label": "All receipts attached",
        "ok": receipts["all_good"],
        "detail": f"{receipts['missing_count']} missing receipts" if not receipts["all_good"] else "All attached",
    })
    checklist.append({
        "label": "All expenses entered for the quarter",
        "ok": None,
        "detail": "Manual check required",
    })

    # Available financial years for dropdown (current ± 1)
    current_fy = get_current_fy()
    available_fys = [current_fy - 1, current_fy, current_fy + 1]

    return templates.TemplateResponse("reports/bas.html", {
        "request": request,
        "summary": summary,
        "all_quarters": all_quarters,
        "fy": fy,
        "quarter": quarter,
        "checklist": checklist,
        "sync_status": sync_status,
        "xero_connected": xero_status.get("connected", False),
        "available_fys": available_fys,
        "due_status": due_status,
        "receipts": receipts,
        "expense_breakdown": expense_breakdown,
        "gst_reference": GST_CLAIMABLE_ITEMS,
    })


@router.get("/bas/export-csv", name="reports:bas_export_csv")
async def bas_export_csv(
    db: AsyncSession = Depends(get_db),
    fy: Optional[int] = None,
    quarter: Optional[int] = None,
):
    """
    Export BAS summary as CSV for accountant/BAS agent.
    """
    from app.core.bas import get_bas_quarter, get_bas_summary, get_quarter_label

    if fy is None or quarter is None:
        fy, quarter = get_bas_quarter()

    summary = await get_bas_summary(db, fy, quarter)

    import csv
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["BAS Summary Export"])
    writer.writerow(["Period", summary["label"]])
    writer.writerow(["Start Date", summary["period_start"]])
    writer.writerow(["End Date", summary["period_end"]])
    writer.writerow([])

    writer.writerow(["GST ON SALES (Collected)"])
    writer.writerow(["Total Sales (inc GST)", f"${summary['sales']['total_cents']/100:,.2f}"])
    writer.writerow(["Sales ex GST", f"${summary['sales']['subtotal_cents']/100:,.2f}"])
    writer.writerow(["GST Collected", f"${summary['sales']['gst_cents']/100:,.2f}"])
    writer.writerow([])

    writer.writerow(["GST ON PURCHASES (Paid)"])
    writer.writerow(["Total Purchases (inc GST)", f"${summary['purchases']['total_cents']/100:,.2f}"])
    writer.writerow(["Purchases ex GST", f"${summary['purchases']['amount_cents']/100:,.2f}"])
    writer.writerow(["GST Paid", f"${summary['purchases']['gst_cents']/100:,.2f}"])
    writer.writerow([])

    writer.writerow(["NET GST POSITION"])
    net = summary["net_gst_cents"]
    writer.writerow(["Net GST", f"${net/100:,.2f}", "You owe ATO" if net > 0 else "ATO owes you"])
    writer.writerow([])

    writer.writerow(["BAS FORM FIELDS"])
    for field, value in summary["bas_fields"].items():
        writer.writerow([field, f"${value/100:,.2f}"])

    csv_data = output.getvalue()
    filename = f"BAS_Q{quarter}_FY{fy}.csv"

    return StreamingResponse(
        io.BytesIO(csv_data.encode('utf-8')),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{url_quote(filename)}"
        }
    )


# =============================================================================
# API ENDPOINTS
# =============================================================================

@router.get("/api/dashboard")
async def api_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    period: str = Query("month"),
) -> dict:
    """API: Get dashboard statistics."""
    return await get_dashboard_stats(db, period)


@router.get("/api/quotes")
async def api_quote_stats(
    db: AsyncSession = Depends(get_db),
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: str = Query("month"),
) -> dict:
    """API: Get quote statistics."""
    if start and end:
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError:
            start_date, end_date = get_period_dates(period)
    else:
        start_date, end_date = get_period_dates(period)

    return await get_quote_stats(db, start_date, end_date)


@router.get("/api/revenue")
async def api_revenue_stats(
    db: AsyncSession = Depends(get_db),
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: str = Query("month"),
) -> dict:
    """API: Get revenue statistics."""
    if start and end:
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError:
            start_date, end_date = get_period_dates(period)
    else:
        start_date, end_date = get_period_dates(period)

    return await get_revenue_stats(db, start_date, end_date)


@router.get("/api/jobs")
async def api_job_stats(
    db: AsyncSession = Depends(get_db),
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: str = Query("month"),
) -> dict:
    """API: Get job statistics."""
    if start and end:
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError:
            start_date, end_date = get_period_dates(period)
    else:
        start_date, end_date = get_period_dates(period)

    return await get_job_stats(db, start_date, end_date)


@router.get("/api/customers")
async def api_customer_stats(
    db: AsyncSession = Depends(get_db),
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: str = Query("month"),
) -> dict:
    """API: Get customer statistics."""
    if start and end:
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError:
            start_date, end_date = get_period_dates(period)
    else:
        start_date, end_date = get_period_dates(period)

    return await get_customer_stats(db, start_date, end_date)


# =============================================================================
# CSV EXPORTS
# =============================================================================

@router.get("/api/export/{report_type}")
async def api_export_csv(
    report_type: str,
    db: AsyncSession = Depends(get_db),
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: str = Query("month"),
):
    """
    API: Export report data as CSV.

    report_type: quotes, invoices, payments
    """
    # Parse dates
    if start and end:
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError:
            start_date, end_date = get_period_dates(period)
    else:
        start_date, end_date = get_period_dates(period)

    # Generate CSV
    if report_type == "quotes":
        csv_data = await export_quotes_csv(db, start_date, end_date)
        filename = f"quotes_{start_date}_{end_date}.csv"
    elif report_type == "invoices":
        csv_data = await export_invoices_csv(db, start_date, end_date)
        filename = f"invoices_{start_date}_{end_date}.csv"
    elif report_type == "payments":
        csv_data = await export_payments_csv(db, start_date, end_date)
        filename = f"payments_{start_date}_{end_date}.csv"
    else:
        raise HTTPException(400, f"Unknown report type: {report_type}")

    # Return as streaming response
    return StreamingResponse(
        io.BytesIO(csv_data.encode('utf-8')),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{url_quote(filename)}"
        }
    )


# =============================================================================
# ATO COMPLIANCE CALENDAR
# =============================================================================

@router.get("/compliance", name="reports:compliance")
async def compliance_calendar(
    request: Request,
    fy: Optional[int] = None,
):
    """
    ATO Compliance Calendar — all key deadlines for the financial year.

    Shows BAS, Super, TPAR, Tax Return, Insurance deadlines with status.
    """
    from app.core.bas import (
        get_current_fy, get_compliance_deadlines, get_compliance_status,
    )

    if fy is None:
        fy = get_current_fy()

    today = sydney_today()
    deadlines = get_compliance_deadlines(fy)
    deadlines = get_compliance_status(deadlines, today)

    # Group by category for summary stats
    overdue_count = sum(1 for d in deadlines if d["status"] == "overdue")
    due_soon_count = sum(1 for d in deadlines if d["status"] == "due_soon")
    upcoming_count = sum(1 for d in deadlines if d["status"] == "upcoming")

    # Group by quarter for visual layout
    from collections import defaultdict
    by_month = defaultdict(list)
    for d in deadlines:
        month_key = d["due_date"].strftime("%B %Y")
        by_month[month_key].append(d)

    return templates.TemplateResponse("reports/compliance.html", {
        "request": request,
        "fy": fy,
        "deadlines": deadlines,
        "by_month": dict(by_month),
        "overdue_count": overdue_count,
        "due_soon_count": due_soon_count,
        "upcoming_count": upcoming_count,
        "today": today,
    })


# =============================================================================
# TPAR (Taxable Payments Annual Report)
# =============================================================================

@router.get("/tpar", name="reports:tpar")
async def tpar_report(
    request: Request,
    db: AsyncSession = Depends(get_db),
    fy: Optional[int] = None,
):
    """
    TPAR Report — Taxable Payments Annual Report.

    Building & construction industry must report payments to subcontractors.
    Due 28 August following end of financial year.
    """
    from app.core.bas import get_current_fy, get_quarter_dates
    from sqlalchemy import select, func
    from app.models import Expense

    if fy is None:
        fy = get_current_fy()

    # FY date range
    fy_start = date(fy - 1, 7, 1)
    fy_end = date(fy, 6, 30)

    # Get all subcontractor expenses in the FY
    result = await db.execute(
        select(Expense)
        .where(Expense.category == "subcontractor")
        .where(Expense.expense_date >= fy_start)
        .where(Expense.expense_date <= fy_end)
        .order_by(Expense.vendor, Expense.expense_date)
    )
    subbie_expenses = result.scalars().all()

    # Group by vendor (payee)
    from collections import defaultdict
    by_vendor = defaultdict(lambda: {"expenses": [], "total_cents": 0, "gst_cents": 0})
    for exp in subbie_expenses:
        vendor_name = exp.vendor or "Unknown Subcontractor"
        by_vendor[vendor_name]["expenses"].append(exp)
        by_vendor[vendor_name]["total_cents"] += exp.amount_cents + exp.gst_cents
        by_vendor[vendor_name]["gst_cents"] += exp.gst_cents

    # Sort by total paid descending
    vendor_list = []
    for name, data in sorted(by_vendor.items(), key=lambda x: x[1]["total_cents"], reverse=True):
        vendor_list.append({
            "name": name,
            "payment_count": len(data["expenses"]),
            "total_cents": data["total_cents"],
            "gst_cents": data["gst_cents"],
            "expenses": data["expenses"],
        })

    grand_total_cents = sum(v["total_cents"] for v in vendor_list)
    grand_gst_cents = sum(v["gst_cents"] for v in vendor_list)

    # Due date is 28 August after end of FY
    due_date = date(fy, 8, 28)
    days_remaining = (due_date - sydney_today()).days

    return templates.TemplateResponse("reports/tpar.html", {
        "request": request,
        "fy": fy,
        "fy_start": fy_start,
        "fy_end": fy_end,
        "vendors": vendor_list,
        "total_vendors": len(vendor_list),
        "grand_total_cents": grand_total_cents,
        "grand_gst_cents": grand_gst_cents,
        "due_date": due_date,
        "days_remaining": days_remaining,
    })


@router.get("/pay-split", name="reports:pay_split")
async def pay_split_calculator(request: Request):
    """
    Pay Distribution Calculator — weekly income split for proprietor.

    Pure client-side Alpine.js calculator (no DB queries needed).
    Implements the KRG income distribution formula:
    - Base threshold: $1,543/week
    - 7 standard accounts: Daily, PAYG Tax, Vehicle, Health, Fun Card, Savings, Subscriptions
    - Excess above threshold split: 50% Trustee, 22% Extra Tax, 28% Keep
    """
    return templates.TemplateResponse("reports/pay_split.html", {
        "request": request,
    })


@router.get("/tpar/export-csv", name="reports:tpar_export_csv")
async def tpar_export_csv(
    db: AsyncSession = Depends(get_db),
    fy: Optional[int] = None,
):
    """
    Export TPAR data as CSV.

    Columns: Payee Name, ABN, Total Paid (inc GST), GST, Payment Count
    """
    from app.core.bas import get_current_fy
    from sqlalchemy import select, func
    from app.models import Expense

    if fy is None:
        fy = get_current_fy()

    fy_start = date(fy - 1, 7, 1)
    fy_end = date(fy, 6, 30)

    # Get subcontractor expenses grouped by vendor
    result = await db.execute(
        select(
            Expense.vendor,
            func.count(Expense.id),
            func.coalesce(func.sum(Expense.amount_cents), 0),
            func.coalesce(func.sum(Expense.gst_cents), 0),
        )
        .where(Expense.category == "subcontractor")
        .where(Expense.expense_date >= fy_start)
        .where(Expense.expense_date <= fy_end)
        .group_by(Expense.vendor)
        .order_by(func.sum(Expense.amount_cents).desc())
    )

    import csv
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["TPAR - Taxable Payments Annual Report"])
    writer.writerow([f"Financial Year: FY{fy} ({fy_start} to {fy_end})"])
    writer.writerow([f"Payer: KRG Concreting"])
    writer.writerow([])
    writer.writerow(["Payee Name", "ABN", "Total Paid (inc GST)", "GST Included", "Payment Count"])

    for row in result.all():
        vendor = row[0] or "Unknown"
        count = row[1]
        amount = row[2]
        gst = row[3]
        total = amount + gst
        writer.writerow([
            vendor,
            "",  # ABN — needs to be filled manually or from Supplier table
            f"${total/100:,.2f}",
            f"${gst/100:,.2f}",
            count,
        ])

    csv_data = output.getvalue()
    filename = f"TPAR_FY{fy}.csv"

    return StreamingResponse(
        io.BytesIO(csv_data.encode('utf-8')),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{url_quote(filename)}"
        }
    )
