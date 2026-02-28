"""
Worker routes — CRUD operations for workers and job assignments.

Follows the established pattern from customers/routes.py.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Worker, JobAssignment, Quote, ActivityLog
from app.schemas import (
    WorkerCreate, WorkerUpdate, WorkerResponse,
    JobAssignmentCreate, JobAssignmentUpdate, JobAssignmentResponse,
    PaginatedResponse, SuccessResponse
)
from app.core.auth import require_login, verify_csrf
from app.core.templates import templates
from app.workers.service import (
    create_worker, get_workers, get_worker, update_worker, deactivate_worker,
    permanently_delete_worker,
    assign_worker_to_job, get_job_assignments, get_worker_assignments,
    update_assignment, remove_assignment,
)
from app.workers.availability import check_availability, get_available_workers, get_crew_calendar
from app.core.dates import sydney_today, parse_date

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


# =============================================================================
# HTML PAGES
# =============================================================================

@router.get("", name="workers:list")
async def worker_list_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    q: Optional[str] = None,
    show_inactive: bool = False,
    page: int = Query(1, ge=1),
):
    """Worker list page."""
    page_size = 20
    offset = (page - 1) * page_size

    # Base query
    query = select(Worker).order_by(Worker.name)
    count_query = select(func.count(Worker.id))

    # Active filter
    if not show_inactive:
        query = query.where(Worker.active == True)
        count_query = count_query.where(Worker.active == True)

    # Search filter
    if q:
        search = f"%{q}%"
        query = query.where(
            (Worker.name.ilike(search)) |
            (Worker.phone.ilike(search)) |
            (Worker.email.ilike(search))
        )
        count_query = count_query.where(
            (Worker.name.ilike(search)) |
            (Worker.phone.ilike(search)) |
            (Worker.email.ilike(search))
        )

    # Execute
    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(query.offset(offset).limit(page_size))
    workers = result.scalars().all()

    return templates.TemplateResponse("workers/list.html", {
        "request": request,
        "workers": workers,
        "search": q,
        "show_inactive": show_inactive,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size if total > 0 else 1,
    })


@router.get("/availability", name="workers:availability")
async def worker_availability_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Crew availability checker page."""
    today = sydney_today()
    return templates.TemplateResponse("workers/availability.html", {
        "request": request,
        "today": today.isoformat(),
    })


@router.get("/new", name="workers:new")
async def worker_new_page(request: Request):
    """New worker form."""
    return templates.TemplateResponse("workers/form.html", {
        "request": request,
        "worker": None,
        "is_new": True,
    })


@router.get("/{id}", name="workers:detail")
async def worker_detail_page(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Worker detail page."""
    worker = await db.get(Worker, id)
    if not worker:
        raise HTTPException(404, "Worker not found")

    # Get job assignments
    assignments = await get_worker_assignments(db, id)

    # Get activity
    activity_result = await db.execute(
        select(ActivityLog)
        .where(ActivityLog.entity_type == "worker")
        .where(ActivityLog.entity_id == id)
        .order_by(ActivityLog.created_at.desc())
        .limit(20)
    )
    activity = activity_result.scalars().all()

    return templates.TemplateResponse("workers/detail.html", {
        "request": request,
        "worker": worker,
        "assignments": assignments,
        "activity": activity,
    })


@router.get("/{id}/edit", name="workers:edit")
async def worker_edit_page(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Edit worker form."""
    worker = await db.get(Worker, id)
    if not worker:
        raise HTTPException(404, "Worker not found")

    return templates.TemplateResponse("workers/form.html", {
        "request": request,
        "worker": worker,
        "is_new": False,
    })


# =============================================================================
# API ENDPOINTS - WORKERS
# =============================================================================

@router.get("/api/crew-rates")
async def api_crew_rates(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """API: Get default crew rates per role from Settings.

    Used by the worker form to pre-fill rates when creating/editing workers.
    Returns rates for worker-selectable roles (excludes owner).
    """
    from app.quotes.pricing import get_crew_rates_async
    rates = await get_crew_rates_async(db)
    # Only return roles valid for workers (exclude owner)
    return {
        role: rates[role]
        for role in ["finisher", "experienced_labourer", "labourer"]
        if role in rates
    }


@router.get("/api/availability")
async def api_availability(
    db: AsyncSession = Depends(get_db),
    date: Optional[str] = None,
):
    """API: Get worker availability for a given date.

    Query params:
        date: ISO date string (YYYY-MM-DD). Defaults to today (Sydney).

    Returns list of worker availability dicts.
    """
    target_date = parse_date(date) if date else sydney_today()
    if target_date is None:
        target_date = sydney_today()

    workers = await check_availability(db, target_date)
    return {
        "date": target_date.isoformat(),
        "workers": workers,
    }


@router.get("/api/calendar")
async def api_calendar(
    db: AsyncSession = Depends(get_db),
    start: Optional[str] = None,
    end: Optional[str] = None,
):
    """API: Get crew calendar data for a date range.

    Query params:
        start: ISO date string (YYYY-MM-DD). Defaults to today (Sydney).
        end:   ISO date string (YYYY-MM-DD). Defaults to start + 6 days.

    Returns dict of date -> list of assignment info.
    """
    from datetime import timedelta

    start_date = parse_date(start) if start else sydney_today()
    if start_date is None:
        start_date = sydney_today()

    end_date = parse_date(end) if end else start_date + timedelta(days=6)
    if end_date is None:
        end_date = start_date + timedelta(days=6)

    calendar = await get_crew_calendar(db, start_date, end_date)
    return {
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "calendar": calendar,
    }


@router.get("/api/list")
async def api_worker_list(
    db: AsyncSession = Depends(get_db),
    q: Optional[str] = None,
    active_only: bool = True,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PaginatedResponse:
    """API: List workers with pagination."""
    offset = (page - 1) * page_size

    query = select(Worker).order_by(Worker.name)
    count_query = select(func.count(Worker.id))

    if active_only:
        query = query.where(Worker.active == True)
        count_query = count_query.where(Worker.active == True)

    if q:
        search = f"%{q}%"
        query = query.where(
            (Worker.name.ilike(search)) |
            (Worker.phone.ilike(search)) |
            (Worker.email.ilike(search))
        )
        count_query = count_query.where(
            (Worker.name.ilike(search)) |
            (Worker.phone.ilike(search)) |
            (Worker.email.ilike(search))
        )

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(query.offset(offset).limit(page_size))
    workers = result.scalars().all()

    return PaginatedResponse(
        items=[WorkerResponse.model_validate(w) for w in workers],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size if total > 0 else 1,
    )


@router.post("/api/create")
async def api_worker_create(
    data: WorkerCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> WorkerResponse:
    """API: Create a worker."""
    worker = await create_worker(db, data, request)
    await db.commit()
    await db.refresh(worker)

    return WorkerResponse.model_validate(worker)


@router.get("/api/{id}")
async def api_worker_get(
    id: int,
    db: AsyncSession = Depends(get_db),
) -> WorkerResponse:
    """API: Get a single worker."""
    worker = await db.get(Worker, id)
    if not worker:
        raise HTTPException(404, "Worker not found")

    return WorkerResponse.model_validate(worker)


@router.put("/api/{id}")
async def api_worker_update(
    id: int,
    data: WorkerUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> WorkerResponse:
    """API: Update a worker."""
    worker = await db.get(Worker, id)
    if not worker:
        raise HTTPException(404, "Worker not found")

    worker = await update_worker(db, worker, data, request)
    await db.commit()
    await db.refresh(worker)

    return WorkerResponse.model_validate(worker)


@router.delete("/api/{id}")
async def api_worker_delete(
    id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    """API: Deactivate a worker (soft delete)."""
    worker = await db.get(Worker, id)
    if not worker:
        raise HTTPException(404, "Worker not found")

    await deactivate_worker(db, worker, request)
    await db.commit()

    return SuccessResponse(message="Worker deactivated")


@router.delete("/api/{id}/permanent")
async def api_worker_permanent_delete(
    id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    """API: Permanently delete a worker and all related data."""
    worker = await db.get(Worker, id)
    if not worker:
        raise HTTPException(404, "Worker not found")

    await permanently_delete_worker(db, worker, request)
    await db.commit()

    return SuccessResponse(message="Worker permanently deleted")


@router.get("/api/{id}/activity")
async def api_worker_activity(
    id: int,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
):
    """API: Get worker activity log."""
    result = await db.execute(
        select(ActivityLog)
        .where(ActivityLog.entity_type == "worker")
        .where(ActivityLog.entity_id == id)
        .order_by(ActivityLog.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


# =============================================================================
# API ENDPOINTS - JOB ASSIGNMENTS
# =============================================================================

@router.post("/api/assign")
async def api_assign_worker(
    data: JobAssignmentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JobAssignmentResponse:
    """API: Assign a worker to a job."""
    try:
        assignment = await assign_worker_to_job(db, data, request)
        await db.commit()

        # Reload with worker relationship
        result = await db.execute(
            select(JobAssignment)
            .where(JobAssignment.id == assignment.id)
            .options(selectinload(JobAssignment.worker))
        )
        assignment = result.scalar_one()

        response = JobAssignmentResponse.model_validate(assignment)
        response.worker_name = assignment.worker.name if assignment.worker else None
        return response

    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/assignments/{quote_id}")
async def api_get_assignments(
    quote_id: int,
    db: AsyncSession = Depends(get_db),
) -> list[JobAssignmentResponse]:
    """API: Get all assignments for a job."""
    assignments = await get_job_assignments(db, quote_id)

    responses = []
    for a in assignments:
        response = JobAssignmentResponse.model_validate(a)
        response.worker_name = a.worker.name if a.worker else None
        responses.append(response)

    return responses


@router.put("/api/assignment/{id}")
async def api_update_assignment(
    id: int,
    data: JobAssignmentUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JobAssignmentResponse:
    """API: Update a job assignment."""
    assignment = await db.get(JobAssignment, id)
    if not assignment:
        raise HTTPException(404, "Assignment not found")

    assignment = await update_assignment(db, assignment, data, request)
    await db.commit()

    # Reload with worker relationship
    result = await db.execute(
        select(JobAssignment)
        .where(JobAssignment.id == assignment.id)
        .options(selectinload(JobAssignment.worker))
    )
    assignment = result.scalar_one()

    response = JobAssignmentResponse.model_validate(assignment)
    response.worker_name = assignment.worker.name if assignment.worker else None
    return response


@router.delete("/api/assignment/{id}")
async def api_remove_assignment(
    id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    """API: Remove a job assignment."""
    try:
        await remove_assignment(db, id, request)
        await db.commit()
        return SuccessResponse(message="Assignment removed")
    except ValueError as e:
        raise HTTPException(404, str(e))
