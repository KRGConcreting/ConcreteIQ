"""
Document service — T&C PDF generation and document management.

Handles:
- Listing documents from static/documents/ by category
- Saving uploaded documents to categorised subdirectories
- Deleting documents
- Generating professional Terms & Conditions PDFs via reportlab
"""

import io
import os
import re
import platform
from pathlib import Path
from datetime import datetime
from typing import Optional

from app.core.dates import sydney_now

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = BASE_DIR / "static"
DOCUMENTS_DIR = STATIC_DIR / "documents"

# Valid categories and their directory names
CATEGORIES = {
    "tcs": "T&Cs",
    "insurance": "Insurance",
    "datasheets": "Datasheets",
    "swms": "SWMS Templates",
    "portfolio": "Portfolio",
    "other": "Other",
}

# Image extensions (subset used by portfolio gallery)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# Allowed upload extensions
ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".txt", ".csv",
}

MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20MB


def _ensure_dirs():
    """Ensure all category directories exist."""
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    for cat_key in CATEGORIES:
        (DOCUMENTS_DIR / cat_key).mkdir(exist_ok=True)


def _safe_filename(filename: str) -> str:
    """Sanitise a filename — keep alphanumeric, hyphens, underscores, dots."""
    # Remove path separators
    filename = filename.replace("\\", "/").split("/")[-1]
    # Keep only safe characters
    name, ext = os.path.splitext(filename)
    name = re.sub(r"[^\w\-]", "_", name)
    # Truncate very long names
    if len(name) > 100:
        name = name[:100]
    return f"{name}{ext.lower()}"


def list_documents(category: Optional[str] = None) -> list[dict]:
    """
    List files in static/documents/ directory.

    Args:
        category: Optional category key to filter (tcs, insurance, datasheets, swms, other).
                  If None, lists all categories.

    Returns:
        List of dicts with: filename, size_mb, category, category_label,
        uploaded_at, extension, download_url.
    """
    _ensure_dirs()
    results = []

    categories_to_scan = {category: CATEGORIES[category]} if category and category in CATEGORIES else CATEGORIES

    for cat_key, cat_label in categories_to_scan.items():
        cat_dir = DOCUMENTS_DIR / cat_key
        if not cat_dir.exists():
            continue

        for f in sorted(cat_dir.iterdir()):
            if not f.is_file():
                continue
            # Skip hidden/temp files
            if f.name.startswith(".") or f.name.startswith("~"):
                continue

            stat = f.stat()
            ext = f.suffix.lower()

            # Format the modification time
            if platform.system() == "Windows":
                uploaded_fmt = datetime.fromtimestamp(stat.st_mtime).strftime("%d %b %Y %#I:%M %p")
            else:
                uploaded_fmt = datetime.fromtimestamp(stat.st_mtime).strftime("%d %b %Y %-I:%M %p")

            results.append({
                "filename": f.name,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "size_bytes": stat.st_size,
                "category": cat_key,
                "category_label": cat_label,
                "uploaded_at": uploaded_fmt,
                "uploaded_ts": stat.st_mtime,
                "extension": ext.lstrip("."),
                "download_url": f"/documents/download/{cat_key}/{f.name}",
            })

    # Sort by most recently modified
    results.sort(key=lambda x: x["uploaded_ts"], reverse=True)
    return results


def list_portfolio_photos() -> list[dict]:
    """
    List image files in the portfolio category for the customer-facing gallery.

    Returns list of dicts with: filename, url, thumbnail, title, description.
    Only includes image files (jpg, jpeg, png, gif, webp).

    Titles/descriptions can come from an optional ``portfolio.json`` metadata
    file in the portfolio directory.  Format::

        {
          "my_photo.jpg": {"title": "Exposed Aggregate Driveway", "description": "Broom finish"},
          "another.jpg": {"title": "Side Pathway"}
        }

    If no metadata exists for a file, the title is derived from the filename.
    """
    import json

    _ensure_dirs()
    results = []
    portfolio_dir = DOCUMENTS_DIR / "portfolio"

    if not portfolio_dir.exists():
        return results

    # Load optional metadata JSON
    meta: dict = {}
    meta_path = portfolio_dir / "portfolio.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}

    for f in sorted(portfolio_dir.iterdir()):
        if not f.is_file():
            continue
        if f.name.startswith(".") or f.name.startswith("~"):
            continue
        if f.name == "portfolio.json":
            continue

        ext = f.suffix.lower()
        if ext not in IMAGE_EXTENSIONS:
            continue

        # Derive title from metadata or filename
        file_meta = meta.get(f.name, {})
        title = file_meta.get("title", "")
        description = file_meta.get("description", "")

        if not title:
            # Convert filename to readable title: "Exposed_Aggregate-Driveway.jpg" → "Exposed Aggregate Driveway"
            name_part = os.path.splitext(f.name)[0]
            title = name_part.replace("_", " ").replace("-", " ").strip()
            title = re.sub(r"\s+", " ", title)  # collapse multiple spaces
            title = title.title()

        results.append({
            "filename": f.name,
            "url": f"/static/documents/portfolio/{f.name}",
            "thumbnail": f"/static/documents/portfolio/{f.name}",
            "title": title,
            "description": description,
        })

    return results


def save_document(content: bytes, filename: str, category: str) -> str:
    """
    Save a file to static/documents/{category}/.

    Args:
        content: Raw file bytes.
        filename: Original filename (will be sanitised).
        category: Category key (tcs, insurance, datasheets, swms, other).

    Returns:
        The relative URL path to the saved file.

    Raises:
        ValueError: If category is invalid, extension not allowed, or file too large.
    """
    _ensure_dirs()

    if category not in CATEGORIES:
        raise ValueError(f"Invalid category '{category}'. Must be one of: {', '.join(CATEGORIES.keys())}")

    safe_name = _safe_filename(filename)
    ext = os.path.splitext(safe_name)[1].lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"File type '{ext}' not allowed. Accepted: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    if len(content) > MAX_UPLOAD_SIZE:
        raise ValueError(f"File too large. Maximum size: {MAX_UPLOAD_SIZE // (1024 * 1024)}MB")

    if len(content) == 0:
        raise ValueError("Empty file")

    # If file with same name exists, add timestamp suffix
    dest = DOCUMENTS_DIR / category / safe_name
    if dest.exists():
        name_part, ext_part = os.path.splitext(safe_name)
        ts = sydney_now().strftime("%Y%m%d_%H%M%S")
        safe_name = f"{name_part}_{ts}{ext_part}"
        dest = DOCUMENTS_DIR / category / safe_name

    dest.write_bytes(content)

    return f"/documents/download/{category}/{safe_name}"


def delete_document(category: str, filename: str) -> bool:
    """
    Delete a document from static/documents/{category}/{filename}.

    Returns True if deleted, False if not found.
    """
    if category not in CATEGORIES:
        return False

    safe_name = _safe_filename(filename)
    path = DOCUMENTS_DIR / category / safe_name

    if not path.exists() or not path.is_file():
        return False

    # Safety: ensure we're not deleting outside the documents dir
    try:
        path.resolve().relative_to(DOCUMENTS_DIR.resolve())
    except ValueError:
        return False

    path.unlink()
    return True


def get_document_path(category: str, filename: str) -> Optional[Path]:
    """
    Get the full filesystem path for a document.

    Returns None if the file doesn't exist or category is invalid.
    """
    if category not in CATEGORIES:
        return None

    safe_name = _safe_filename(filename)
    path = DOCUMENTS_DIR / category / safe_name

    if not path.exists() or not path.is_file():
        return None

    # Safety check
    try:
        path.resolve().relative_to(DOCUMENTS_DIR.resolve())
    except ValueError:
        return None

    return path


# =============================================================================
# T&C PDF GENERATION
# =============================================================================

def generate_tc_pdf(
    text_content: str,
    business_name: str,
    business_abn: str = "",
    business_phone: str = "",
    business_email: str = "",
) -> bytes:
    """
    Generate a professional Terms & Conditions PDF using reportlab.

    Args:
        text_content: The T&C text. Clauses can be separated by blank lines.
                      Lines starting with a number or letter followed by a period/bracket
                      are treated as clause headings.
        business_name: Business name for the header.
        business_abn: Optional ABN for the header.
        business_phone: Optional phone for footer.
        business_email: Optional email for footer.

    Returns:
        PDF file content as bytes.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm, cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, HRFlowable, PageBreak,
    )

    buffer = io.BytesIO()

    # Page setup
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=25 * mm,
        rightMargin=25 * mm,
        topMargin=25 * mm,
        bottomMargin=30 * mm,
        title=f"{business_name} — Terms & Conditions",
        author=business_name,
    )

    # Colours
    brand_orange = HexColor("#F97316")
    dark_gray = HexColor("#1F2937")
    mid_gray = HexColor("#6B7280")
    light_gray = HexColor("#D1D5DB")

    # Styles
    styles = getSampleStyleSheet()

    style_title = ParagraphStyle(
        "TCTitle",
        parent=styles["Heading1"],
        fontSize=20,
        leading=24,
        textColor=dark_gray,
        spaceAfter=4 * mm,
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
    )

    style_business = ParagraphStyle(
        "TCBusiness",
        parent=styles["Normal"],
        fontSize=11,
        leading=14,
        textColor=mid_gray,
        alignment=TA_CENTER,
        spaceAfter=2 * mm,
    )

    style_date = ParagraphStyle(
        "TCDate",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=mid_gray,
        alignment=TA_CENTER,
        spaceAfter=6 * mm,
    )

    style_clause_heading = ParagraphStyle(
        "TCClauseHeading",
        parent=styles["Heading2"],
        fontSize=12,
        leading=16,
        textColor=dark_gray,
        spaceBefore=5 * mm,
        spaceAfter=2 * mm,
        fontName="Helvetica-Bold",
    )

    style_clause_body = ParagraphStyle(
        "TCClauseBody",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        textColor=dark_gray,
        spaceAfter=3 * mm,
        alignment=TA_JUSTIFY,
        fontName="Helvetica",
    )

    style_footer_text = ParagraphStyle(
        "TCFooter",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        textColor=mid_gray,
        alignment=TA_CENTER,
    )

    # Build content
    story = []

    # Header
    story.append(Paragraph(f"{business_name}", style_title))
    story.append(Paragraph("Terms &amp; Conditions", style_business))

    # ABN line
    abn_parts = []
    if business_abn:
        abn_parts.append(f"ABN: {business_abn}")
    if business_phone:
        abn_parts.append(business_phone)
    if business_email:
        abn_parts.append(business_email)
    if abn_parts:
        story.append(Paragraph(" | ".join(abn_parts), style_date))

    # Date
    now = sydney_now()
    if platform.system() == "Windows":
        date_str = now.strftime("%#d %B %Y")
    else:
        date_str = now.strftime("%-d %B %Y")
    story.append(Paragraph(f"Effective Date: {date_str}", style_date))

    # Divider
    story.append(HRFlowable(
        width="100%", thickness=1, color=light_gray,
        spaceAfter=6 * mm, spaceBefore=2 * mm,
    ))

    # Parse text into clauses
    clauses = _parse_tc_text(text_content)

    clause_number = 0
    for clause in clauses:
        if clause["type"] == "heading":
            clause_number += 1
            heading_text = clause["text"]
            # If the heading doesn't start with a number, prepend one
            if not re.match(r"^\d+[\.\)]", heading_text):
                heading_text = f"{clause_number}. {heading_text}"
            story.append(Paragraph(heading_text, style_clause_heading))
        elif clause["type"] == "body":
            # Escape special XML characters for reportlab
            text = clause["text"]
            text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(text, style_clause_body))
        elif clause["type"] == "spacer":
            story.append(Spacer(1, 3 * mm))

    # Footer spacer
    story.append(Spacer(1, 10 * mm))
    story.append(HRFlowable(
        width="100%", thickness=0.5, color=light_gray,
        spaceAfter=3 * mm,
    ))

    footer_parts = [f"&copy; {now.year} {business_name}"]
    if business_phone:
        footer_parts.append(business_phone)
    if business_email:
        footer_parts.append(business_email)
    story.append(Paragraph(" | ".join(footer_parts), style_footer_text))

    # Build PDF
    doc.build(story)

    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


def _parse_tc_text(text: str) -> list[dict]:
    """
    Parse raw T&C text into structured clauses.

    Rules:
    - Blank lines separate sections.
    - Lines that look like headings (start with number/letter + dot/bracket,
      or are ALL CAPS, or are short and bold-looking) become headings.
    - Everything else becomes body text.
    """
    lines = text.strip().split("\n")
    clauses = []
    current_body_lines = []

    # Patterns that indicate a heading
    heading_pattern = re.compile(
        r"^(\d+[\.\)]\s+.+|[A-Z][A-Z\s&\-]{4,}$|#{1,3}\s+.+)"
    )

    def flush_body():
        if current_body_lines:
            body_text = " ".join(current_body_lines).strip()
            if body_text:
                clauses.append({"type": "body", "text": body_text})
            current_body_lines.clear()

    for line in lines:
        stripped = line.strip()

        # Blank line — flush current body and add spacer
        if not stripped:
            flush_body()
            # Only add spacer if last item isn't already a spacer
            if clauses and clauses[-1]["type"] != "spacer":
                clauses.append({"type": "spacer"})
            continue

        # Check if this looks like a heading
        if heading_pattern.match(stripped):
            flush_body()
            # Clean up markdown-style headings
            clean = re.sub(r"^#{1,3}\s+", "", stripped)
            clauses.append({"type": "heading", "text": clean})
        else:
            current_body_lines.append(stripped)

    # Flush any remaining body text
    flush_body()

    return clauses
