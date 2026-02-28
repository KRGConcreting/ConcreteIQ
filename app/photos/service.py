"""
Photos service — Upload, retrieve, and delete job photos.

Uses S3-compatible storage (Cloudflare R2).
"""

import uuid
import mimetypes
from decimal import Decimal
from typing import Optional
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request, UploadFile

from app.models import Photo, Quote, ActivityLog
from app.config import settings
from app.core.dates import sydney_now


# Allowed file types
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


async def get_storage_client():
    """
    Get boto3 S3 client for R2.

    Returns None if storage is not configured.
    """
    if not settings.r2_access_key or not settings.r2_secret_key or not settings.r2_endpoint:
        return None

    try:
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint,
            aws_access_key_id=settings.r2_access_key,
            aws_secret_access_key=settings.r2_secret_key,
            config=Config(signature_version="s3v4"),
        )
    except ImportError:
        return None


def validate_file(file: UploadFile) -> tuple[bool, str]:
    """
    Validate uploaded file.

    Returns (is_valid, error_message).
    """
    if not file.filename:
        return False, "No filename provided"

    # Check extension
    ext = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"

    # Check MIME type
    content_type = file.content_type or mimetypes.guess_type(file.filename)[0]
    if content_type not in ALLOWED_MIME_TYPES:
        return False, f"Invalid content type: {content_type}"

    return True, ""


def generate_storage_key(quote_id: int, filename: str) -> str:
    """Generate unique storage key for a photo."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ".jpg"
    unique_id = uuid.uuid4().hex[:12]
    return f"photos/quote-{quote_id}/{unique_id}{ext}"


async def upload_photo(
    db: AsyncSession,
    quote_id: int,
    file: UploadFile,
    photo_type: str,
    caption: Optional[str],
    request: Request,
    gps_lat: Optional[Decimal] = None,
    gps_lng: Optional[Decimal] = None,
) -> Photo:
    """
    Upload a photo for a quote/job.

    Args:
        db: Database session
        quote_id: ID of the quote/job
        file: Uploaded file
        photo_type: Category (before, during, after, issue)
        caption: Optional caption
        request: HTTP request for logging

    Returns:
        Created Photo record

    Raises:
        ValueError: If validation fails or upload fails
    """
    # Validate quote exists
    quote = await db.get(Quote, quote_id)
    if not quote:
        raise ValueError("Quote not found")

    # Validate file
    is_valid, error = validate_file(file)
    if not is_valid:
        raise ValueError(error)

    # Read file content
    content = await file.read()
    file_size = len(content)

    if file_size > MAX_FILE_SIZE:
        raise ValueError(f"File too large. Maximum size: {MAX_FILE_SIZE // (1024*1024)}MB")

    if file_size == 0:
        raise ValueError("Empty file")

    # Generate storage key
    storage_key = generate_storage_key(quote_id, file.filename)

    # Upload to storage
    s3_client = await get_storage_client()
    if s3_client:
        try:
            content_type = file.content_type or "image/jpeg"
            s3_client.put_object(
                Bucket=settings.r2_bucket,
                Key=storage_key,
                Body=content,
                ContentType=content_type,
            )
        except Exception as e:
            raise ValueError(f"Failed to upload to storage: {str(e)}")

    # Build public URL
    if settings.r2_public_url:
        storage_url = f"{settings.r2_public_url.rstrip('/')}/{storage_key}"
    elif settings.r2_endpoint:
        storage_url = f"{settings.r2_endpoint.rstrip('/')}/{settings.r2_bucket}/{storage_key}"
    else:
        # Local fallback - store as data URL or placeholder
        storage_url = f"/photos/placeholder/{storage_key}"

    # Create photo record
    photo = Photo(
        quote_id=quote_id,
        category=photo_type,
        filename=storage_key.split("/")[-1],
        storage_url=storage_url,
        caption=caption,
        gps_lat=gps_lat,
        gps_lng=gps_lng,
        created_at=sydney_now(),
    )
    db.add(photo)
    await db.flush()

    # Log activity
    activity = ActivityLog(
        action="photo_uploaded",
        description=f"Photo uploaded for quote {quote.quote_number} ({photo_type})",
        entity_type="quote",
        entity_id=quote_id,
        ip_address=request.client.host if request.client else None,
        extra_data={
            "photo_id": photo.id,
            "filename": file.filename,
            "category": photo_type,
            "file_size": file_size,
            "gps_lat": float(gps_lat) if gps_lat else None,
            "gps_lng": float(gps_lng) if gps_lng else None,
        },
    )
    db.add(activity)

    return photo


async def get_photos(
    db: AsyncSession,
    quote_id: int,
    photo_type: Optional[str] = None,
) -> list[Photo]:
    """
    Get photos for a quote.

    Args:
        db: Database session
        quote_id: Quote ID
        photo_type: Optional filter by category

    Returns:
        List of Photo records
    """
    query = (
        select(Photo)
        .where(Photo.quote_id == quote_id)
        .order_by(Photo.created_at.desc())
    )

    if photo_type:
        query = query.where(Photo.category == photo_type)

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_photo(db: AsyncSession, photo_id: int) -> Optional[Photo]:
    """Get a single photo by ID."""
    return await db.get(Photo, photo_id)


async def delete_photo(
    db: AsyncSession,
    photo: Photo,
    request: Request,
) -> None:
    """
    Delete a photo from storage and database.

    Args:
        db: Database session
        photo: Photo to delete
        request: HTTP request for logging
    """
    quote = await db.get(Quote, photo.quote_id)
    quote_number = quote.quote_number if quote else f"#{photo.quote_id}"

    # Extract storage key from URL
    storage_key = None
    if settings.r2_public_url and photo.storage_url.startswith(settings.r2_public_url):
        storage_key = photo.storage_url.replace(settings.r2_public_url.rstrip("/") + "/", "")
    elif "photos/quote-" in photo.storage_url:
        # Extract key from URL
        parts = photo.storage_url.split("photos/quote-")
        if len(parts) > 1:
            storage_key = "photos/quote-" + parts[1]

    # Delete from storage
    if storage_key:
        s3_client = await get_storage_client()
        if s3_client:
            try:
                s3_client.delete_object(
                    Bucket=settings.r2_bucket,
                    Key=storage_key,
                )
            except Exception:
                pass  # Don't fail if storage delete fails

    # Log activity before deletion
    activity = ActivityLog(
        action="photo_deleted",
        description=f"Photo deleted from quote {quote_number}",
        entity_type="quote",
        entity_id=photo.quote_id,
        ip_address=request.client.host if request.client else None,
        extra_data={
            "photo_id": photo.id,
            "category": photo.category,
            "filename": photo.filename,
        },
    )
    db.add(activity)

    # Delete record
    await db.delete(photo)
