"""
Schedule routes — Job calendar and scheduling views.
"""

from fastapi import APIRouter, Depends, Request, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import date, timedelta
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.core.auth import require_login, verify_csrf
from app.core.templates import templates
from app.core.dates import sydney_now
from app.models import Quote, Customer, ActivityLog, Worker, JobAssignment, PourPlan

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


@router.get("", name="schedule:index")
async def schedule_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Schedule page showing confirmed/scheduled jobs.
    """
    # Get confirmed quotes (jobs) with scheduled dates
    today = sydney_now().date()

    result = await db.execute(
        select(Quote)
        .where(Quote.status.in_(["accepted", "confirmed", "pour_stage"]))
        .where(Quote.confirmed_start_date != None)
        .where(Quote.confirmed_start_date >= today)
        .order_by(Quote.confirmed_start_date.asc())
        .limit(50)
    )
    upcoming_jobs = result.scalars().all()

    # Get customer names
    customer_ids = [j.customer_id for j in upcoming_jobs if j.customer_id]
    customers = {}
    if customer_ids:
        cust_result = await db.execute(
            select(Customer).where(Customer.id.in_(customer_ids))
        )
        for c in cust_result.scalars().all():
            customers[c.id] = c

    return templates.TemplateResponse("schedule/index.html", {
        "request": request,
        "upcoming_jobs": upcoming_jobs,
        "customers": customers,
        "today": today,
        "tomorrow": today + timedelta(days=1),
        "active": "schedule",
    })


@router.get("/api/events", name="schedule:api:events")
async def get_calendar_events(
    request: Request,
    start: str = Query(..., description="Start date (ISO format)"),
    end: str = Query(..., description="End date (ISO format)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Return jobs as calendar events for FullCalendar.

    FullCalendar sends start and end dates to filter events.
    Returns a JSON array of event objects.
    """
    # Parse dates (FullCalendar sends ISO strings)
    try:
        start_date = date.fromisoformat(start[:10])
        end_date = date.fromisoformat(end[:10])
    except (ValueError, TypeError):
        start_date = sydney_now().date()
        end_date = start_date + timedelta(days=90)

    # Get jobs in date range (include pending_completion and completed)
    result = await db.execute(
        select(Quote)
        .where(Quote.status.in_(["accepted", "confirmed", "pour_stage", "pending_completion", "completed"]))
        .where(Quote.confirmed_start_date != None)
        .where(Quote.confirmed_start_date >= start_date)
        .where(Quote.confirmed_start_date <= end_date)
        .order_by(Quote.confirmed_start_date.asc())
    )
    jobs = result.scalars().all()

    # Get customer names
    customer_ids = [j.customer_id for j in jobs if j.customer_id]
    customers = {}
    if customer_ids:
        cust_result = await db.execute(
            select(Customer).where(Customer.id.in_(customer_ids))
        )
        for c in cust_result.scalars().all():
            customers[c.id] = c

    # Build event list
    events = []
    for job in jobs:
        customer = customers.get(job.customer_id)
        customer_name = customer.name if customer else "Customer"

        # Extract suburb from job_address (last part before state/postcode)
        suburb = "TBC"
        if job.job_address:
            parts = job.job_address.split(",")
            if len(parts) >= 2:
                suburb = parts[-2].strip() if len(parts) > 2 else parts[-1].strip()
            else:
                suburb = job.job_address[:20]

        # Calculate end date (default to 1 day)
        end_date_job = job.confirmed_start_date + timedelta(days=1)

        # Determine color based on status
        status_colors = {
            "confirmed": "#3b82f6",      # blue-500
            "pour_stage": "#f97316",     # orange-500
            "pending_completion": "#eab308",  # yellow-500
            "completed": "#22c55e",      # green-500
            "accepted": "#8b5cf6",       # violet-500
        }
        color = status_colors.get(job.status, "#3b82f6")

        events.append({
            "id": str(job.id),
            "title": f"{customer_name} - {suburb}",
            "start": job.confirmed_start_date.isoformat(),
            "end": end_date_job.isoformat(),
            "url": f"/quotes/{job.id}",
            "backgroundColor": color,
            "borderColor": color,
            "extendedProps": {
                "status": job.status,
                "quote_number": job.quote_number,
                "total": (job.total_cents or 0) / 100,
                "customer_name": customer_name,
                "address": job.job_address,
            }
        })

    return events


# =============================================================================
# DRAG & DROP RESCHEDULING
# =============================================================================

class RescheduleRequest(BaseModel):
    """Reschedule a job via drag & drop."""
    quote_id: int
    new_date: str  # ISO date string


@router.patch("/api/reschedule", name="schedule:reschedule")
async def reschedule_job(
    request: Request,
    data: RescheduleRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Reschedule a job by updating its confirmed_start_date.
    Called when an event is dragged on the calendar.
    """
    quote = await db.get(Quote, data.quote_id)
    if not quote:
        raise HTTPException(404, "Job not found")

    if quote.status not in ("accepted", "confirmed", "pour_stage"):
        return {"success": False, "error": f"Cannot reschedule a job in '{quote.status}' status"}

    try:
        new_date_parsed = date.fromisoformat(data.new_date)
    except ValueError:
        return {"success": False, "error": "Invalid date format"}

    old_date = quote.confirmed_start_date
    quote.confirmed_start_date = new_date_parsed

    # Log activity
    activity = ActivityLog(
        action="job_rescheduled",
        description=f"Job {quote.quote_number or quote.id} rescheduled from {old_date} to {new_date_parsed}",
        entity_type="quote",
        entity_id=quote.id,
        ip_address=request.client.host if request.client else None,
        extra_data={
            "old_date": str(old_date) if old_date else None,
            "new_date": str(new_date_parsed),
        },
    )
    db.add(activity)

    # Sync to Google Calendar if connected
    try:
        from app.integrations.google_calendar import update_job_event
        await update_job_event(db, quote)
    except Exception:
        pass  # Don't fail if gcal sync fails

    await db.commit()

    # Send reschedule email to customer (don't fail if email fails)
    try:
        customer = await db.get(Customer, quote.customer_id)
        if customer:
            from app.core.security import decrypt_customer_pii
            decrypt_customer_pii(customer)
            if customer.email:
                from app.notifications.email import send_job_rescheduled_email
                await send_job_rescheduled_email(
                    db=db,
                    quote=quote,
                    customer=customer,
                    old_date=old_date,
                    new_date=new_date_parsed,
                )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to send reschedule email: {e}")

    return {"success": True, "message": f"Job rescheduled to {new_date_parsed}"}


# =============================================================================
# ON MY WAY SMS
# =============================================================================

class OnMyWayRequest(BaseModel):
    """Optional request body for On My Way SMS."""
    eta_minutes: Optional[int] = None


@router.post("/api/on-my-way/{quote_id}", name="schedule:on_my_way")
async def send_on_my_way(
    quote_id: int,
    request: Request,
    data: OnMyWayRequest = OnMyWayRequest(),
    db: AsyncSession = Depends(get_db),
):
    """
    Send 'On My Way' SMS to customer for a scheduled job.

    Accepts optional JSON body with eta_minutes (e.g. 15, 30, 45, 60).
    """
    # Get the quote/job
    quote = await db.get(Quote, quote_id)
    if not quote:
        raise HTTPException(404, "Job not found")

    if not quote.customer_id:
        return {"success": False, "error": "No customer linked to this job"}

    # Get the customer
    customer = await db.get(Customer, quote.customer_id)
    if not customer:
        return {"success": False, "error": "Customer not found"}

    # Send the SMS
    from app.notifications.sms import send_on_my_way_sms
    result = await send_on_my_way_sms(db, quote, customer, eta_minutes=data.eta_minutes)

    if result.get("success"):
        # Log activity
        activity = ActivityLog(
            action="on_my_way_sms_sent",
            description=f"Sent 'On My Way' SMS for job {quote.quote_number or quote.id}",
            entity_type="quote",
            entity_id=quote.id,
            ip_address=request.client.host if request.client else None,
            extra_data={
                "eta_minutes": data.eta_minutes,
                "customer_id": customer.id,
            },
        )
        db.add(activity)
        await db.commit()

    return result


# =============================================================================
# DAILY RUN SHEET
# =============================================================================

@router.get("/run-sheet", name="schedule:run_sheet")
async def daily_run_sheet(
    request: Request,
    for_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Daily run sheet — printable one-pager for tomorrow (or any date).

    Shows: jobs, addresses, crew, concrete times, mix types, customer contacts.
    Designed for print: clean layout, no nav, fits A4.
    """
    today = sydney_now().date()

    if for_date:
        try:
            target_date = date.fromisoformat(for_date)
        except ValueError:
            target_date = today + timedelta(days=1)
    else:
        target_date = today + timedelta(days=1)

    # Get all jobs scheduled for target date
    result = await db.execute(
        select(Quote)
        .where(Quote.status.in_(["accepted", "confirmed", "pour_stage"]))
        .where(Quote.confirmed_start_date == target_date)
        .order_by(Quote.confirmed_start_date.asc())
    )
    jobs = result.scalars().all()

    # Get customers for those jobs
    customer_ids = [j.customer_id for j in jobs if j.customer_id]
    customers = {}
    if customer_ids:
        cust_result = await db.execute(
            select(Customer).where(Customer.id.in_(customer_ids))
        )
        for c in cust_result.scalars().all():
            customers[c.id] = c

    # Get crew assignments for those jobs
    job_ids = [j.id for j in jobs]
    assignments = {}
    workers_map = {}
    if job_ids:
        assign_result = await db.execute(
            select(JobAssignment)
            .where(JobAssignment.quote_id.in_(job_ids))
        )
        for a in assign_result.scalars().all():
            if a.quote_id not in assignments:
                assignments[a.quote_id] = []
            assignments[a.quote_id].append(a)

        # Get all workers referenced
        worker_ids = list({a.worker_id for assigns in assignments.values() for a in assigns})
        if worker_ids:
            worker_result = await db.execute(
                select(Worker).where(Worker.id.in_(worker_ids))
            )
            for w in worker_result.scalars().all():
                workers_map[w.id] = w

    # Get pour plans for those jobs
    pour_plans = {}
    if job_ids:
        pp_result = await db.execute(
            select(PourPlan).where(PourPlan.quote_id.in_(job_ids))
        )
        for pp in pp_result.scalars().all():
            pour_plans[pp.quote_id] = pp

    # Build enriched job data
    run_sheet_jobs = []
    for job in jobs:
        customer = customers.get(job.customer_id)
        crew = assignments.get(job.id, [])
        crew_names = []
        for a in crew:
            w = workers_map.get(a.worker_id)
            if w:
                crew_names.append(w.name)

        pp = pour_plans.get(job.id)

        # Extract concrete info from calculator_input if available
        concrete_info = {}
        if job.calculator_input:
            ci = job.calculator_input
            concrete_info = {
                "area_m2": ci.get("area_m2") or ci.get("area"),
                "depth_mm": ci.get("depth_mm") or ci.get("depth"),
                "cubic_metres": ci.get("cubic_metres") or ci.get("volume"),
                "mix_type": ci.get("mix_type") or ci.get("concrete_grade"),
                "finish_type": ci.get("finish_type") or ci.get("finish"),
            }

        run_sheet_jobs.append({
            "job": job,
            "customer": customer,
            "crew_names": crew_names,
            "pour_plan": pp,
            "concrete_info": concrete_info,
        })

    return templates.TemplateResponse("schedule/run_sheet.html", {
        "request": request,
        "target_date": target_date,
        "today": today,
        "tomorrow": today + timedelta(days=1),
        "jobs": run_sheet_jobs,
        "total_jobs": len(jobs),
    })
