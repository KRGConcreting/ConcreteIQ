"""
Quote / Invoice / Receipt PDF Generation using ReportLab.

Generates professional PDF documents with KRG Concreting branding.
No external system libraries required (unlike the previous WeasyPrint implementation).
"""

from io import BytesIO
from typing import Optional
from pathlib import Path
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    Image,
    HRFlowable,
    KeepTogether,
)
from reportlab.platypus.flowables import Flowable

from app.config import get_settings
from app.core.dates import sydney_now


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent.parent
STATIC_IMAGES_DIR = PROJECT_ROOT / "static" / "images"


# ---------------------------------------------------------------------------
# Brand colours
# ---------------------------------------------------------------------------

BRAND_ORANGE = colors.HexColor("#E86823")
DARK_SLATE = colors.HexColor("#1e293b")
LIGHT_GREY = colors.HexColor("#f8fafc")
MID_GREY = colors.HexColor("#e2e8f0")
GREEN = colors.HexColor("#16a34a")
WHITE = colors.white
BLACK = colors.black


# ---------------------------------------------------------------------------
# Reusable paragraph styles
# ---------------------------------------------------------------------------

def _styles():
    """Return a dict of reusable ParagraphStyles."""
    return {
        "title": ParagraphStyle(
            "title",
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=DARK_SLATE,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            fontName="Helvetica",
            fontSize=10,
            leading=13,
            textColor=DARK_SLATE,
        ),
        "section_heading": ParagraphStyle(
            "section_heading",
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=DARK_SLATE,
            spaceBefore=6,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body",
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=DARK_SLATE,
        ),
        "body_bold": ParagraphStyle(
            "body_bold",
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=DARK_SLATE,
        ),
        "body_small": ParagraphStyle(
            "body_small",
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#64748b"),
        ),
        "body_right": ParagraphStyle(
            "body_right",
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=DARK_SLATE,
            alignment=TA_RIGHT,
        ),
        "body_bold_right": ParagraphStyle(
            "body_bold_right",
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=DARK_SLATE,
            alignment=TA_RIGHT,
        ),
        "orange_number": ParagraphStyle(
            "orange_number",
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            textColor=BRAND_ORANGE,
            alignment=TA_RIGHT,
        ),
        "table_header": ParagraphStyle(
            "table_header",
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=WHITE,
        ),
        "table_header_right": ParagraphStyle(
            "table_header_right",
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=WHITE,
            alignment=TA_RIGHT,
        ),
        "table_cell": ParagraphStyle(
            "table_cell",
            fontName="Helvetica",
            fontSize=8,
            leading=11,
            textColor=DARK_SLATE,
        ),
        "table_cell_right": ParagraphStyle(
            "table_cell_right",
            fontName="Helvetica",
            fontSize=8,
            leading=11,
            textColor=DARK_SLATE,
            alignment=TA_RIGHT,
        ),
        "table_cell_bold": ParagraphStyle(
            "table_cell_bold",
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=11,
            textColor=DARK_SLATE,
        ),
        "table_cell_bold_right": ParagraphStyle(
            "table_cell_bold_right",
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=11,
            textColor=DARK_SLATE,
            alignment=TA_RIGHT,
        ),
        "footer": ParagraphStyle(
            "footer",
            fontName="Helvetica",
            fontSize=7,
            leading=10,
            textColor=colors.HexColor("#94a3b8"),
            alignment=TA_CENTER,
        ),
        "notes": ParagraphStyle(
            "notes",
            fontName="Helvetica",
            fontSize=8,
            leading=11,
            textColor=DARK_SLATE,
        ),
        "total_label": ParagraphStyle(
            "total_label",
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=14,
            textColor=DARK_SLATE,
            alignment=TA_RIGHT,
        ),
        "total_value_orange": ParagraphStyle(
            "total_value_orange",
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=16,
            textColor=BRAND_ORANGE,
            alignment=TA_RIGHT,
        ),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_business(include_bank: bool = False) -> dict:
    """Build business info dict from app config (real details, not placeholders)."""
    s = get_settings()
    biz = {
        "name": s.business_name,
        "trading_as": s.trading_as,
        "abn": s.abn,
        "address": s.business_address,
        "phone": s.business_phone,
        "email": s.business_email,
    }
    if include_bank:
        biz["bank_name"] = s.bank_name
        biz["bsb"] = s.bank_bsb
        biz["account"] = s.bank_account
        biz["bank_account_name"] = s.bank_account_name
    return biz


def _logo_path() -> str:
    """Return the filesystem path string for the KRG logo."""
    logo = STATIC_IMAGES_DIR / "KyleRGyoles_Concreting_Logo.png"
    return str(logo)


def _fmt(cents) -> str:
    """Format cents as a dollar string, e.g. 12050 -> '$120.50'."""
    if cents is None:
        return "$0.00"
    return f"${cents / 100:,.2f}"


def _fmt_date(d) -> str:
    """Format a date object or string to DD/MM/YYYY."""
    if d is None:
        return ""
    if isinstance(d, str):
        return d
    if isinstance(d, date):
        return d.strftime("%d/%m/%Y")
    return str(d)


def _fmt_date_long(d) -> str:
    """Format a date object to '03 March 2026'."""
    if d is None:
        return ""
    if isinstance(d, str):
        return d
    if isinstance(d, date):
        return d.strftime("%d %B %Y")
    return str(d)


def _try_logo(max_width=45 * mm, max_height=22 * mm):
    """Return a ReportLab Image flowable for the logo, or None if missing."""
    path = _logo_path()
    try:
        if not Path(path).exists():
            return None
        img = Image(path)
        # Scale preserving aspect ratio
        iw, ih = img.imageWidth, img.imageHeight
        if iw <= 0 or ih <= 0:
            return None
        ratio = min(max_width / iw, max_height / ih)
        img.drawWidth = iw * ratio
        img.drawHeight = ih * ratio
        return img
    except Exception:
        return None


class OrangeAccentLine(Flowable):
    """Draws a thin orange accent line across the full width at the top."""

    def __init__(self, width, height=2):
        super().__init__()
        self.width = width
        self.height = height

    def draw(self):
        self.canv.setStrokeColor(BRAND_ORANGE)
        self.canv.setFillColor(BRAND_ORANGE)
        self.canv.rect(0, 0, self.width, self.height, fill=1, stroke=0)


def _build_doc(buffer) -> SimpleDocTemplate:
    """Create an A4 SimpleDocTemplate with 15mm margins."""
    return SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )


def _content_width() -> float:
    """Usable content width = A4 width minus 2 x 15mm margins."""
    return A4[0] - 30 * mm


def _add_footer(canvas, doc, business: dict):
    """Draw a centred footer on every page."""
    canvas.saveState()
    w = A4[0]
    y = 10 * mm
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#94a3b8"))
    parts = []
    if business.get("trading_as"):
        parts.append(business["trading_as"])
    elif business.get("name"):
        parts.append(business["name"])
    if business.get("abn"):
        parts.append(f"ABN {business['abn']}")
    if business.get("phone"):
        parts.append(business["phone"])
    if business.get("email"):
        parts.append(business["email"])
    line = "  |  ".join(parts)
    canvas.drawCentredString(w / 2, y, line)
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Header builder (shared across quote / invoice / receipt)
# ---------------------------------------------------------------------------

def _build_header(story, styles, business: dict, doc_label: str, doc_number: str):
    """
    Add branded header: orange accent line, then a two-column row with
    logo + business name on the left, document number on the right.
    """
    cw = _content_width()

    # Orange accent line
    story.append(OrangeAccentLine(cw))
    story.append(Spacer(1, 4 * mm))

    # Left side: logo + business name
    logo = _try_logo()
    left_parts = []
    if logo:
        left_parts.append(logo)
        left_parts.append(Spacer(1, 2 * mm))
    biz_name = business.get("trading_as") or business.get("name", "")
    left_parts.append(Paragraph(biz_name, styles["title"]))
    if business.get("trading_as") and business.get("name"):
        left_parts.append(Paragraph(business["name"], styles["body_small"]))

    # Right side: document label + number
    right_parts = [
        Paragraph(doc_label, styles["body_right"]),
        Paragraph(doc_number, styles["orange_number"]),
    ]

    # Build a two-column table for the header
    left_col_width = cw * 0.6
    right_col_width = cw * 0.4

    header_table = Table(
        [[left_parts, right_parts]],
        colWidths=[left_col_width, right_col_width],
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 3 * mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=MID_GREY))
    story.append(Spacer(1, 4 * mm))


# ---------------------------------------------------------------------------
# Info block builders
# ---------------------------------------------------------------------------

def _customer_block(customer: dict, styles) -> list:
    """Build a list of Paragraphs for customer info."""
    parts = []
    if customer.get("name"):
        parts.append(Paragraph(customer["name"], styles["body_bold"]))
    addr_pieces = []
    if customer.get("street"):
        addr_pieces.append(customer["street"])
    city_line = ", ".join(
        filter(None, [customer.get("city"), customer.get("state")])
    )
    if customer.get("postcode"):
        city_line = f"{city_line} {customer['postcode']}".strip()
    if city_line:
        addr_pieces.append(city_line)
    for line in addr_pieces:
        parts.append(Paragraph(line, styles["body"]))
    if customer.get("email"):
        parts.append(Paragraph(customer["email"], styles["body_small"]))
    if customer.get("phone"):
        parts.append(Paragraph(customer["phone"], styles["body_small"]))
    return parts


def _info_pair(label: str, value: str, styles) -> list:
    """Return a label + value as [Paragraph, Paragraph]."""
    return [
        Paragraph(label, styles["body_small"]),
        Paragraph(value, styles["body_bold"]),
    ]


# ---------------------------------------------------------------------------
# QUOTE PDF
# ---------------------------------------------------------------------------

def generate_quote_pdf(
    quote: dict,
    customer: Optional[dict] = None,
    business: Optional[dict] = None,
) -> bytes:
    """
    Generate PDF for a quote.

    Args:
        quote: Quote data dict
        customer: Customer data dict
        business: Business info dict (name, address, phone, etc.)

    Returns:
        PDF bytes
    """
    if business is None:
        business = _default_business()

    customer = customer or {}
    styles = _styles()
    buffer = BytesIO()
    doc = _build_doc(buffer)
    cw = _content_width()
    story = []

    # --- Header ---
    _build_header(story, styles, business, "QUOTE", quote.get("quote_number", ""))

    # --- Quote meta + customer info (two columns) ---
    # Left: PREPARED FOR
    left_col = []
    left_col.append(Paragraph("PREPARED FOR", styles["section_heading"]))
    left_col.extend(_customer_block(customer, styles))

    # Right: Quote details + Job Location
    right_col = []
    right_col.extend(_info_pair("Quote Date", _fmt_date_long(quote.get("quote_date")), styles))
    right_col.append(Spacer(1, 2 * mm))
    right_col.extend(_info_pair("Valid Until", _fmt_date_long(quote.get("expiry_date")), styles))
    if quote.get("status"):
        right_col.append(Spacer(1, 2 * mm))
        right_col.extend(_info_pair("Status", quote["status"].replace("_", " ").title(), styles))

    info_table = Table(
        [[left_col, right_col]],
        colWidths=[cw * 0.5, cw * 0.5],
    )
    info_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 3 * mm))

    # Job location
    if quote.get("job_name") or quote.get("job_address"):
        story.append(Paragraph("JOB LOCATION", styles["section_heading"]))
        if quote.get("job_name"):
            story.append(Paragraph(quote["job_name"], styles["body_bold"]))
        if quote.get("job_address"):
            story.append(Paragraph(quote["job_address"], styles["body"]))
        story.append(Spacer(1, 2 * mm))

    # Job specs row
    specs = []
    if quote.get("concrete_finish"):
        specs.append(("Finish", quote["concrete_finish"]))
    if quote.get("reinforcement_type"):
        specs.append(("Reinforcement", quote["reinforcement_type"]))
    if quote.get("concrete_grade"):
        specs.append(("Concrete Grade", quote["concrete_grade"]))
    if specs:
        spec_cells = []
        for label, val in specs:
            spec_cells.append([
                Paragraph(label, styles["body_small"]),
                Paragraph(val, styles["body_bold"]),
            ])
        num_specs = len(spec_cells)
        spec_table = Table(
            [spec_cells],
            colWidths=[cw / num_specs] * num_specs,
        )
        spec_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(spec_table)
        story.append(Spacer(1, 4 * mm))

    # --- Line items table ---
    story.append(Paragraph("SCOPE OF WORK", styles["section_heading"]))
    story.append(Spacer(1, 2 * mm))

    line_items = quote.get("customer_line_items", [])
    table_data = []
    # Header row
    table_data.append([
        Paragraph("Description", styles["table_header"]),
        Paragraph("Amount", styles["table_header_right"]),
    ])

    row_idx = 1
    for item in line_items:
        category = item.get("category", "")
        total_cents = item.get("total_cents", 0) or 0

        # Skip discount groups here — they show in the totals section below
        if total_cents < 0 or category.lower().startswith("discount"):
            continue

        # Category row
        table_data.append([
            Paragraph(category, styles["table_cell_bold"]),
            Paragraph(_fmt(total_cents), styles["table_cell_bold_right"]),
        ])
        row_idx += 1
        # Sub-items
        for sub in item.get("sub_items", []):
            desc = sub.get("description", "") if isinstance(sub, dict) else str(sub)
            table_data.append([
                Paragraph(f"    {desc}", styles["table_cell"]),
                Paragraph("", styles["table_cell"]),
            ])
            row_idx += 1

    col_widths = [cw * 0.75, cw * 0.25]
    items_table = Table(table_data, colWidths=col_widths, repeatRows=1)

    # Style the table
    ts = [
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), DARK_SLATE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        # Grid
        ("LINEBELOW", (0, 0), (-1, 0), 1, DARK_SLATE),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, MID_GREY),
        # Padding
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    # Alternating row backgrounds (skip header)
    for i in range(1, len(table_data)):
        if i % 2 == 0:
            ts.append(("BACKGROUND", (0, i), (-1, i), LIGHT_GREY))
        ts.append(("LINEBELOW", (0, i), (-1, i), 0.25, MID_GREY))

    items_table.setStyle(TableStyle(ts))
    story.append(items_table)
    story.append(Spacer(1, 4 * mm))

    # --- Totals ---
    totals_data = []
    totals_data.append([
        Paragraph("Subtotal", styles["body_right"]),
        Paragraph(_fmt(quote.get("subtotal_cents")), styles["body_bold_right"]),
    ])
    discount = quote.get("discount_cents", 0) or 0
    # Fallback: extract discount from customer_line_items if not set on quote
    if not discount and line_items:
        for li in line_items:
            tc = li.get("total_cents", 0) or 0
            if tc < 0 or (li.get("category", "").lower().startswith("discount")):
                discount = abs(tc)
                break
    if discount > 0:
        discount_style = ParagraphStyle(
            "discount_val",
            parent=styles["body_bold_right"],
            textColor=GREEN,
        )
        totals_data.append([
            Paragraph("Discount", styles["body_right"]),
            Paragraph(f"-{_fmt(discount)}", discount_style),
        ])
    totals_data.append([
        Paragraph("GST (10%)", styles["body_right"]),
        Paragraph(_fmt(quote.get("gst_cents")), styles["body_bold_right"]),
    ])
    totals_data.append([
        Paragraph("TOTAL (inc GST)", styles["total_label"]),
        Paragraph(_fmt(quote.get("total_cents")), styles["total_value_orange"]),
    ])

    totals_table = Table(
        totals_data,
        colWidths=[cw * 0.75, cw * 0.25],
    )
    totals_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LINEABOVE", (0, -1), (-1, -1), 1, DARK_SLATE),
        ("TOPPADDING", (0, -1), (-1, -1), 6),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 6),
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 4 * mm))

    # --- Payment schedule ---
    payment_schedule = quote.get("payments", [])
    if payment_schedule:
        story.append(Paragraph("PAYMENT SCHEDULE", styles["section_heading"]))
        story.append(Spacer(1, 2 * mm))
        sched_data = [[
            Paragraph("Milestone", styles["table_header"]),
            Paragraph("Amount", styles["table_header_right"]),
        ]]
        for pmt in payment_schedule:
            sched_data.append([
                Paragraph(pmt.get("name", ""), styles["table_cell"]),
                Paragraph(_fmt(pmt.get("amount_cents")), styles["table_cell_right"]),
            ])
        sched_table = Table(sched_data, colWidths=[cw * 0.70, cw * 0.30], repeatRows=1)
        sched_ts = [
            ("BACKGROUND", (0, 0), (-1, 0), DARK_SLATE),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("LINEBELOW", (0, 0), (-1, 0), 1, DARK_SLATE),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        for i in range(1, len(sched_data)):
            if i % 2 == 0:
                sched_ts.append(("BACKGROUND", (0, i), (-1, i), LIGHT_GREY))
            sched_ts.append(("LINEBELOW", (0, i), (-1, i), 0.25, MID_GREY))
        sched_table.setStyle(TableStyle(sched_ts))
        story.append(sched_table)
        story.append(Spacer(1, 4 * mm))

    # --- Notes ---
    notes = quote.get("notes")
    if notes:
        story.append(Paragraph("NOTES", styles["section_heading"]))
        story.append(Spacer(1, 1 * mm))
        for line in str(notes).split("\n"):
            story.append(Paragraph(line, styles["notes"]))
        story.append(Spacer(1, 4 * mm))

    # Build
    biz_copy = dict(business)
    doc.build(
        story,
        onFirstPage=lambda c, d: _add_footer(c, d, biz_copy),
        onLaterPages=lambda c, d: _add_footer(c, d, biz_copy),
    )
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# INVOICE PDF
# ---------------------------------------------------------------------------

def generate_invoice_pdf(
    invoice: dict,
    customer: Optional[dict] = None,
    business: Optional[dict] = None,
    payments: Optional[list] = None,
) -> bytes:
    """
    Generate PDF for an invoice.

    Args:
        invoice: Invoice data dict
        customer: Customer data dict
        business: Business info dict
        payments: List of payment dicts (amount_cents, method, reference, payment_date)

    Returns:
        PDF bytes
    """
    if business is None:
        business = _default_business(include_bank=True)

    customer = customer or {}
    payments = payments or []
    styles = _styles()
    buffer = BytesIO()
    doc = _build_doc(buffer)
    cw = _content_width()
    story = []

    # --- Header ---
    _build_header(story, styles, business, "TAX INVOICE", invoice.get("invoice_number", ""))

    # --- Invoice meta + customer (two columns) ---
    left_col = []
    left_col.append(Paragraph("BILL TO", styles["section_heading"]))
    left_col.extend(_customer_block(customer, styles))

    right_col = []
    right_col.extend(_info_pair("Issue Date", _fmt_date_long(invoice.get("issue_date")), styles))
    right_col.append(Spacer(1, 2 * mm))
    right_col.extend(_info_pair("Due Date", _fmt_date_long(invoice.get("due_date")), styles))
    if invoice.get("status"):
        right_col.append(Spacer(1, 2 * mm))
        status_text = invoice["status"].replace("_", " ").title()
        right_col.extend(_info_pair("Status", status_text, styles))

    info_table = Table(
        [[left_col, right_col]],
        colWidths=[cw * 0.5, cw * 0.5],
    )
    info_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 2 * mm))

    # Description
    if invoice.get("description"):
        story.append(Paragraph("DESCRIPTION", styles["section_heading"]))
        story.append(Paragraph(invoice["description"], styles["body"]))
        story.append(Spacer(1, 3 * mm))

    # --- Line items ---
    line_items = invoice.get("line_items", [])
    if line_items:
        story.append(Paragraph("LINE ITEMS", styles["section_heading"]))
        story.append(Spacer(1, 2 * mm))

        table_data = [[
            Paragraph("Description", styles["table_header"]),
            Paragraph("Qty", styles["table_header_right"]),
            Paragraph("Unit", styles["table_header"]),
            Paragraph("Unit Price", styles["table_header_right"]),
            Paragraph("Total", styles["table_header_right"]),
        ]]
        for li in line_items:
            table_data.append([
                Paragraph(li.get("description", ""), styles["table_cell_bold"]),
                Paragraph(str(li.get("quantity", "")), styles["table_cell_right"]),
                Paragraph(li.get("unit", ""), styles["table_cell"]),
                Paragraph(_fmt(li.get("unit_price_cents")), styles["table_cell_right"]),
                Paragraph(_fmt(li.get("total_cents")), styles["table_cell_bold_right"]),
            ])
            # Sub-items
            for sub in li.get("sub_items", []):
                desc = sub.get("description", "") if isinstance(sub, dict) else str(sub)
                table_data.append([
                    Paragraph(f"    {desc}", styles["table_cell"]),
                    Paragraph("", styles["table_cell"]),
                    Paragraph("", styles["table_cell"]),
                    Paragraph("", styles["table_cell"]),
                    Paragraph("", styles["table_cell"]),
                ])

        col_widths = [cw * 0.40, cw * 0.10, cw * 0.12, cw * 0.18, cw * 0.20]
        items_table = Table(table_data, colWidths=col_widths, repeatRows=1)
        ts = [
            ("BACKGROUND", (0, 0), (-1, 0), DARK_SLATE),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("LINEBELOW", (0, 0), (-1, 0), 1, DARK_SLATE),
            ("LINEBELOW", (0, -1), (-1, -1), 0.5, MID_GREY),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        for i in range(1, len(table_data)):
            if i % 2 == 0:
                ts.append(("BACKGROUND", (0, i), (-1, i), LIGHT_GREY))
            ts.append(("LINEBELOW", (0, i), (-1, i), 0.25, MID_GREY))
        items_table.setStyle(TableStyle(ts))
        story.append(items_table)
        story.append(Spacer(1, 4 * mm))

    # --- Totals ---
    totals_data = [
        [
            Paragraph("Subtotal", styles["body_right"]),
            Paragraph(_fmt(invoice.get("subtotal_cents")), styles["body_bold_right"]),
        ],
        [
            Paragraph("GST (10%)", styles["body_right"]),
            Paragraph(_fmt(invoice.get("gst_cents")), styles["body_bold_right"]),
        ],
        [
            Paragraph("TOTAL (inc GST)", styles["total_label"]),
            Paragraph(_fmt(invoice.get("total_cents")), styles["total_value_orange"]),
        ],
    ]
    paid = invoice.get("paid_cents", 0) or 0
    if paid > 0:
        totals_data.append([
            Paragraph("Paid", styles["body_right"]),
            Paragraph(f"-{_fmt(paid)}", ParagraphStyle(
                "paid_val", parent=styles["body_bold_right"], textColor=GREEN)),
        ])
        balance = (invoice.get("total_cents", 0) or 0) - paid
        totals_data.append([
            Paragraph("BALANCE DUE", styles["total_label"]),
            Paragraph(_fmt(balance), styles["total_value_orange"]),
        ])

    totals_table = Table(totals_data, colWidths=[cw * 0.75, cw * 0.25])
    ts_totals = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    # Line above the first "TOTAL" row (index 2)
    ts_totals.append(("LINEABOVE", (0, 2), (-1, 2), 1, DARK_SLATE))
    ts_totals.append(("TOPPADDING", (0, 2), (-1, 2), 6))
    ts_totals.append(("BOTTOMPADDING", (0, 2), (-1, 2), 6))
    # If there is a balance due row, emphasize it
    if paid > 0:
        last = len(totals_data) - 1
        ts_totals.append(("LINEABOVE", (0, last), (-1, last), 1, BRAND_ORANGE))
        ts_totals.append(("TOPPADDING", (0, last), (-1, last), 6))
        ts_totals.append(("BOTTOMPADDING", (0, last), (-1, last), 6))

    totals_table.setStyle(TableStyle(ts_totals))
    story.append(totals_table)
    story.append(Spacer(1, 4 * mm))

    # --- Payment schedule ---
    payment_schedule = invoice.get("payment_schedule", [])
    if payment_schedule:
        story.append(Paragraph("PAYMENT SCHEDULE", styles["section_heading"]))
        story.append(Spacer(1, 2 * mm))
        sched_data = [[
            Paragraph("Milestone", styles["table_header"]),
            Paragraph("%", styles["table_header_right"]),
            Paragraph("Amount", styles["table_header_right"]),
            Paragraph("Status", styles["table_header"]),
        ]]
        for ps in payment_schedule:
            status_text = (ps.get("status") or "").replace("_", " ").title()
            sched_data.append([
                Paragraph(ps.get("label", ""), styles["table_cell"]),
                Paragraph(f"{ps.get('percent', '')}%", styles["table_cell_right"]),
                Paragraph(_fmt(ps.get("amount_cents")), styles["table_cell_right"]),
                Paragraph(status_text, styles["table_cell"]),
            ])
        sched_table = Table(
            sched_data,
            colWidths=[cw * 0.40, cw * 0.12, cw * 0.25, cw * 0.23],
            repeatRows=1,
        )
        sched_ts = [
            ("BACKGROUND", (0, 0), (-1, 0), DARK_SLATE),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("LINEBELOW", (0, 0), (-1, 0), 1, DARK_SLATE),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        for i in range(1, len(sched_data)):
            if i % 2 == 0:
                sched_ts.append(("BACKGROUND", (0, i), (-1, i), LIGHT_GREY))
            sched_ts.append(("LINEBELOW", (0, i), (-1, i), 0.25, MID_GREY))
        sched_table.setStyle(TableStyle(sched_ts))
        story.append(sched_table)
        story.append(Spacer(1, 4 * mm))

    # --- Payment history ---
    if payments:
        story.append(Paragraph("PAYMENT HISTORY", styles["section_heading"]))
        story.append(Spacer(1, 2 * mm))
        ph_data = [[
            Paragraph("Date", styles["table_header"]),
            Paragraph("Method", styles["table_header"]),
            Paragraph("Reference", styles["table_header"]),
            Paragraph("Amount", styles["table_header_right"]),
        ]]
        for pmt in payments:
            ph_data.append([
                Paragraph(_fmt_date(pmt.get("payment_date")), styles["table_cell"]),
                Paragraph((pmt.get("method") or "").title(), styles["table_cell"]),
                Paragraph(pmt.get("reference", "") or "", styles["table_cell"]),
                Paragraph(_fmt(pmt.get("amount_cents")), styles["table_cell_right"]),
            ])
        ph_table = Table(
            ph_data,
            colWidths=[cw * 0.22, cw * 0.22, cw * 0.31, cw * 0.25],
            repeatRows=1,
        )
        ph_ts = [
            ("BACKGROUND", (0, 0), (-1, 0), DARK_SLATE),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("LINEBELOW", (0, 0), (-1, 0), 1, DARK_SLATE),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        for i in range(1, len(ph_data)):
            if i % 2 == 0:
                ph_ts.append(("BACKGROUND", (0, i), (-1, i), LIGHT_GREY))
            ph_ts.append(("LINEBELOW", (0, i), (-1, i), 0.25, MID_GREY))
        ph_table.setStyle(TableStyle(ph_ts))
        story.append(ph_table)
        story.append(Spacer(1, 4 * mm))

    # --- Bank details ---
    has_bank = business.get("bsb") or business.get("account")
    if has_bank:
        story.append(Paragraph("BANK DETAILS", styles["section_heading"]))
        story.append(Spacer(1, 1 * mm))
        bank_lines = []
        if business.get("bank_name"):
            bank_lines.append(f"Bank: {business['bank_name']}")
        if business.get("bsb"):
            bank_lines.append(f"BSB: {business['bsb']}")
        if business.get("account"):
            bank_lines.append(f"Account: {business['account']}")
        bank_lines.append(f"Name: {business.get('bank_account_name') or business.get('trading_as') or business.get('name', '')}")
        for bl in bank_lines:
            story.append(Paragraph(bl, styles["body"]))
        story.append(Spacer(1, 4 * mm))

    # --- Notes ---
    notes = invoice.get("notes")
    if notes:
        story.append(Paragraph("NOTES", styles["section_heading"]))
        story.append(Spacer(1, 1 * mm))
        for line in str(notes).split("\n"):
            story.append(Paragraph(line, styles["notes"]))
        story.append(Spacer(1, 4 * mm))

    # Build
    biz_copy = dict(business)
    doc.build(
        story,
        onFirstPage=lambda c, d: _add_footer(c, d, biz_copy),
        onLaterPages=lambda c, d: _add_footer(c, d, biz_copy),
    )
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# RECEIPT PDF
# ---------------------------------------------------------------------------

def generate_receipt_pdf(
    payment: dict,
    invoice: dict,
    customer: Optional[dict] = None,
    business: Optional[dict] = None,
) -> bytes:
    """
    Generate PDF receipt for a payment.

    Args:
        payment: Payment data dict (amount_cents, method, reference, payment_date)
        invoice: Invoice data dict (invoice_number, total_cents, paid_cents, etc.)
        customer: Customer data dict
        business: Business info dict

    Returns:
        PDF bytes
    """
    if business is None:
        business = _default_business()

    customer = customer or {}
    styles = _styles()
    buffer = BytesIO()
    doc = _build_doc(buffer)
    cw = _content_width()
    story = []

    # --- Header ---
    _build_header(story, styles, business, "PAYMENT RECEIPT", invoice.get("invoice_number", ""))

    # --- Receipt info (two columns) ---
    left_col = []
    left_col.append(Paragraph("RECEIVED FROM", styles["section_heading"]))
    left_col.extend(_customer_block(customer, styles))

    right_col = []
    right_col.extend(_info_pair("Receipt Date", _fmt_date_long(payment.get("payment_date")), styles))
    right_col.append(Spacer(1, 2 * mm))
    right_col.extend(_info_pair(
        "Invoice Reference",
        invoice.get("invoice_number", ""),
        styles,
    ))

    info_table = Table(
        [[left_col, right_col]],
        colWidths=[cw * 0.5, cw * 0.5],
    )
    info_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 6 * mm))

    # --- Payment confirmation box ---
    story.append(Paragraph("PAYMENT DETAILS", styles["section_heading"]))
    story.append(Spacer(1, 2 * mm))

    detail_data = [
        [
            Paragraph("Amount Paid", styles["table_header"]),
            Paragraph("Method", styles["table_header"]),
            Paragraph("Reference", styles["table_header"]),
            Paragraph("Date", styles["table_header"]),
        ],
        [
            Paragraph(_fmt(payment.get("amount_cents")), styles["table_cell_bold"]),
            Paragraph((payment.get("method") or "").title(), styles["table_cell"]),
            Paragraph(payment.get("reference", "") or "", styles["table_cell"]),
            Paragraph(_fmt_date(payment.get("payment_date")), styles["table_cell"]),
        ],
    ]
    detail_table = Table(
        detail_data,
        colWidths=[cw * 0.25, cw * 0.22, cw * 0.31, cw * 0.22],
    )
    detail_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), DARK_SLATE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("LINEBELOW", (0, 0), (-1, 0), 1, DARK_SLATE),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 1), (-1, 1), LIGHT_GREY),
        ("LINEBELOW", (0, 1), (-1, 1), 0.5, MID_GREY),
    ]))
    story.append(detail_table)
    story.append(Spacer(1, 6 * mm))

    # --- Invoice summary ---
    story.append(Paragraph("INVOICE SUMMARY", styles["section_heading"]))
    story.append(Spacer(1, 2 * mm))

    invoice_total = invoice.get("total_cents", 0) or 0
    total_paid = invoice.get("paid_cents", 0) or 0
    balance = invoice_total - total_paid

    summary_data = [
        [
            Paragraph("Invoice Total", styles["body"]),
            Paragraph(_fmt(invoice_total), styles["body_bold_right"]),
        ],
        [
            Paragraph("Total Paid", styles["body"]),
            Paragraph(_fmt(total_paid), ParagraphStyle(
                "paid_green", parent=styles["body_bold_right"], textColor=GREEN)),
        ],
        [
            Paragraph("BALANCE REMAINING", styles["total_label"]),
            Paragraph(_fmt(balance), styles["total_value_orange"]),
        ],
    ]
    summary_table = Table(summary_data, colWidths=[cw * 0.70, cw * 0.30])
    summary_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEABOVE", (0, -1), (-1, -1), 1, DARK_SLATE),
        ("TOPPADDING", (0, -1), (-1, -1), 6),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 6),
        ("BACKGROUND", (0, 0), (-1, 1), LIGHT_GREY),
        ("LINEBELOW", (0, 0), (-1, 0), 0.25, MID_GREY),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 8 * mm))

    # --- Thank you note ---
    thank_you_style = ParagraphStyle(
        "thank_you",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=BRAND_ORANGE,
        alignment=TA_CENTER,
    )
    story.append(Paragraph("Thank you for your payment!", thank_you_style))
    story.append(Spacer(1, 4 * mm))

    # Build
    biz_copy = dict(business)
    doc.build(
        story,
        onFirstPage=lambda c, d: _add_footer(c, d, biz_copy),
        onLaterPages=lambda c, d: _add_footer(c, d, biz_copy),
    )
    return buffer.getvalue()
