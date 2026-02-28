"""
Earnings Tasks — Background tasks for recording earnings snapshots.

Handles:
- Weekly earnings snapshot (runs every Monday)
- Monthly earnings snapshot (runs 1st of each month)
"""

import logging
from datetime import date, timedelta

from app.celery_app import celery_app
from app.core.dates import sydney_today
from app.database import get_sync_session
from app.models import EarningsSnapshot
from app.reports.take_home import (
    get_period_dates_sync,
    calculate_take_home_sync,
)

logger = logging.getLogger(__name__)


@celery_app.task
def record_weekly_snapshot():
    """
    Record weekly earnings snapshot.

    Runs every Monday at 1am for the previous week.
    """
    with get_sync_session() as db:
        today = sydney_today()

        # Get last week's dates (Monday to Sunday)
        # If today is Monday, get the week before
        days_since_monday = today.weekday()
        last_monday = today - timedelta(days=days_since_monday + 7)
        last_sunday = last_monday + timedelta(days=6)

        # Check if snapshot already exists
        existing = db.query(EarningsSnapshot).filter(
            EarningsSnapshot.period_type == "weekly",
            EarningsSnapshot.period_start == last_monday,
        ).first()

        if existing:
            logger.info(f"Weekly snapshot for {last_monday} already exists")
            return f"Snapshot already exists for {last_monday}"

        # Calculate take-home for last week
        take_home = calculate_take_home_sync(db, last_monday, last_sunday)

        # Create snapshot with enhanced breakdown
        snapshot = EarningsSnapshot(
            period_type="weekly",
            period_start=last_monday,
            period_end=last_sunday,
            # Revenue breakdown
            revenue_cents=take_home["revenue_ex_gst_cents"],  # Ex GST for backwards compat
            revenue_inc_gst_cents=take_home.get("revenue_inc_gst_cents", 0),
            gst_collected_cents=take_home.get("gst_collected_cents", 0),
            # Costs
            materials_cents=take_home["materials_cents"],
            labour_cents=take_home["labour_total_cents"],  # Total for backwards compat
            labour_wages_cents=take_home.get("labour_wages_cents", 0),
            labour_super_cents=take_home.get("labour_super_cents", 0),
            labour_payg_cents=take_home.get("labour_payg_cents", 0),
            expenses_cents=take_home["expenses_cents"],
            # Final
            take_home_cents=take_home["take_home_cents"],
            jobs_completed=take_home["jobs_completed"],
        )
        db.add(snapshot)
        db.commit()

        logger.info(f"Recorded weekly snapshot for {last_monday} to {last_sunday}: ${take_home['take_home_cents']/100:,.2f}")
        return f"Recorded snapshot for {last_monday} to {last_sunday}: ${take_home['take_home_cents']/100:,.2f}"


@celery_app.task
def record_monthly_snapshot():
    """
    Record monthly earnings snapshot.

    Runs on the 1st of each month at 2am for the previous month.
    """
    with get_sync_session() as db:
        today = sydney_today()

        # Get last month's dates
        first_of_this_month = today.replace(day=1)
        last_day_prev_month = first_of_this_month - timedelta(days=1)
        first_of_prev_month = last_day_prev_month.replace(day=1)

        # Check if snapshot already exists
        existing = db.query(EarningsSnapshot).filter(
            EarningsSnapshot.period_type == "monthly",
            EarningsSnapshot.period_start == first_of_prev_month,
        ).first()

        if existing:
            month_name = first_of_prev_month.strftime('%B %Y')
            logger.info(f"Monthly snapshot for {month_name} already exists")
            return f"Snapshot already exists for {month_name}"

        # Calculate take-home for last month
        take_home = calculate_take_home_sync(db, first_of_prev_month, last_day_prev_month)

        # Create snapshot with enhanced breakdown
        snapshot = EarningsSnapshot(
            period_type="monthly",
            period_start=first_of_prev_month,
            period_end=last_day_prev_month,
            # Revenue breakdown
            revenue_cents=take_home["revenue_ex_gst_cents"],  # Ex GST for backwards compat
            revenue_inc_gst_cents=take_home.get("revenue_inc_gst_cents", 0),
            gst_collected_cents=take_home.get("gst_collected_cents", 0),
            # Costs
            materials_cents=take_home["materials_cents"],
            labour_cents=take_home["labour_total_cents"],  # Total for backwards compat
            labour_wages_cents=take_home.get("labour_wages_cents", 0),
            labour_super_cents=take_home.get("labour_super_cents", 0),
            labour_payg_cents=take_home.get("labour_payg_cents", 0),
            expenses_cents=take_home["expenses_cents"],
            # Final
            take_home_cents=take_home["take_home_cents"],
            jobs_completed=take_home["jobs_completed"],
        )
        db.add(snapshot)
        db.commit()

        month_name = first_of_prev_month.strftime('%B %Y')
        logger.info(f"Recorded monthly snapshot for {month_name}: ${take_home['take_home_cents']/100:,.2f}")
        return f"Recorded snapshot for {month_name}: ${take_home['take_home_cents']/100:,.2f}"


@celery_app.task
def backfill_earnings_snapshots(months_back: int = 12):
    """
    Backfill monthly earnings snapshots for historical data.

    This is a manual task that can be run to populate historical data.
    """
    with get_sync_session() as db:
        today = sydney_today()
        created_count = 0

        for i in range(1, months_back + 1):
            # Calculate the month we're processing
            year = today.year
            month = today.month - i

            while month <= 0:
                month += 12
                year -= 1

            # Get the date range for this month
            first_of_month = date(year, month, 1)
            if month == 12:
                last_of_month = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                last_of_month = date(year, month + 1, 1) - timedelta(days=1)

            # Check if snapshot already exists
            existing = db.query(EarningsSnapshot).filter(
                EarningsSnapshot.period_type == "monthly",
                EarningsSnapshot.period_start == first_of_month,
            ).first()

            if existing:
                continue

            # Calculate take-home
            take_home = calculate_take_home_sync(db, first_of_month, last_of_month)

            # Create snapshot with enhanced breakdown
            snapshot = EarningsSnapshot(
                period_type="monthly",
                period_start=first_of_month,
                period_end=last_of_month,
                # Revenue breakdown
                revenue_cents=take_home["revenue_ex_gst_cents"],
                revenue_inc_gst_cents=take_home.get("revenue_inc_gst_cents", 0),
                gst_collected_cents=take_home.get("gst_collected_cents", 0),
                # Costs
                materials_cents=take_home["materials_cents"],
                labour_cents=take_home["labour_total_cents"],
                labour_wages_cents=take_home.get("labour_wages_cents", 0),
                labour_super_cents=take_home.get("labour_super_cents", 0),
                labour_payg_cents=take_home.get("labour_payg_cents", 0),
                expenses_cents=take_home["expenses_cents"],
                # Final
                take_home_cents=take_home["take_home_cents"],
                jobs_completed=take_home["jobs_completed"],
            )
            db.add(snapshot)
            created_count += 1
            logger.info(f"Backfilled snapshot for {first_of_month.strftime('%B %Y')}")

        db.commit()
        return f"Backfilled {created_count} monthly snapshots"
