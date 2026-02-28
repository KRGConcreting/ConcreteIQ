"""
SMS Inbox routes — Two-way SMS conversation view.

Shows inbound and outbound SMS grouped by customer phone number.
"""

import logging
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case, or_, and_, desc
from pydantic import BaseModel, Field

from app.database import get_db
from app.core.auth import require_login, verify_csrf
from app.core.templates import templates
from app.core.dates import sydney_now
from app.models import CommunicationLog, Customer

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


@router.get("", name="sms_inbox:index")
async def sms_inbox_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    SMS Inbox — shows conversation threads grouped by phone number.

    Each thread shows the latest message and unread count.
    """
    # Get all SMS conversations grouped by phone number (both inbound and outbound)
    # We need to get the "other party" phone for each message
    # For outbound: to_phone is the other party
    # For inbound: from_phone is the other party

    # Get distinct phone numbers that have SMS activity
    # Using a subquery approach for SQLite compatibility
    all_sms = await db.execute(
        select(CommunicationLog)
        .where(CommunicationLog.channel == "sms")
        .where(
            or_(
                CommunicationLog.to_phone != None,
                CommunicationLog.from_phone != None,
            )
        )
        .order_by(CommunicationLog.created_at.desc())
    )
    all_messages = all_sms.scalars().all()

    # Group by phone number to build conversation threads
    threads = {}
    for msg in all_messages:
        phone = msg.to_phone if msg.direction == "outbound" else msg.from_phone
        if not phone:
            continue

        if phone not in threads:
            threads[phone] = {
                "phone": phone,
                "latest_message": msg,
                "customer_id": msg.customer_id,
                "customer_name": None,
                "unread_count": 0,
                "last_activity": msg.created_at,
            }

        if msg.direction == "inbound" and not msg.read_at:
            threads[phone]["unread_count"] += 1

        # Use customer_id from any message
        if msg.customer_id and not threads[phone]["customer_id"]:
            threads[phone]["customer_id"] = msg.customer_id

    # Get customer names
    customer_ids = [t["customer_id"] for t in threads.values() if t["customer_id"]]
    if customer_ids:
        cust_result = await db.execute(
            select(Customer).where(Customer.id.in_(customer_ids))
        )
        customers_map = {c.id: c for c in cust_result.scalars().all()}
        for thread in threads.values():
            if thread["customer_id"] and thread["customer_id"] in customers_map:
                thread["customer_name"] = customers_map[thread["customer_id"]].name

    # Sort threads by last activity (most recent first)
    sorted_threads = sorted(threads.values(), key=lambda t: t["last_activity"], reverse=True)

    # Count total unread
    total_unread = sum(t["unread_count"] for t in sorted_threads)

    return templates.TemplateResponse("sms_inbox/index.html", {
        "request": request,
        "threads": sorted_threads,
        "total_unread": total_unread,
        "active": "sms_inbox",
    })


@router.get("/conversation/{phone}", name="sms_inbox:conversation")
async def sms_conversation(
    request: Request,
    phone: str,
    db: AsyncSession = Depends(get_db),
):
    """
    View SMS conversation with a specific phone number.

    Shows all messages (inbound + outbound) in chronological order.
    Marks inbound messages as read.
    """
    # Get all messages for this phone number
    result = await db.execute(
        select(CommunicationLog)
        .where(CommunicationLog.channel == "sms")
        .where(
            or_(
                CommunicationLog.to_phone == phone,
                CommunicationLog.from_phone == phone,
            )
        )
        .order_by(CommunicationLog.created_at.asc())
    )
    messages = result.scalars().all()

    if not messages:
        raise HTTPException(404, "No conversation found")

    # Mark unread inbound messages as read
    now = sydney_now()
    for msg in messages:
        if msg.direction == "inbound" and not msg.read_at:
            msg.read_at = now
    await db.commit()

    # Get customer info
    customer = None
    customer_id = None
    for msg in messages:
        if msg.customer_id:
            customer_id = msg.customer_id
            break

    if customer_id:
        customer = await db.get(Customer, customer_id)

    return templates.TemplateResponse("sms_inbox/conversation.html", {
        "request": request,
        "messages": messages,
        "phone": phone,
        "customer": customer,
        "active": "sms_inbox",
    })


class SMSReplyRequest(BaseModel):
    """Reply to an SMS conversation."""
    message: str = Field(..., min_length=1, max_length=500)


@router.post("/reply/{phone}", name="sms_inbox:reply")
async def sms_reply(
    request: Request,
    phone: str,
    data: SMSReplyRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Send a reply SMS to a phone number.

    Links to the customer if a conversation exists.
    """
    # Find customer for this phone
    customer_id = None
    quote_id = None

    result = await db.execute(
        select(CommunicationLog)
        .where(CommunicationLog.channel == "sms")
        .where(
            or_(
                CommunicationLog.to_phone == phone,
                CommunicationLog.from_phone == phone,
            )
        )
        .where(CommunicationLog.customer_id != None)
        .order_by(CommunicationLog.created_at.desc())
        .limit(1)
    )
    recent = result.scalar_one_or_none()
    if recent:
        customer_id = recent.customer_id
        quote_id = recent.quote_id

    # Send SMS
    from app.notifications.sms import send_sms
    result = await send_sms(
        db=db,
        to=phone,
        message=data.message,
        quote_id=quote_id,
        customer_id=customer_id,
    )

    await db.commit()
    return result


@router.get("/api/unread-count", name="sms_inbox:unread_count")
async def get_unread_count(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the count of unread inbound SMS messages."""
    result = await db.execute(
        select(func.count(CommunicationLog.id))
        .where(CommunicationLog.channel == "sms")
        .where(CommunicationLog.direction == "inbound")
        .where(CommunicationLog.read_at == None)
    )
    count = result.scalar() or 0
    return {"unread_count": count}
