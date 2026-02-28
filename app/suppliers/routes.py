"""
Supplier Contact Book — CRUD routes.

Manages contacts for concrete plants, pump companies, steel suppliers,
subcontractors, equipment hire, etc.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query, Form
from fastapi.responses import RedirectResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.auth import require_login, verify_csrf
from app.core.templates import templates
from app.models import Supplier

SUPPLIER_CATEGORIES = [
    ("concrete_plant", "Concrete Plant"),
    ("pump_hire", "Pump Hire"),
    ("steel_supplier", "Steel / Mesh Supplier"),
    ("formwork_supplier", "Formwork Supplier"),
    ("equipment_hire", "Equipment Hire"),
    ("subcontractor", "Subcontractor"),
    ("tool_shop", "Tool Shop"),
    ("waste_disposal", "Waste / Tip"),
    ("ppe_safety", "PPE & Safety"),
    ("insurance", "Insurance"),
    ("accounting", "Accountant / BAS Agent"),
    ("other", "Other"),
]

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


# =============================================================================
# HTML PAGES
# =============================================================================

@router.get("", name="suppliers:list")
async def supplier_list_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    category: Optional[str] = None,
    q: Optional[str] = None,
):
    """Supplier contact book list."""
    query = select(Supplier).where(Supplier.is_active == True)

    if category:
        query = query.where(Supplier.category == category)

    if q:
        search = f"%{q}%"
        query = query.where(
            Supplier.name.ilike(search) |
            Supplier.contact_person.ilike(search) |
            Supplier.abn.ilike(search)
        )

    query = query.order_by(Supplier.name.asc())
    result = await db.execute(query)
    suppliers = result.scalars().all()

    # Count by category
    cat_counts_result = await db.execute(
        select(Supplier.category, func.count(Supplier.id))
        .where(Supplier.is_active == True)
        .group_by(Supplier.category)
    )
    cat_counts = {row[0]: row[1] for row in cat_counts_result.all()}

    return templates.TemplateResponse("suppliers/index.html", {
        "request": request,
        "suppliers": suppliers,
        "categories": SUPPLIER_CATEGORIES,
        "categories_dict": dict(SUPPLIER_CATEGORIES),
        "cat_counts": cat_counts,
        "selected_category": category,
        "search_query": q or "",
        "total_count": sum(cat_counts.values()),
    })


@router.get("/new", name="suppliers:new")
async def supplier_new_page(request: Request):
    """New supplier form."""
    return templates.TemplateResponse("suppliers/form.html", {
        "request": request,
        "supplier": None,
        "categories": SUPPLIER_CATEGORIES,
        "is_edit": False,
    })


@router.get("/{supplier_id}", name="suppliers:detail")
async def supplier_detail_page(
    request: Request,
    supplier_id: int,
    db: AsyncSession = Depends(get_db),
):
    """View supplier details."""
    result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    supplier = result.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    return templates.TemplateResponse("suppliers/detail.html", {
        "request": request,
        "supplier": supplier,
        "categories_dict": dict(SUPPLIER_CATEGORIES),
    })


@router.get("/{supplier_id}/edit", name="suppliers:edit")
async def supplier_edit_page(
    request: Request,
    supplier_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Edit supplier form."""
    result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    supplier = result.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    return templates.TemplateResponse("suppliers/form.html", {
        "request": request,
        "supplier": supplier,
        "categories": SUPPLIER_CATEGORIES,
        "is_edit": True,
    })


# =============================================================================
# FORM HANDLERS
# =============================================================================

@router.post("", name="suppliers:create")
async def create_supplier(
    request: Request,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    category: str = Form("other"),
    contact_person: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    website: Optional[str] = Form(None),
    address: Optional[str] = Form(None),
    abn: Optional[str] = Form(None),
    account_number: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
):
    """Create a new supplier."""
    supplier = Supplier(
        name=name.strip(),
        category=category,
        contact_person=contact_person.strip() if contact_person else None,
        phone=phone.strip() if phone else None,
        email=email.strip() if email else None,
        website=website.strip() if website else None,
        address=address.strip() if address else None,
        abn=abn.strip() if abn else None,
        account_number=account_number.strip() if account_number else None,
        notes=notes.strip() if notes else None,
    )
    db.add(supplier)
    await db.commit()
    await db.refresh(supplier)

    return RedirectResponse(url=f"/suppliers/{supplier.id}", status_code=303)


@router.post("/{supplier_id}", name="suppliers:update")
async def update_supplier(
    request: Request,
    supplier_id: int,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    category: str = Form("other"),
    contact_person: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    website: Optional[str] = Form(None),
    address: Optional[str] = Form(None),
    abn: Optional[str] = Form(None),
    account_number: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
):
    """Update an existing supplier."""
    result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    supplier = result.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    supplier.name = name.strip()
    supplier.category = category
    supplier.contact_person = contact_person.strip() if contact_person else None
    supplier.phone = phone.strip() if phone else None
    supplier.email = email.strip() if email else None
    supplier.website = website.strip() if website else None
    supplier.address = address.strip() if address else None
    supplier.abn = abn.strip() if abn else None
    supplier.account_number = account_number.strip() if account_number else None
    supplier.notes = notes.strip() if notes else None

    await db.commit()
    return RedirectResponse(url=f"/suppliers/{supplier.id}", status_code=303)


@router.post("/{supplier_id}/delete", name="suppliers:delete")
async def delete_supplier(
    request: Request,
    supplier_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a supplier (mark inactive)."""
    result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    supplier = result.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    supplier.is_active = False
    await db.commit()

    return RedirectResponse(url="/suppliers", status_code=303)
