"""
Customer routes — CRUD operations.

This establishes the pattern for all resource routes.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Customer, Quote, Invoice, ActivityLog, CommunicationLog, Payment, Notification, ProgressUpdate
from app.schemas import (
    CustomerCreate, CustomerUpdate, CustomerResponse,
    PaginatedResponse, SuccessResponse
)
from app.core.auth import require_login, verify_csrf
from app.core.templates import templates
from app.core.dates import sydney_now
from app.core.security import encrypt_customer_pii, decrypt_customer_pii, is_encryption_active, hash_value

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


# =============================================================================
# HTML PAGES
# =============================================================================

@router.get("", name="customers:list")
async def customer_list_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
):
    """Customer list page."""
    page_size = 20
    offset = (page - 1) * page_size
    
    # Base query
    query = select(Customer).order_by(Customer.name)
    count_query = select(func.count(Customer.id))
    
    # Search filter
    if q:
        search = f"%{q}%"
        if is_encryption_active():
            # Encrypted: search name by ILIKE, email/phone by hash match
            search_hash = hash_value(q)
            query = query.where(
                (Customer.name.ilike(search)) |
                (Customer.email_hash == search_hash) |
                (Customer.phone_hash == search_hash)
            )
            count_query = count_query.where(
                (Customer.name.ilike(search)) |
                (Customer.email_hash == search_hash) |
                (Customer.phone_hash == search_hash)
            )
        else:
            query = query.where(
                (Customer.name.ilike(search)) |
                (Customer.email.ilike(search)) |
                (Customer.phone.ilike(search))
            )
            count_query = count_query.where(
                (Customer.name.ilike(search)) |
                (Customer.email.ilike(search)) |
                (Customer.phone.ilike(search))
            )

    # Execute
    total = (await db.execute(count_query)).scalar()
    result = await db.execute(query.offset(offset).limit(page_size))
    customers = result.scalars().all()

    # Decrypt PII for display
    for c in customers:
        decrypt_customer_pii(c)
    
    return templates.TemplateResponse("customers/list.html", {
        "request": request,
        "customers": customers,
        "search": q,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
    })


@router.get("/new", name="customers:new")
async def customer_new_page(request: Request):
    """New customer form."""
    return templates.TemplateResponse("customers/form.html", {
        "request": request,
        "customer": None,
        "is_new": True,
    })


@router.get("/{id}", name="customers:detail")
async def customer_detail_page(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Customer detail page."""
    from sqlalchemy import or_

    customer = await db.get(Customer, id)
    if not customer:
        raise HTTPException(404, "Customer not found")
    decrypt_customer_pii(customer)

    # Get quotes
    quotes_result = await db.execute(
        select(Quote)
        .where(Quote.customer_id == id)
        .order_by(Quote.created_at.desc())
        .limit(10)
    )
    quotes = quotes_result.scalars().all()
    quote_ids = [q.id for q in quotes]

    # Get all quotes for activity (not just limited to 10)
    all_quotes_result = await db.execute(
        select(Quote).where(Quote.customer_id == id)
    )
    all_quotes = all_quotes_result.scalars().all()
    all_quote_ids = [q.id for q in all_quotes]
    quote_map = {q.id: q for q in all_quotes}

    # Get invoices
    invoices_result = await db.execute(
        select(Invoice)
        .where(Invoice.customer_id == id)
        .order_by(Invoice.created_at.desc())
        .limit(10)
    )
    invoices = invoices_result.scalars().all()

    # Get all invoices for activity
    all_invoices_result = await db.execute(
        select(Invoice).where(Invoice.customer_id == id)
    )
    all_invoices = all_invoices_result.scalars().all()
    all_invoice_ids = [i.id for i in all_invoices]
    invoice_map = {i.id: i for i in all_invoices}

    # Build unified activity timeline
    timeline = []

    # 1. ActivityLog entries for customer, quotes, and invoices
    activity_conditions = [
        (ActivityLog.entity_type == "customer") & (ActivityLog.entity_id == id)
    ]
    if all_quote_ids:
        activity_conditions.append(
            (ActivityLog.entity_type == "quote") & (ActivityLog.entity_id.in_(all_quote_ids))
        )
    if all_invoice_ids:
        activity_conditions.append(
            (ActivityLog.entity_type == "invoice") & (ActivityLog.entity_id.in_(all_invoice_ids))
        )

    activity_result = await db.execute(
        select(ActivityLog)
        .where(or_(*activity_conditions))
        .order_by(ActivityLog.created_at.desc())
        .limit(100)
    )
    activities = activity_result.scalars().all()

    for log in activities:
        item = {
            "type": "activity",
            "created_at": log.created_at,
            "description": log.description,
            "action": log.action,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
        }

        # Determine icon and color based on action
        if log.entity_type == "customer":
            if "created" in log.action:
                item.update({"icon": "user-plus", "color": "green"})
            elif "updated" in log.action:
                item.update({"icon": "edit", "color": "blue"})
            elif "deleted" in log.action:
                item.update({"icon": "trash", "color": "red"})
            else:
                item.update({"icon": "user", "color": "gray"})
        elif log.entity_type == "quote":
            quote = quote_map.get(log.entity_id)
            quote_num = quote.quote_number if quote else f"Q-{log.entity_id}"
            item["link"] = f"/quotes/{log.entity_id}"
            item["link_text"] = quote_num
            if "created" in log.action:
                item.update({"icon": "document-plus", "color": "green"})
            elif "sent" in log.action:
                item.update({"icon": "paper-airplane", "color": "blue"})
            elif "viewed" in log.action:
                item.update({"icon": "eye", "color": "purple"})
            elif "accepted" in log.action:
                item.update({"icon": "check-circle", "color": "green"})
            elif "declined" in log.action:
                item.update({"icon": "x-circle", "color": "red"})
            elif "updated" in log.action:
                item.update({"icon": "pencil", "color": "blue"})
            else:
                item.update({"icon": "document", "color": "gray"})
        elif log.entity_type == "invoice":
            invoice = invoice_map.get(log.entity_id)
            inv_num = invoice.invoice_number if invoice else f"INV-{log.entity_id}"
            item["link"] = f"/invoices/{log.entity_id}"
            item["link_text"] = inv_num
            if "created" in log.action:
                item.update({"icon": "document-plus", "color": "green"})
            elif "sent" in log.action:
                item.update({"icon": "paper-airplane", "color": "blue"})
            elif "paid" in log.action:
                item.update({"icon": "currency-dollar", "color": "green"})
            elif "updated" in log.action:
                item.update({"icon": "pencil", "color": "blue"})
            else:
                item.update({"icon": "document-text", "color": "gray"})
        else:
            item.update({"icon": "information-circle", "color": "gray"})

        timeline.append(item)

    # 2. CommunicationLog entries (email, SMS, phone calls, notes)
    comm_conditions = [CommunicationLog.customer_id == id]
    if all_quote_ids:
        comm_conditions.append(CommunicationLog.quote_id.in_(all_quote_ids))
    if all_invoice_ids:
        comm_conditions.append(CommunicationLog.invoice_id.in_(all_invoice_ids))

    comm_result = await db.execute(
        select(CommunicationLog)
        .where(or_(*comm_conditions))
        .order_by(CommunicationLog.created_at.desc())
        .limit(50)
    )
    comms = comm_result.scalars().all()

    for comm in comms:
        # Build reference link
        ref = ""
        link = None
        if comm.quote_id:
            quote = quote_map.get(comm.quote_id)
            ref = quote.quote_number if quote else f"Q-{comm.quote_id}"
            link = f"/quotes/{comm.quote_id}"
        elif comm.invoice_id:
            invoice = invoice_map.get(comm.invoice_id)
            ref = invoice.invoice_number if invoice else f"INV-{comm.invoice_id}"
            link = f"/invoices/{comm.invoice_id}"

        # Channel-specific icon and description
        icon_map = {
            "email": "envelope",
            "sms": "chat-bubble-left",
            "phone_call": "phone",
            "note": "document-text",
        }
        icon = icon_map.get(comm.channel, "chat-bubble-left")

        desc = comm.subject or (comm.body[:80] + "..." if comm.body and len(comm.body) > 80 else comm.body) or f"{comm.channel.replace('_', ' ').title()} sent"
        if ref:
            desc = f"{desc} for {ref}"

        timeline.append({
            "type": comm.channel,
            "created_at": comm.created_at,
            "description": desc,
            "icon": icon,
            "color": "blue",
            "link": link,
            "link_text": ref if ref else None,
            "to_address": comm.to_address,
            "to_phone": comm.to_phone,
        })

    # 3. Payment records for invoices
    if all_invoice_ids:
        payment_result = await db.execute(
            select(Payment)
            .where(Payment.invoice_id.in_(all_invoice_ids))
            .order_by(Payment.created_at.desc())
            .limit(50)
        )
        payments = payment_result.scalars().all()

        for payment in payments:
            invoice = invoice_map.get(payment.invoice_id)
            inv_num = invoice.invoice_number if invoice else f"INV-{payment.invoice_id}"
            amount = payment.amount_cents / 100
            method = payment.method or "payment"

            timeline.append({
                "type": "payment",
                "created_at": payment.created_at,
                "description": f"Payment ${amount:,.2f} received for {inv_num}",
                "icon": "currency-dollar",
                "color": "green",
                "link": f"/invoices/{payment.invoice_id}",
                "link_text": inv_num,
                "amount_cents": payment.amount_cents,
                "method": method,
            })

    # Sort all timeline items by created_at descending and limit to 50
    timeline.sort(key=lambda x: x["created_at"] if x["created_at"] else sydney_now(), reverse=True)
    timeline = timeline[:50]

    return templates.TemplateResponse("customers/detail.html", {
        "request": request,
        "customer": customer,
        "quotes": quotes,
        "invoices": invoices,
        "timeline": timeline,
    })


@router.get("/{id}/edit", name="customers:edit")
async def customer_edit_page(
    request: Request,
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Edit customer form."""
    customer = await db.get(Customer, id)
    if not customer:
        raise HTTPException(404, "Customer not found")
    decrypt_customer_pii(customer)

    return templates.TemplateResponse("customers/form.html", {
        "request": request,
        "customer": customer,
        "is_new": False,
    })


# =============================================================================
# API ENDPOINTS
# =============================================================================

@router.get("/api/list")
async def api_customer_list(
    db: AsyncSession = Depends(get_db),
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PaginatedResponse:
    """API: List customers with pagination."""
    offset = (page - 1) * page_size
    
    query = select(Customer).order_by(Customer.name)
    count_query = select(func.count(Customer.id))
    
    if q:
        search = f"%{q}%"
        if is_encryption_active():
            search_hash = hash_value(q)
            query = query.where(
                (Customer.name.ilike(search)) |
                (Customer.email_hash == search_hash) |
                (Customer.phone_hash == search_hash)
            )
            count_query = count_query.where(
                (Customer.name.ilike(search)) |
                (Customer.email_hash == search_hash) |
                (Customer.phone_hash == search_hash)
            )
        else:
            query = query.where(
                (Customer.name.ilike(search)) |
                (Customer.email.ilike(search)) |
                (Customer.phone.ilike(search))
            )
            count_query = count_query.where(
                (Customer.name.ilike(search)) |
                (Customer.email.ilike(search)) |
                (Customer.phone.ilike(search))
            )

    total = (await db.execute(count_query)).scalar()
    result = await db.execute(query.offset(offset).limit(page_size))
    customers = result.scalars().all()

    for c in customers:
        decrypt_customer_pii(c)

    return PaginatedResponse(
        items=[CustomerResponse.model_validate(c) for c in customers],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size,
    )


@router.post("/api/create")
async def api_customer_create(
    data: CustomerCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CustomerResponse:
    """API: Create a customer."""
    customer = Customer(**data.model_dump())
    encrypt_customer_pii(customer)
    db.add(customer)
    await db.flush()
    
    # Log activity
    activity = ActivityLog(
        action="customer_created",
        description=f"Created customer: {customer.name}",
        entity_type="customer",
        entity_id=customer.id,
        ip_address=request.client.host if request.client else None,
    )
    db.add(activity)
    
    await db.commit()
    await db.refresh(customer)
    
    return CustomerResponse.model_validate(customer)


@router.get("/api/{id}")
async def api_customer_get(
    id: int,
    db: AsyncSession = Depends(get_db),
) -> CustomerResponse:
    """API: Get a single customer."""
    customer = await db.get(Customer, id)
    if not customer:
        raise HTTPException(404, "Customer not found")
    decrypt_customer_pii(customer)

    return CustomerResponse.model_validate(customer)


@router.put("/api/{id}")
async def api_customer_update(
    id: int,
    data: CustomerUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CustomerResponse:
    """API: Update a customer."""
    customer = await db.get(Customer, id)
    if not customer:
        raise HTTPException(404, "Customer not found")

    # Update only provided fields
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(customer, key, value)
    # Re-encrypt PII if any contact fields changed
    encrypt_customer_pii(customer)

    # Log activity
    activity = ActivityLog(
        action="customer_updated",
        description=f"Updated customer: {customer.name}",
        entity_type="customer",
        entity_id=customer.id,
        ip_address=request.client.host if request.client else None,
        extra_data={"changes": list(update_data.keys())},
    )
    db.add(activity)
    
    await db.commit()
    await db.refresh(customer)
    
    return CustomerResponse.model_validate(customer)


@router.delete("/api/{id}")
async def api_customer_delete(
    id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    """API: Delete a customer (soft delete or hard delete if no quotes)."""
    customer = await db.get(Customer, id)
    if not customer:
        raise HTTPException(404, "Customer not found")
    
    # Check for quotes
    quote_count = (await db.execute(
        select(func.count(Quote.id)).where(Quote.customer_id == id)
    )).scalar()
    
    if quote_count > 0:
        raise HTTPException(400, "Cannot delete customer with existing quotes")

    # Clean up related records that reference this customer (FK safety)
    from sqlalchemy import delete as sql_delete
    await db.execute(sql_delete(Notification).where(Notification.customer_id == id))
    await db.execute(sql_delete(CommunicationLog).where(CommunicationLog.customer_id == id))
    await db.execute(sql_delete(ProgressUpdate).where(ProgressUpdate.customer_id == id))

    # Log before delete
    activity = ActivityLog(
        action="customer_deleted",
        description=f"Deleted customer: {customer.name}",
        entity_type="customer",
        entity_id=customer.id,
        ip_address=request.client.host if request.client else None,
    )
    db.add(activity)

    await db.delete(customer)
    await db.commit()
    
    return SuccessResponse(message="Customer deleted")


@router.get("/api/{id}/activity")
async def api_customer_activity(
    id: int,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
):
    """API: Get customer activity log."""
    result = await db.execute(
        select(ActivityLog)
        .where(ActivityLog.entity_type == "customer")
        .where(ActivityLog.entity_id == id)
        .order_by(ActivityLog.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()
