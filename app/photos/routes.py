"""
Photos routes — Upload and manage job site photos.
"""

from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.auth import require_login, verify_csrf
from app.photos import service


router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


# =============================================================================
# API ENDPOINTS
# =============================================================================

@router.post("/api/upload")
async def api_upload_photo(
    request: Request,
    quote_id: int = Form(...),
    photo_type: str = Form(default="general"),
    caption: Optional[str] = Form(default=None),
    gps_lat: Optional[float] = Form(default=None),
    gps_lng: Optional[float] = Form(default=None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Upload a photo for a quote/job.

    Accepts multipart form data with:
    - quote_id: The quote ID
    - photo_type: Category (before, during, after, issue, general)
    - caption: Optional caption
    - file: Image file (JPG/PNG, max 10MB)
    """
    # Validate photo_type
    valid_types = {"before", "during", "after", "issue", "general"}
    if photo_type not in valid_types:
        raise HTTPException(400, f"Invalid photo_type. Must be one of: {', '.join(valid_types)}")

    try:
        photo = await service.upload_photo(
            db=db,
            quote_id=quote_id,
            file=file,
            photo_type=photo_type,
            caption=caption,
            request=request,
            gps_lat=Decimal(str(gps_lat)) if gps_lat is not None else None,
            gps_lng=Decimal(str(gps_lng)) if gps_lng is not None else None,
        )
        await db.commit()
        await db.refresh(photo)

        return {
            "success": True,
            "photo": {
                "id": photo.id,
                "quote_id": photo.quote_id,
                "category": photo.category,
                "filename": photo.filename,
                "url": photo.storage_url,
                "caption": photo.caption,
                "gps_lat": float(photo.gps_lat) if photo.gps_lat else None,
                "gps_lng": float(photo.gps_lng) if photo.gps_lng else None,
                "created_at": photo.created_at.isoformat() if photo.created_at else None,
            },
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/quote/{quote_id}")
async def api_list_photos(
    quote_id: int,
    photo_type: Optional[str] = Query(None, description="Filter by category"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    List photos for a quote.

    Optionally filter by photo_type (before, during, after, issue, general).
    """
    photos = await service.get_photos(db, quote_id, photo_type)

    return {
        "success": True,
        "quote_id": quote_id,
        "count": len(photos),
        "photos": [
            {
                "id": p.id,
                "quote_id": p.quote_id,
                "category": p.category,
                "filename": p.filename,
                "url": p.storage_url,
                "thumbnail_url": p.thumbnail_url,
                "caption": p.caption,
                "gps_lat": float(p.gps_lat) if p.gps_lat else None,
                "gps_lng": float(p.gps_lng) if p.gps_lng else None,
                "taken_at": p.taken_at.isoformat() if p.taken_at else None,
                "shared_with_customer": p.shared_with_customer,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in photos
        ],
    }


@router.get("/api/{photo_id}")
async def api_get_photo(
    photo_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get a single photo by ID."""
    photo = await service.get_photo(db, photo_id)
    if not photo:
        raise HTTPException(404, "Photo not found")

    return {
        "success": True,
        "photo": {
            "id": photo.id,
            "quote_id": photo.quote_id,
            "category": photo.category,
            "filename": photo.filename,
            "url": photo.storage_url,
            "thumbnail_url": photo.thumbnail_url,
            "caption": photo.caption,
            "gps_lat": float(photo.gps_lat) if photo.gps_lat else None,
            "gps_lng": float(photo.gps_lng) if photo.gps_lng else None,
            "taken_at": photo.taken_at.isoformat() if photo.taken_at else None,
            "shared_with_customer": photo.shared_with_customer,
            "created_at": photo.created_at.isoformat() if photo.created_at else None,
        },
    }


@router.delete("/api/{photo_id}")
async def api_delete_photo(
    request: Request,
    photo_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete a photo."""
    photo = await service.get_photo(db, photo_id)
    if not photo:
        raise HTTPException(404, "Photo not found")

    await service.delete_photo(db, photo, request)
    await db.commit()

    return {
        "success": True,
        "message": "Photo deleted",
    }


@router.patch("/api/{photo_id}")
async def api_update_photo(
    request: Request,
    photo_id: int,
    caption: Optional[str] = None,
    shared_with_customer: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update photo metadata (caption, sharing)."""
    photo = await service.get_photo(db, photo_id)
    if not photo:
        raise HTTPException(404, "Photo not found")

    if caption is not None:
        photo.caption = caption

    if shared_with_customer is not None:
        photo.shared_with_customer = shared_with_customer

    await db.commit()
    await db.refresh(photo)

    return {
        "success": True,
        "photo": {
            "id": photo.id,
            "caption": photo.caption,
            "shared_with_customer": photo.shared_with_customer,
        },
    }
