"""
BAS (Business Activity Statement) quarter utilities.

Australian financial year quarters:
  Q1: July - September
  Q2: October - December
  Q3: January - March
  Q4: April - June

Financial year runs 1 July → 30 June (e.g., FY2026 = 1 Jul 2025 → 30 Jun 2026).

BAS lodgement deadlines:
  Q1 (Jul-Sep): 28 October
  Q2 (Oct-Dec): 28 February
  Q3 (Jan-Mar): 28 April
  Q4 (Apr-Jun): 28 July  (next FY)
"""

from datetime import date, timedelta
from typing import Optional, List

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dates import sydney_today
from app.models import Invoice, Expense


# =============================================================================
# QUARTER CALCULATION
# =============================================================================

def get_bas_quarter(d: Optional[date] = None) -> tuple[int, int]:
    """
    Get BAS quarter for a given date.

    Returns:
        (financial_year, quarter_number) e.g. (2026, 1) for Jul-Sep 2025

    The financial year is named by its END year:
      FY2026 = 1 Jul 2025 → 30 Jun 2026
    """
    if d is None:
        d = sydney_today()

    month = d.month

    if month >= 7 and month <= 9:
        # Q1: Jul-Sep → FY = year + 1
        return d.year + 1, 1
    elif month >= 10 and month <= 12:
        # Q2: Oct-Dec → FY = year + 1
        return d.year + 1, 2
    elif month >= 1 and month <= 3:
        # Q3: Jan-Mar → FY = year
        return d.year, 3
    else:
        # Q4: Apr-Jun → FY = year
        return d.year, 4


def get_quarter_dates(fy: int, quarter: int) -> tuple[date, date]:
    """
    Get start and end dates for a BAS quarter.

    Args:
        fy: Financial year (end year, e.g. 2026 for FY2026)
        quarter: Quarter number (1-4)

    Returns:
        (start_date, end_date) inclusive
    """
    if quarter == 1:
        # Q1: Jul 1 - Sep 30 of FY-1
        start = date(fy - 1, 7, 1)
        end = date(fy - 1, 9, 30)
    elif quarter == 2:
        # Q2: Oct 1 - Dec 31 of FY-1
        start = date(fy - 1, 10, 1)
        end = date(fy - 1, 12, 31)
    elif quarter == 3:
        # Q3: Jan 1 - Mar 31 of FY
        start = date(fy, 1, 1)
        end = date(fy, 3, 31)
    elif quarter == 4:
        # Q4: Apr 1 - Jun 30 of FY
        start = date(fy, 4, 1)
        end = date(fy, 6, 30)
    else:
        raise ValueError(f"Invalid quarter: {quarter}. Must be 1-4.")

    return start, end


def get_quarter_label(fy: int, quarter: int) -> str:
    """
    Human-readable quarter label.

    Returns e.g. "Q1 FY2026 (Jul-Sep 2025)"
    """
    months = {1: "Jul-Sep", 2: "Oct-Dec", 3: "Jan-Mar", 4: "Apr-Jun"}
    start, _ = get_quarter_dates(fy, quarter)
    return f"Q{quarter} FY{fy} ({months[quarter]} {start.year})"


def get_fy_quarters(fy: int) -> list[tuple[int, int]]:
    """
    Get all 4 quarters for a financial year.

    Returns list of (fy, quarter) tuples.
    """
    return [(fy, q) for q in range(1, 5)]


def get_current_fy() -> int:
    """Get the current financial year number."""
    fy, _ = get_bas_quarter()
    return fy


# =============================================================================
# GST CALCULATIONS
# =============================================================================

async def get_gst_collected(db: AsyncSession, start: date, end: date) -> dict:
    """
    Get GST collected on sales (from invoices) for a period.

    Only counts PAID invoices in the period.
    Returns dict with subtotal_cents, gst_cents, total_cents.
    """
    result = await db.execute(
        select(
            func.coalesce(func.sum(Invoice.subtotal_cents), 0),
            func.coalesce(func.sum(Invoice.gst_cents), 0),
            func.coalesce(func.sum(Invoice.total_cents), 0),
        ).where(
            Invoice.status == "paid",
            Invoice.paid_date.isnot(None),
            Invoice.paid_date >= start,
            Invoice.paid_date <= end,
        )
    )
    row = result.one()
    return {
        "subtotal_cents": row[0],
        "gst_cents": row[1],
        "total_cents": row[2],
    }


async def get_gst_paid(db: AsyncSession, start: date, end: date) -> dict:
    """
    Get GST paid on purchases (from expenses) for a period.

    Returns dict with amount_cents (ex GST), gst_cents, total_cents.
    """
    result = await db.execute(
        select(
            func.coalesce(func.sum(Expense.amount_cents), 0),
            func.coalesce(func.sum(Expense.gst_cents), 0),
        ).where(
            Expense.expense_date >= start,
            Expense.expense_date <= end,
        )
    )
    row = result.one()
    amount = row[0]
    gst = row[1]
    return {
        "amount_cents": amount,
        "gst_cents": gst,
        "total_cents": amount + gst,
    }


async def get_bas_summary(db: AsyncSession, fy: int, quarter: int) -> dict:
    """
    Get full BAS summary for a quarter.

    Returns:
        Dict with GST collected, GST paid, net position, and BAS form field values.
    """
    start, end = get_quarter_dates(fy, quarter)

    collected = await get_gst_collected(db, start, end)
    paid = await get_gst_paid(db, start, end)

    # Net GST position (positive = you owe ATO, negative = ATO owes you)
    net_gst_cents = collected["gst_cents"] - paid["gst_cents"]

    return {
        "quarter": quarter,
        "financial_year": fy,
        "label": get_quarter_label(fy, quarter),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),

        # GST on sales (collected from customers)
        "sales": collected,

        # GST on purchases (paid to suppliers)
        "purchases": paid,

        # Net position
        "net_gst_cents": net_gst_cents,

        # BAS form field mapping (Australian BAS form)
        "bas_fields": {
            "G1": collected["total_cents"],    # Total sales (inc GST)
            "G2": 0,                            # Export sales (not applicable)
            "G3": 0,                            # GST-free sales
            "G10": 0,                           # Capital purchases — not tracked separately yet
            "G11": paid["total_cents"],          # Non-capital purchases (inc GST)
            "1A": collected["gst_cents"],        # GST on sales
            "1B": paid["gst_cents"],             # GST on purchases
        },
    }


# =============================================================================
# BAS DUE DATES
# =============================================================================

def get_bas_due_date(fy: int, quarter: int) -> date:
    """
    Get the ATO lodgement deadline for a BAS quarter.

    Standard deadlines (paper/online):
      Q1 (Jul-Sep): 28 October  (same year as quarter)
      Q2 (Oct-Dec): 28 February (following year)
      Q3 (Jan-Mar): 28 April    (same year as quarter)
      Q4 (Apr-Jun): 28 July     (same year as quarter end)

    Note: If using a registered tax/BAS agent, you may get extended deadlines.
    """
    if quarter == 1:
        return date(fy - 1, 10, 28)
    elif quarter == 2:
        return date(fy, 2, 28)
    elif quarter == 3:
        return date(fy, 4, 28)
    elif quarter == 4:
        return date(fy, 7, 28)
    else:
        raise ValueError(f"Invalid quarter: {quarter}")


def get_bas_due_status(fy: int, quarter: int) -> dict:
    """
    Get the due date and status (overdue, due soon, upcoming, or past).

    Returns dict with due_date, days_remaining, status, status_label.
    """
    due = get_bas_due_date(fy, quarter)
    today = sydney_today()
    days_remaining = (due - today).days

    if days_remaining < 0:
        status = "overdue"
        label = f"Overdue by {abs(days_remaining)} days"
    elif days_remaining <= 14:
        status = "due_soon"
        label = f"Due in {days_remaining} days"
    elif days_remaining <= 60:
        status = "upcoming"
        label = f"Due in {days_remaining} days"
    else:
        status = "future"
        label = f"Due {due.strftime('%d %b %Y')}"

    return {
        "due_date": due,
        "days_remaining": days_remaining,
        "status": status,
        "label": label,
    }


# =============================================================================
# MISSING RECEIPTS
# =============================================================================

async def get_missing_receipts(db: AsyncSession, start: date, end: date) -> dict:
    """
    Find expenses in a period that are missing receipt documentation.

    An expense is missing a receipt if it has no receipt_url AND no receipt_photo_id.
    """
    # Count total expenses in period
    total_result = await db.execute(
        select(func.count(Expense.id)).where(
            Expense.expense_date >= start,
            Expense.expense_date <= end,
        )
    )
    total_count = total_result.scalar() or 0

    # Count expenses missing receipts
    missing_result = await db.execute(
        select(func.count(Expense.id)).where(
            Expense.expense_date >= start,
            Expense.expense_date <= end,
            (Expense.receipt_url.is_(None) | (Expense.receipt_url == "")),
            Expense.receipt_photo_id.is_(None),
        )
    )
    missing_count = missing_result.scalar() or 0

    # Get the actual missing expense records (for display, limit 20)
    missing_expenses = []
    if missing_count > 0:
        result = await db.execute(
            select(Expense).where(
                Expense.expense_date >= start,
                Expense.expense_date <= end,
                (Expense.receipt_url.is_(None) | (Expense.receipt_url == "")),
                Expense.receipt_photo_id.is_(None),
            ).order_by(Expense.expense_date.desc()).limit(20)
        )
        missing_expenses = result.scalars().all()

    return {
        "total_count": total_count,
        "missing_count": missing_count,
        "has_receipts_count": total_count - missing_count,
        "all_good": missing_count == 0,
        "missing_expenses": missing_expenses,
    }


# =============================================================================
# EXPENSE BREAKDOWN BY CATEGORY
# =============================================================================

async def get_expense_breakdown(db: AsyncSession, start: date, end: date) -> list[dict]:
    """
    Get expense totals grouped by category for a period.

    Returns list of dicts with category, count, amount_cents, gst_cents, total_cents.
    Sorted by total descending.
    """
    result = await db.execute(
        select(
            Expense.category,
            func.count(Expense.id),
            func.coalesce(func.sum(Expense.amount_cents), 0),
            func.coalesce(func.sum(Expense.gst_cents), 0),
        ).where(
            Expense.expense_date >= start,
            Expense.expense_date <= end,
        ).group_by(Expense.category).order_by(func.sum(Expense.amount_cents).desc())
    )

    # Category display names for expense breakdown
    names = {
        "concrete_supply": "Concrete Supply",
        "steel_mesh": "Steel / Mesh / Rebar",
        "formwork": "Formwork & Boxing",
        "materials_other": "Other Materials",
        "pump_hire": "Pump Hire",
        "equipment_hire": "Equipment Hire",
        "tool_purchase": "Tool Purchases",
        "subcontractor": "Subcontractor",
        "fuel": "Fuel",
        "vehicle": "Vehicle Running Costs",
        "tip_fees": "Tip Fees / Waste",
        "ppe_safety": "PPE & Safety Gear",
        "insurance": "Insurance",
        "phone_internet": "Phone / Internet",
        "office": "Office & Admin",
        "accounting": "Accountant / BAS Agent",
        "marketing": "Marketing",
        "other": "Other",
        # Legacy categories (before update)
        "materials": "Materials",
        "equipment": "Equipment & Tools",
        "utilities": "Utilities",
    }

    breakdown = []
    for row in result.all():
        cat_key = row[0] or "other"
        amount = row[2]
        gst = row[3]
        breakdown.append({
            "category": cat_key,
            "display_name": names.get(cat_key, cat_key.replace("_", " ").title()),
            "count": row[1],
            "amount_cents": amount,
            "gst_cents": gst,
            "total_cents": amount + gst,
        })

    return breakdown


# =============================================================================
# GST CLAIMABLE REFERENCE — CONCRETING BUSINESS
# =============================================================================

GST_CLAIMABLE_ITEMS = {
    "claimable": [
        {"item": "Concrete supply (Boral, Hanson, etc.)", "note": "Full GST credit"},
        {"item": "Pump hire", "note": "Full GST credit"},
        {"item": "Steel, mesh, rebar", "note": "Full GST credit"},
        {"item": "Formwork & boxing timber", "note": "Full GST credit"},
        {"item": "Tools & equipment purchases", "note": "Full GST credit"},
        {"item": "Equipment hire (bobcat, excavator)", "note": "Full GST credit"},
        {"item": "PPE & safety gear", "note": "Full GST credit"},
        {"item": "Fuel (business use)", "note": "Full GST credit if 100% business"},
        {"item": "Vehicle running costs", "note": "Business-use portion only"},
        {"item": "Tip fees / waste disposal", "note": "Full GST credit"},
        {"item": "Phone & internet", "note": "Business-use portion only"},
        {"item": "Accountant / BAS agent fees", "note": "Full GST credit"},
        {"item": "Work clothing with logo", "note": "Full GST credit"},
        {"item": "Office supplies & stationery", "note": "Full GST credit"},
        {"item": "Subcontractor payments", "note": "If sub is registered for GST"},
        {"item": "Software subscriptions", "note": "Full GST credit"},
        {"item": "Advertising & marketing", "note": "Full GST credit"},
    ],
    "not_claimable": [
        {"item": "Private vehicle expenses", "note": "No claim on personal use"},
        {"item": "Entertainment & meals", "note": "No GST credit (FBT applies)"},
        {"item": "Fines & penalties", "note": "Not deductible, no GST"},
        {"item": "Private phone use", "note": "Apportion business vs personal"},
        {"item": "Insurance (some types)", "note": "Check — some policies are input-taxed"},
        {"item": "Bank fees & interest", "note": "GST-free (no GST to claim)"},
        {"item": "Government fees & licences", "note": "Most are GST-free"},
        {"item": "Wages to employees", "note": "No GST on wages"},
        {"item": "Donations", "note": "No GST credit"},
    ],
}


# =============================================================================
# ATO COMPLIANCE CALENDAR
# =============================================================================

def get_compliance_deadlines(fy: int) -> List[dict]:
    """
    Get all ATO / business compliance deadlines for a financial year.

    Covers:
      - BAS lodgement (quarterly)
      - Super Guarantee (quarterly)
      - TPAR (annual)
      - PAYG Instalment (if applicable)
      - Workers Comp renewal (typical)
      - Public Liability renewal (typical)

    Args:
        fy: Financial year end year (e.g. 2026 for FY2026 = Jul 2025 - Jun 2026)

    Returns:
        List of deadline dicts sorted by date, each with:
          name, due_date, category, description, quarter (if applicable)
    """
    deadlines = []

    # ── BAS Lodgement ──
    bas_info = {
        1: ("Q1 BAS Lodgement", "Lodge BAS for Jul-Sep. Report GST collected & paid."),
        2: ("Q2 BAS Lodgement", "Lodge BAS for Oct-Dec. Report GST collected & paid."),
        3: ("Q3 BAS Lodgement", "Lodge BAS for Jan-Mar. Report GST collected & paid."),
        4: ("Q4 BAS Lodgement", "Lodge BAS for Apr-Jun. Report GST collected & paid."),
    }
    for q in range(1, 5):
        due = get_bas_due_date(fy, q)
        name, desc = bas_info[q]
        deadlines.append({
            "name": name,
            "due_date": due,
            "category": "bas",
            "description": desc,
            "quarter": q,
            "icon": "calculator",
        })

    # ── Super Guarantee ──
    # Due 28th of month after quarter end
    # Q1 (Jul-Sep): 28 October
    # Q2 (Oct-Dec): 28 January
    # Q3 (Jan-Mar): 28 April
    # Q4 (Apr-Jun): 28 July
    super_dates = {
        1: (date(fy - 1, 10, 28), "Super for Jul-Sep wages. 11.5% of ordinary time earnings."),
        2: (date(fy, 1, 28), "Super for Oct-Dec wages. 11.5% of ordinary time earnings."),
        3: (date(fy, 4, 28), "Super for Jan-Mar wages. 11.5% of ordinary time earnings."),
        4: (date(fy, 7, 28), "Super for Apr-Jun wages. 11.5% of ordinary time earnings."),
    }
    for q, (due, desc) in super_dates.items():
        deadlines.append({
            "name": f"Q{q} Super Guarantee",
            "due_date": due,
            "category": "super",
            "description": desc,
            "quarter": q,
            "icon": "shield",
        })

    # ── TPAR (Taxable Payments Annual Report) ──
    # Due 28 August following end of FY
    # For building/construction industry: report payments to subcontractors
    deadlines.append({
        "name": "TPAR Lodgement",
        "due_date": date(fy, 8, 28),
        "category": "tpar",
        "description": f"Report all payments to subcontractors for FY{fy}. Required for building & construction.",
        "quarter": None,
        "icon": "document",
    })

    # ── Income Tax Return ──
    # Self-lodgement due 31 October, tax agent extension usually to 15 May
    deadlines.append({
        "name": f"FY{fy} Tax Return (self-lodge)",
        "due_date": date(fy, 10, 31),
        "category": "tax",
        "description": f"Individual/sole trader tax return for FY{fy}. Due 31 Oct if self-lodging.",
        "quarter": None,
        "icon": "document",
    })

    # ── Workers Comp Renewal ──
    # Typically renews annually — we set it at 30 June (end of FY)
    deadlines.append({
        "name": "Workers Comp Renewal",
        "due_date": date(fy, 6, 30),
        "category": "insurance",
        "description": "Review and renew Workers Compensation insurance. Check premium based on wages declared.",
        "quarter": None,
        "icon": "shield",
    })

    # ── Public Liability Renewal ──
    # Also typically annual — varies by insurer
    deadlines.append({
        "name": "Public Liability Check",
        "due_date": date(fy, 6, 30),
        "category": "insurance",
        "description": "Check Public Liability insurance is current. Required for most construction work.",
        "quarter": None,
        "icon": "shield",
    })

    # Sort by date
    deadlines.sort(key=lambda d: d["due_date"])

    return deadlines


def get_compliance_status(deadlines: List[dict], today: Optional[date] = None) -> List[dict]:
    """
    Enrich deadline list with status info (overdue, due_soon, upcoming, done).

    Returns the same list with added: status, status_label, days_remaining
    """
    if today is None:
        today = sydney_today()

    enriched = []
    for d in deadlines:
        due = d["due_date"]
        days = (due - today).days

        if days < 0:
            status = "overdue"
            label = f"Overdue by {abs(days)} days"
        elif days <= 14:
            status = "due_soon"
            label = f"Due in {days} days"
        elif days <= 60:
            status = "upcoming"
            label = f"Due in {days} days"
        else:
            status = "future"
            label = due.strftime("%d %b %Y")

        enriched.append({
            **d,
            "status": status,
            "status_label": label,
            "days_remaining": days,
        })

    return enriched
