"""Crew availability checker -- Check which workers are free on a given date."""

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Worker, JobAssignment, Quote
from app.core.dates import sydney_today

logger = logging.getLogger(__name__)


async def check_availability(
    db: AsyncSession,
    target_date: date,
) -> list[dict]:
    """
    Check which workers are available on a given date.

    Returns a list of dicts, one per active worker:
        {
            "worker_id": int,
            "name": str,
            "role": str,
            "is_available": bool,
            "assigned_job": str | None,   # quote_number if busy
            "job_name": str | None,       # job name if busy
        }

    A worker is considered "busy" when they have a JobAssignment linked to a
    Quote whose confirmed_start_date equals *target_date* and whose status is
    one of ("confirmed", "pour_stage").
    """
    # 1. Get all active workers
    workers_result = await db.execute(
        select(Worker)
        .where(Worker.active == True)
        .order_by(Worker.name)
    )
    workers = workers_result.scalars().all()

    # 2. Find all job assignments for the target date
    #    Join JobAssignment -> Quote where the quote is scheduled on target_date
    busy_query = (
        select(JobAssignment, Quote)
        .join(Quote, JobAssignment.quote_id == Quote.id)
        .where(
            and_(
                Quote.confirmed_start_date == target_date,
                Quote.status.in_(["confirmed", "pour_stage"]),
            )
        )
    )
    busy_result = await db.execute(busy_query)
    busy_rows = busy_result.all()

    # Build a lookup: worker_id -> (quote_number, job_name)
    busy_map: dict[int, tuple[str, Optional[str]]] = {}
    for assignment, quote in busy_rows:
        busy_map[assignment.worker_id] = (
            quote.quote_number,
            quote.job_name,
        )

    # 3. Build result list
    availability = []
    for worker in workers:
        is_busy = worker.id in busy_map
        entry = {
            "worker_id": worker.id,
            "name": worker.name,
            "role": worker.role,
            "is_available": not is_busy,
            "assigned_job": busy_map[worker.id][0] if is_busy else None,
            "job_name": busy_map[worker.id][1] if is_busy else None,
        }
        availability.append(entry)

    return availability


async def get_available_workers(
    db: AsyncSession,
    target_date: date,
    role: Optional[str] = None,
) -> list[Worker]:
    """
    Return only workers who are available (not assigned to a job) on the
    given date, optionally filtered by role.

    Args:
        db: Async database session.
        target_date: The date to check.
        role: Optional role filter (finisher, experienced_labourer, labourer).

    Returns:
        List of available Worker objects.
    """
    # Get busy worker IDs for the target date
    busy_ids_query = (
        select(JobAssignment.worker_id)
        .join(Quote, JobAssignment.quote_id == Quote.id)
        .where(
            and_(
                Quote.confirmed_start_date == target_date,
                Quote.status.in_(["confirmed", "pour_stage"]),
            )
        )
    )
    busy_result = await db.execute(busy_ids_query)
    busy_worker_ids = {row[0] for row in busy_result.all()}

    # Query active workers, excluding busy ones
    query = (
        select(Worker)
        .where(Worker.active == True)
        .order_by(Worker.name)
    )
    if busy_worker_ids:
        query = query.where(Worker.id.notin_(busy_worker_ids))
    if role:
        query = query.where(Worker.role == role)

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_crew_calendar(
    db: AsyncSession,
    start_date: date,
    end_date: date,
) -> dict[str, list[dict]]:
    """
    Build a calendar view of crew assignments over a date range.

    Returns a dict mapping ISO date strings to lists of assignment info:
        {
            "2026-02-26": [
                {"worker_name": "John", "quote_number": "Q-00012", "job_name": "Smith Driveway"},
                ...
            ],
            ...
        }

    Only includes dates that have at least one assignment.
    """
    # Query all assignments in the date range
    query = (
        select(JobAssignment, Quote, Worker)
        .join(Quote, JobAssignment.quote_id == Quote.id)
        .join(Worker, JobAssignment.worker_id == Worker.id)
        .where(
            and_(
                Quote.confirmed_start_date >= start_date,
                Quote.confirmed_start_date <= end_date,
                Quote.status.in_(["confirmed", "pour_stage"]),
                Worker.active == True,
            )
        )
        .order_by(Quote.confirmed_start_date, Worker.name)
    )
    result = await db.execute(query)
    rows = result.all()

    calendar: dict[str, list[dict]] = {}
    for assignment, quote, worker in rows:
        date_key = quote.confirmed_start_date.isoformat()
        if date_key not in calendar:
            calendar[date_key] = []
        calendar[date_key].append({
            "worker_name": worker.name,
            "worker_role": worker.role,
            "quote_number": quote.quote_number,
            "job_name": quote.job_name or "",
        })

    return calendar
