"""
Workers service — Business logic for worker and job assignment operations.

Handles CRUD for workers and job assignments.
"""

from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from fastapi import Request

from app.models import Worker, JobAssignment, TimeEntry, Quote, ActivityLog
from app.schemas import WorkerCreate, WorkerUpdate, JobAssignmentCreate, JobAssignmentUpdate
from app.core.dates import sydney_now


# =============================================================================
# WORKER CRUD
# =============================================================================

async def create_worker(
    db: AsyncSession,
    data: WorkerCreate,
    request: Request,
) -> Worker:
    """
    Create a new worker.

    If hourly_rate_cents or cost_rate_cents are 0, looks up the role's
    default rates from Settings (crew sell rate / loaded rate).

    Args:
        db: Database session
        data: Worker creation data
        request: HTTP request for IP logging

    Returns:
        Created Worker
    """
    hourly_rate = data.hourly_rate_cents
    cost_rate = data.cost_rate_cents

    # Apply role-based defaults from settings if rates are zero
    if hourly_rate == 0 or cost_rate == 0:
        from app.quotes.pricing import get_crew_rates_async
        crew_rates = await get_crew_rates_async(db)
        role_rates = crew_rates.get(data.role, {})
        if hourly_rate == 0:
            hourly_rate = role_rates.get("sell", 0)
        if cost_rate == 0:
            cost_rate = role_rates.get("loaded", 0)

    worker = Worker(
        name=data.name,
        role=data.role,
        hourly_rate_cents=hourly_rate,
        cost_rate_cents=cost_rate,
        phone=data.phone,
        email=data.email,
        notes=data.notes,
        active=True,
    )

    db.add(worker)
    await db.flush()

    # Log activity
    activity = ActivityLog(
        action="worker_created",
        description=f"Created worker: {worker.name}",
        entity_type="worker",
        entity_id=worker.id,
        ip_address=request.client.host if request.client else None,
        extra_data={"role": worker.role},
    )
    db.add(activity)

    return worker


async def get_workers(
    db: AsyncSession,
    active_only: bool = True,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Worker], int]:
    """
    Get paginated list of workers.

    Args:
        db: Database session
        active_only: Only return active workers
        page: Page number (1-indexed)
        page_size: Items per page

    Returns:
        tuple of (workers, total_count)
    """
    offset = (page - 1) * page_size

    # Base query
    query = select(Worker).order_by(Worker.name)
    count_query = select(func.count(Worker.id))

    # Apply active filter
    if active_only:
        query = query.where(Worker.active == True)
        count_query = count_query.where(Worker.active == True)

    # Execute
    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(query.offset(offset).limit(page_size))
    workers = result.scalars().all()

    return list(workers), total


async def get_worker(db: AsyncSession, worker_id: int) -> Optional[Worker]:
    """Get a single worker by ID."""
    return await db.get(Worker, worker_id)


async def update_worker(
    db: AsyncSession,
    worker: Worker,
    data: WorkerUpdate,
    request: Request,
) -> Worker:
    """
    Update a worker.

    Args:
        db: Database session
        worker: Worker to update
        data: Update data
        request: HTTP request for IP logging

    Returns:
        Updated Worker
    """
    update_data = data.model_dump(exclude_unset=True)
    changes = list(update_data.keys())

    for key, value in update_data.items():
        setattr(worker, key, value)

    # Log activity
    activity = ActivityLog(
        action="worker_updated",
        description=f"Updated worker: {worker.name}",
        entity_type="worker",
        entity_id=worker.id,
        ip_address=request.client.host if request.client else None,
        extra_data={"changes": changes},
    )
    db.add(activity)

    return worker


async def deactivate_worker(
    db: AsyncSession,
    worker: Worker,
    request: Request,
) -> Worker:
    """
    Deactivate (soft delete) a worker.

    Args:
        db: Database session
        worker: Worker to deactivate
        request: HTTP request for IP logging

    Returns:
        Deactivated Worker
    """
    worker.active = False

    # Log activity
    activity = ActivityLog(
        action="worker_deactivated",
        description=f"Deactivated worker: {worker.name}",
        entity_type="worker",
        entity_id=worker.id,
        ip_address=request.client.host if request.client else None,
    )
    db.add(activity)

    return worker


async def permanently_delete_worker(
    db: AsyncSession,
    worker: Worker,
    request: Request,
) -> None:
    """
    Permanently delete a worker and all related records.

    Removes job assignments, time entries, activity logs, then the worker.

    Args:
        db: Database session
        worker: Worker to delete
        request: HTTP request for IP logging
    """
    worker_name = worker.name
    worker_id = worker.id

    # Delete job assignments
    assignments = await db.execute(
        select(JobAssignment).where(JobAssignment.worker_id == worker_id)
    )
    for a in assignments.scalars().all():
        await db.delete(a)

    # Delete time entries
    time_entries = await db.execute(
        select(TimeEntry).where(TimeEntry.worker_id == worker_id)
    )
    for t in time_entries.scalars().all():
        await db.delete(t)

    # Delete activity logs for this worker
    activity_logs = await db.execute(
        select(ActivityLog).where(
            ActivityLog.entity_type == "worker",
            ActivityLog.entity_id == worker_id,
        )
    )
    for log in activity_logs.scalars().all():
        await db.delete(log)

    # Delete the worker
    await db.delete(worker)

    # Log the permanent deletion (general log, not tied to the worker)
    activity = ActivityLog(
        action="worker_deleted",
        description=f"Permanently deleted worker: {worker_name} (ID: {worker_id})",
        entity_type="system",
        entity_id=0,
        ip_address=request.client.host if request.client else None,
    )
    db.add(activity)


# =============================================================================
# JOB ASSIGNMENT CRUD
# =============================================================================

async def assign_worker_to_job(
    db: AsyncSession,
    data: JobAssignmentCreate,
    request: Request,
) -> JobAssignment:
    """
    Assign a worker to a job (quote).

    Args:
        db: Database session
        data: Assignment data
        request: HTTP request for IP logging

    Returns:
        Created JobAssignment

    Raises:
        ValueError: If quote or worker not found, or already assigned
    """
    # Verify quote exists
    quote = await db.get(Quote, data.quote_id)
    if not quote:
        raise ValueError(f"Quote {data.quote_id} not found")

    # Verify worker exists
    worker = await db.get(Worker, data.worker_id)
    if not worker:
        raise ValueError(f"Worker {data.worker_id} not found")

    # Check if already assigned
    existing = await db.execute(
        select(JobAssignment).where(
            JobAssignment.quote_id == data.quote_id,
            JobAssignment.worker_id == data.worker_id,
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError(f"Worker {worker.name} is already assigned to this job")

    assignment = JobAssignment(
        quote_id=data.quote_id,
        worker_id=data.worker_id,
        role=data.role or worker.role,  # Default to worker's role
        notes=data.notes,
        confirmed=False,
    )

    db.add(assignment)
    await db.flush()

    # Log activity
    activity = ActivityLog(
        action="worker_assigned",
        description=f"Assigned {worker.name} to quote {quote.quote_number}",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=request.client.host if request.client else None,
        extra_data={
            "worker_id": worker.id,
            "worker_name": worker.name,
            "role": assignment.role,
        },
    )
    db.add(activity)

    return assignment


async def get_job_assignments(
    db: AsyncSession,
    quote_id: int,
) -> list[JobAssignment]:
    """
    Get all worker assignments for a job.

    Args:
        db: Database session
        quote_id: Quote ID

    Returns:
        List of JobAssignment with workers loaded
    """
    result = await db.execute(
        select(JobAssignment)
        .where(JobAssignment.quote_id == quote_id)
        .options(selectinload(JobAssignment.worker))
        .order_by(JobAssignment.created_at)
    )
    return list(result.scalars().all())


async def get_worker_assignments(
    db: AsyncSession,
    worker_id: int,
) -> list[JobAssignment]:
    """
    Get all job assignments for a worker.

    Args:
        db: Database session
        worker_id: Worker ID

    Returns:
        List of JobAssignment with quotes loaded
    """
    result = await db.execute(
        select(JobAssignment)
        .where(JobAssignment.worker_id == worker_id)
        .options(selectinload(JobAssignment.quote))
        .order_by(JobAssignment.created_at.desc())
    )
    return list(result.scalars().all())


async def update_assignment(
    db: AsyncSession,
    assignment: JobAssignment,
    data: JobAssignmentUpdate,
    request: Request,
) -> JobAssignment:
    """
    Update a job assignment.

    Args:
        db: Database session
        assignment: Assignment to update
        data: Update data
        request: HTTP request for IP logging

    Returns:
        Updated JobAssignment
    """
    update_data = data.model_dump(exclude_unset=True)
    changes = list(update_data.keys())

    for key, value in update_data.items():
        setattr(assignment, key, value)

    # Log activity
    activity = ActivityLog(
        action="assignment_updated",
        description=f"Updated job assignment {assignment.id}",
        entity_type="quote",
        entity_id=assignment.quote_id,
        ip_address=request.client.host if request.client else None,
        extra_data={
            "assignment_id": assignment.id,
            "changes": changes,
        },
    )
    db.add(activity)

    return assignment


async def remove_assignment(
    db: AsyncSession,
    assignment_id: int,
    request: Request,
) -> None:
    """
    Remove a job assignment.

    Args:
        db: Database session
        assignment_id: Assignment ID to remove
        request: HTTP request for IP logging

    Raises:
        ValueError: If assignment not found
    """
    assignment = await db.get(JobAssignment, assignment_id)
    if not assignment:
        raise ValueError(f"Assignment {assignment_id} not found")

    # Get worker name for logging
    worker = await db.get(Worker, assignment.worker_id)
    worker_name = worker.name if worker else "Unknown"

    # Get quote number for logging
    quote = await db.get(Quote, assignment.quote_id)
    quote_number = quote.quote_number if quote else "Unknown"

    # Log activity
    activity = ActivityLog(
        action="worker_unassigned",
        description=f"Removed {worker_name} from quote {quote_number}",
        entity_type="quote",
        entity_id=assignment.quote_id,
        ip_address=request.client.host if request.client else None,
        extra_data={
            "worker_id": assignment.worker_id,
            "worker_name": worker_name,
        },
    )
    db.add(activity)

    await db.delete(assignment)
