"""
Document management routes — Upload, download, delete, and generate documents.

All routes are mounted under /documents in main.py.
The settings documents page at /settings/documents renders the UI;
these routes provide the API endpoints the frontend calls.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.auth import require_login, verify_csrf
from app.documents import service as doc_service
from app.settings import service as settings_service

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


# =============================================================================
# DOCUMENT LIST (JSON API)
# =============================================================================

@router.get("/api/list", name="documents:api:list")
async def api_list_documents(
    category: str = None,
):
    """
    List all documents, optionally filtered by category.

    Returns JSON with the document list.
    """
    docs = doc_service.list_documents(category)
    return {
        "success": True,
        "count": len(docs),
        "documents": docs,
    }


# =============================================================================
# UPLOAD
# =============================================================================

@router.post("/upload", name="documents:upload")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    category: str = Form(default="other"),
):
    """
    Upload a document to the library.

    Accepts multipart form data with:
    - file: The document file (PDF, DOCX, XLSX, JPG, PNG, etc.)
    - category: One of tcs, insurance, datasheets, swms, other
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    content = await file.read()

    try:
        download_url = doc_service.save_document(content, file.filename, category)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(f"Document uploaded: {file.filename} -> category={category}")

    return {
        "success": True,
        "message": f"'{file.filename}' uploaded successfully",
        "download_url": download_url,
    }


# =============================================================================
# DELETE
# =============================================================================

@router.delete("/{category}/{filename}", name="documents:delete")
async def delete_document(
    category: str,
    filename: str,
):
    """Delete a document from the library."""
    deleted = doc_service.delete_document(category, filename)

    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")

    logger.info(f"Document deleted: {category}/{filename}")

    return {
        "success": True,
        "message": f"'{filename}' deleted",
    }


# =============================================================================
# DOWNLOAD
# =============================================================================

@router.get("/download/{category}/{filename}", name="documents:download")
async def download_document(
    category: str,
    filename: str,
):
    """Download or view a document."""
    path = doc_service.get_document_path(category, filename)

    if path is None:
        raise HTTPException(status_code=404, detail="Document not found")

    return FileResponse(
        path=str(path),
        filename=filename,
        media_type="application/octet-stream",
    )


# =============================================================================
# T&C PDF GENERATOR
# =============================================================================

@router.post("/generate-tc-pdf", name="documents:generate_tc_pdf")
async def generate_tc_pdf(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a Terms & Conditions PDF from text input.

    Expects JSON body:
    {
        "text_content": "...",        // Required — the T&C text
        "save_as_active": true/false  // Optional — also set as the active T&C PDF
    }

    Returns the PDF as a downloadable response.
    If save_as_active is true, also saves to static/ and updates the
    quotation.terms_pdf_path setting.
    """
    data = await request.json()
    text_content = data.get("text_content", "").strip()

    if not text_content:
        raise HTTPException(status_code=400, detail="No text content provided")

    save_as_active = data.get("save_as_active", False)

    # Get business details for the PDF header
    business = await settings_service.get_settings_by_category(db, "business")
    business_name = business.get("trading_as") or business.get("name") or "KRG Concreting"
    business_abn = business.get("abn", "")
    business_phone = business.get("phone", "")
    business_email = business.get("email", "")

    try:
        pdf_bytes = doc_service.generate_tc_pdf(
            text_content=text_content,
            business_name=business_name,
            business_abn=business_abn,
            business_phone=business_phone,
            business_email=business_email,
        )
    except Exception as e:
        logger.error(f"PDF generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(e)}")

    # Optionally save as the active T&C PDF
    saved_path = None
    if save_as_active:
        from pathlib import Path

        static_dir = Path(__file__).resolve().parent.parent.parent / "static"
        pdf_filename = "KRG_Terms_and_Conditions.pdf"
        pdf_path = static_dir / pdf_filename

        # Backup existing
        if pdf_path.exists():
            import shutil
            backup_path = static_dir / "KRG_Terms_and_Conditions_backup.pdf"
            shutil.copy(pdf_path, backup_path)

        pdf_path.write_bytes(pdf_bytes)

        # Update the setting
        saved_path = f"/static/{pdf_filename}"
        await settings_service.set_setting(db, "quotation", "terms_pdf_path", saved_path)
        await db.commit()

        logger.info(f"Generated T&C PDF saved as active: {saved_path}")

        # Also save a copy in the documents library
        doc_service.save_document(pdf_bytes, "KRG_Terms_and_Conditions.pdf", "tcs")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="Terms_and_Conditions.pdf"',
            "X-TC-Saved-Path": saved_path or "",
        },
    )


@router.post("/generate-tc-pdf/preview", name="documents:generate_tc_pdf_preview")
async def preview_tc_pdf(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Preview a T&C PDF without saving.

    Same as generate, but never saves. Returns PDF inline for browser preview.
    """
    data = await request.json()
    text_content = data.get("text_content", "").strip()

    if not text_content:
        raise HTTPException(status_code=400, detail="No text content provided")

    business = await settings_service.get_settings_by_category(db, "business")
    business_name = business.get("trading_as") or business.get("name") or "KRG Concreting"
    business_abn = business.get("abn", "")
    business_phone = business.get("phone", "")
    business_email = business.get("email", "")

    try:
        pdf_bytes = doc_service.generate_tc_pdf(
            text_content=text_content,
            business_name=business_name,
            business_abn=business_abn,
            business_phone=business_phone,
            business_email=business_email,
        )
    except Exception as e:
        logger.error(f"PDF preview generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(e)}")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="TC_Preview.pdf"',
        },
    )
