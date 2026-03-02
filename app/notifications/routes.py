"""
Notification routes — Admin endpoints for managing notifications and reminders.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.core.auth import require_login, verify_csrf
from app.core.templates import templates
from app.core.security import decrypt_customer_pii
from app.models import ActivityLog, Notification, Customer
from app.notifications.reminders import (
    process_due_reminders,
    get_pending_reminders,
    cancel_reminders,
)
from app.notifications.service import (
    get_all_notifications,
    get_unread_count,
    mark_as_read,
    mark_all_as_read,
    delete_notification,
    check_overdue_invoices,
    check_jobs_tomorrow,
    check_expiring_quotes,
    check_quote_followups,
    check_sealer_followups,
)

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


# =============================================================================
# NOTIFICATIONS PAGE
# =============================================================================

@router.get("", name="notifications:index")
async def notifications_page(
    request: Request,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Display all notifications."""
    page_size = 20
    offset = (page - 1) * page_size

    notifications, total = await get_all_notifications(db, limit=page_size, offset=offset)
    unread_count = await get_unread_count(db)
    pages = (total + page_size - 1) // page_size

    return templates.TemplateResponse("notifications/index.html", {
        "request": request,
        "notifications": notifications,
        "unread_count": unread_count,
        "page": page,
        "pages": pages,
        "total": total,
    })


@router.get("/{notification_id}", name="notifications:detail")
async def notification_detail(
    notification_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """View and mark notification as read, then redirect to relevant page."""
    notification = await db.get(Notification, notification_id)
    if not notification:
        raise HTTPException(404, "Notification not found")

    # Mark as read
    if not notification.is_read:
        notification.is_read = True
        await db.commit()

    # Redirect to relevant page based on notification type
    if notification.type == "inbound_sms":
        # Redirect to SMS inbox conversation for the customer's phone
        if notification.customer_id:
            customer = await db.get(Customer, notification.customer_id)
            if customer:
                decrypt_customer_pii(customer)
                if customer.phone:
                    # Normalize phone for SMS inbox URL (strip spaces/dashes)
                    phone = customer.phone.replace(" ", "").replace("-", "")
                    if phone.startswith("0"):
                        phone = "61" + phone[1:]
                    return RedirectResponse(url=f"/sms-inbox/conversation/{phone}", status_code=302)
        return RedirectResponse(url="/sms-inbox", status_code=302)
    elif notification.type == "sealer_followup" and notification.quote_id:
        return RedirectResponse(url=f"/quotes/{notification.quote_id}", status_code=302)
    elif notification.quote_id:
        return RedirectResponse(url=f"/quotes/{notification.quote_id}", status_code=302)
    elif notification.invoice_id:
        return RedirectResponse(url=f"/invoices/{notification.invoice_id}", status_code=302)
    elif notification.customer_id:
        return RedirectResponse(url=f"/customers/{notification.customer_id}", status_code=302)
    else:
        return RedirectResponse(url="/notifications", status_code=302)


@router.post("/api/mark-read/{notification_id}", name="notifications:mark_read")
async def api_mark_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Mark a notification as read."""
    success = await mark_as_read(db, notification_id)
    await db.commit()
    return {"success": success}


@router.post("/api/mark-all-read", name="notifications:mark_all_read")
async def api_mark_all_read(
    db: AsyncSession = Depends(get_db),
):
    """Mark all notifications as read."""
    count = await mark_all_as_read(db)
    await db.commit()
    return {"success": True, "count": count}


@router.delete("/api/{notification_id}", name="notifications:delete")
async def api_delete_notification(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a notification."""
    success = await delete_notification(db, notification_id)
    await db.commit()
    return {"success": success}


@router.get("/api/poll", name="notifications:poll")
async def api_poll_notifications(
    since: Optional[str] = Query(None, description="ISO timestamp to fetch notifications after"),
    db: AsyncSession = Depends(get_db),
):
    """
    Poll for new notifications since a given timestamp.
    Returns unread count and any new notifications for live Toast display.
    """
    from sqlalchemy import select
    from app.models import Notification

    unread_count = await get_unread_count(db)

    new_notifications = []
    if since:
        from datetime import datetime
        try:
            since_dt = datetime.fromisoformat(since)
        except (ValueError, TypeError):
            since_dt = None

        if since_dt:
            result = await db.execute(
                select(Notification)
                .where(Notification.created_at > since_dt)
                .order_by(Notification.created_at.asc())
                .limit(10)
            )
            for n in result.scalars().all():
                new_notifications.append({
                    "id": n.id,
                    "type": n.type,
                    "title": n.title,
                    "message": n.message,
                    "priority": n.priority,
                    "created_at": n.created_at.isoformat() if n.created_at else None,
                })

    return {
        "unread_count": unread_count,
        "notifications": new_notifications,
    }


@router.post("/api/check-overdue", name="notifications:check_overdue")
async def api_check_overdue(
    db: AsyncSession = Depends(get_db),
):
    """Check for overdue invoices and create notifications. Run daily via cron."""
    count = await check_overdue_invoices(db)
    await db.commit()
    return {"success": True, "notifications_created": count}


@router.post("/api/check-jobs-tomorrow", name="notifications:check_jobs_tomorrow")
async def api_check_jobs_tomorrow(
    db: AsyncSession = Depends(get_db),
):
    """Check for jobs scheduled tomorrow and create reminders. Run daily via cron."""
    count = await check_jobs_tomorrow(db)
    await db.commit()
    return {"success": True, "notifications_created": count}


@router.post("/api/check-expiring-quotes", name="notifications:check_expiring")
async def api_check_expiring_quotes(
    db: AsyncSession = Depends(get_db),
):
    """Check for expired quotes and create notifications. Run daily via cron."""
    count = await check_expiring_quotes(db)
    await db.commit()
    return {"success": True, "notifications_created": count}


@router.post("/api/check-quote-followups", name="notifications:check_quote_followups")
async def api_check_quote_followups(
    db: AsyncSession = Depends(get_db),
):
    """Check for quotes needing follow-up and create reminders. Run daily via cron."""
    count = await check_quote_followups(db)
    await db.commit()
    return {"success": True, "notifications_created": count}


@router.post("/api/check-sealer-followups", name="notifications:check_sealer_followups")
async def api_check_sealer_followups(
    db: AsyncSession = Depends(get_db),
):
    """Check for completed jobs needing sealer maintenance (~3 years). Run daily via cron."""
    count = await check_sealer_followups(db)
    await db.commit()
    return {"success": True, "notifications_created": count}


# =============================================================================
# REMINDER MANAGEMENT
# =============================================================================

@router.post("/api/process-reminders", name="notifications:process_reminders")
async def api_process_reminders(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Process all due reminders.

    This endpoint is designed to be called by a cron job.
    Finds all reminders with scheduled_for <= now and sends them.

    Returns:
        Count of reminders processed
    """
    processed = await process_due_reminders(db)

    if processed > 0:
        # Log activity
        activity = ActivityLog(
            action="reminders_processed",
            description=f"Processed {processed} due reminders",
            entity_type="system",
            ip_address=request.client.host if request.client else None,
            extra_data={"count": processed},
        )
        db.add(activity)
        await db.commit()

    return {"success": True, "processed": processed}


@router.get("/api/reminders/pending", name="notifications:pending_reminders")
async def api_pending_reminders(
    entity_type: str = Query(None, description="Filter by entity type (invoice, quote)"),
    entity_id: int = Query(None, description="Filter by entity ID"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get list of pending (unsent, uncancelled) reminders.

    Useful for debugging and seeing what reminders are scheduled.
    """
    reminders = await get_pending_reminders(db, entity_type, entity_id)

    return {
        "reminders": [
            {
                "id": r.id,
                "reminder_type": r.reminder_type,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "scheduled_for": r.scheduled_for.isoformat() if r.scheduled_for else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reminders
        ],
        "count": len(reminders),
    }


@router.post("/api/reminders/cancel", name="notifications:cancel_reminders")
async def api_cancel_reminders(
    entity_type: str = Query(..., description="Entity type (invoice, quote)"),
    entity_id: int = Query(..., description="Entity ID"),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Cancel all pending reminders for an entity.

    Useful when an invoice is voided or a job is cancelled.
    """
    cancelled = await cancel_reminders(db, entity_type, entity_id)

    if cancelled > 0:
        # Log activity
        activity = ActivityLog(
            action="reminders_cancelled",
            description=f"Cancelled {cancelled} reminders for {entity_type} {entity_id}",
            entity_type=entity_type,
            entity_id=entity_id,
            ip_address=request.client.host if request and request.client else None,
            extra_data={"count": cancelled},
        )
        db.add(activity)
        await db.commit()

    return {"success": True, "cancelled": cancelled}


# =============================================================================
# CUSTOM MESSAGE SENDING (Quick Message Panel)
# =============================================================================

class CustomMessageRequest(BaseModel):
    """Request body for sending a custom message."""
    customer_id: int
    message_type: str  # 'sms' or 'email'
    message: str
    subject: Optional[str] = None  # Required for email


@router.post("/api/send-custom", name="notifications:send_custom")
async def api_send_custom_message(
    data: CustomMessageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Send a custom SMS or email to a customer.

    This is for one-off messages from the quick message panel.
    """
    # Validate message type
    if data.message_type not in ("sms", "email"):
        raise HTTPException(400, "Invalid message type. Must be 'sms' or 'email'.")

    # Get customer
    customer = await db.get(Customer, data.customer_id)
    if not customer:
        raise HTTPException(404, "Customer not found")

    # Decrypt PII if encryption is enabled
    decrypt_customer_pii(customer)

    # Validate message content
    if not data.message or not data.message.strip():
        raise HTTPException(400, "Message cannot be empty")

    result = {"success": False, "error": None}

    if data.message_type == "sms":
        # Validate phone number
        if not customer.phone:
            return {"success": False, "error": "Customer has no phone number"}

        # Send SMS
        from app.notifications.sms import send_sms
        sms_result = await send_sms(
            db=db,
            to=customer.phone,
            message=data.message.strip(),
        )

        if sms_result.get("success"):
            result = {"success": True, "message_id": sms_result.get("message_id")}

            # Log activity
            activity = ActivityLog(
                action="custom_sms_sent",
                description=f"Sent custom SMS to {customer.name}",
                entity_type="customer",
                entity_id=customer.id,
                ip_address=request.client.host if request.client else None,
                extra_data={
                    "phone": customer.phone,
                    "message_preview": data.message[:50] + "..." if len(data.message) > 50 else data.message,
                },
            )
            db.add(activity)
            await db.commit()
        else:
            result = {"success": False, "error": sms_result.get("error", "Failed to send SMS")}

    else:  # email
        # Validate email
        if not customer.email:
            return {"success": False, "error": "Customer has no email address"}

        # Validate subject
        if not data.subject or not data.subject.strip():
            return {"success": False, "error": "Email subject is required"}

        # Send email
        from app.notifications.email import send_email
        from app.config import settings

        # Build simple HTML body
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <p>Hi {customer.name.split()[0] if customer.name else 'there'},</p>
            <p>{data.message.strip().replace(chr(10), '<br>')}</p>
            <br>
            <p>Thanks,<br>{settings.trading_as}</p>
            <p style="color: #666; font-size: 12px;">{settings.business_phone}</p>
        </body>
        </html>
        """

        email_sent = await send_email(
            to=customer.email,
            subject=data.subject.strip(),
            html_body=html_body,
            text_body=f"Hi {customer.name.split()[0] if customer.name else 'there'},\n\n{data.message.strip()}\n\nThanks,\n{settings.trading_as}\n{settings.business_phone}",
            db=db,
        )

        if email_sent:
            result = {"success": True}

            # Log activity
            activity = ActivityLog(
                action="custom_email_sent",
                description=f"Sent custom email to {customer.name}",
                entity_type="customer",
                entity_id=customer.id,
                ip_address=request.client.host if request.client else None,
                extra_data={
                    "email": customer.email,
                    "subject": data.subject,
                    "message_preview": data.message[:50] + "..." if len(data.message) > 50 else data.message,
                },
            )
            db.add(activity)
            await db.commit()
        else:
            result = {"success": False, "error": "Failed to send email. Check email configuration."}

    return result


# =============================================================================
# DAY BEFORE JOB REMINDERS (SMS)
# =============================================================================

@router.post("/api/send-day-before-reminders", name="notifications:send_day_before_reminders")
async def api_send_day_before_reminders(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Find all jobs scheduled for tomorrow and send day-before SMS reminders
    to customers who have SMS enabled.

    Designed to be called daily via cron job.

    Returns:
        Count of reminders sent successfully.
    """
    from sqlalchemy import select
    from datetime import timedelta
    from app.core.dates import sydney_now
    from app.models import Quote, Customer
    from app.notifications.sms import send_day_before_sms

    tomorrow = sydney_now().date() + timedelta(days=1)

    # Find all jobs scheduled for tomorrow
    result = await db.execute(
        select(Quote)
        .where(Quote.status.in_(["accepted", "confirmed", "pour_stage"]))
        .where(Quote.confirmed_start_date == tomorrow)
    )
    jobs = result.scalars().all()

    sent_count = 0
    errors = []

    for job in jobs:
        if not job.customer_id:
            continue

        customer = await db.get(Customer, job.customer_id)
        if not customer:
            continue

        sms_result = await send_day_before_sms(db, job, customer)

        if sms_result.get("success"):
            sent_count += 1
        else:
            error = sms_result.get("error", "Unknown error")
            # Don't log expected skips (no phone, SMS disabled)
            if error not in ("No phone number", "SMS notifications disabled"):
                errors.append(f"Job {job.id}: {error}")

    if sent_count > 0:
        # Log activity
        from app.models import ActivityLog
        activity = ActivityLog(
            action="day_before_reminders_sent",
            description=f"Sent {sent_count} day-before reminder SMS for {tomorrow.isoformat()}",
            entity_type="system",
            ip_address=request.client.host if request.client else None,
            extra_data={"count": sent_count, "date": tomorrow.isoformat(), "errors": errors},
        )
        db.add(activity)

    await db.commit()

    return {
        "success": True,
        "sent": sent_count,
        "total_jobs": len(jobs),
        "date": tomorrow.isoformat(),
    }
