"""
Quote PDF Generation using WeasyPrint.

Generates professional PDF quotes from HTML templates.

Note: WeasyPrint requires GTK/Pango system libraries.
On Windows, these must be installed separately via MSYS2.
The import is done lazily to allow the app to run without PDF support.
"""

from io import BytesIO
from typing import Optional
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.config import get_settings
from app.core.dates import sydney_now


# Project root directory (for resolving static file paths in PDFs)
PROJECT_ROOT = Path(__file__).parent.parent.parent

# Template directory
TEMPLATE_DIR = PROJECT_ROOT / "templates" / "pdf"

# Static images directory (absolute file path for WeasyPrint)
STATIC_IMAGES_DIR = PROJECT_ROOT / "static" / "images"


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
    return biz


def _logo_uri() -> str:
    """Return the file:// URI for the KRG logo so WeasyPrint can embed it."""
    logo_path = STATIC_IMAGES_DIR / "KyleRGyoles_Concreting_Logo.png"
    return logo_path.as_uri()


def _concreteiq_logo_uri() -> str:
    """Return the file:// URI for the ConcreteIQ logo."""
    logo_path = STATIC_IMAGES_DIR / "ConcreteIQ_Logo_Nav.png"
    return logo_path.as_uri()


# Lazy import flag
_weasyprint = None


def _get_weasyprint():
    """Lazy load weasyprint to handle missing system dependencies gracefully."""
    global _weasyprint
    if _weasyprint is None:
        try:
            from weasyprint import HTML
            _weasyprint = HTML
        except OSError as e:
            raise RuntimeError(
                "WeasyPrint requires GTK/Pango libraries. "
                "On Windows, install via MSYS2: https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows"
            ) from e
    return _weasyprint


def _make_env():
    """Create a Jinja2 environment with PDF filters pre-loaded."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    env.filters["currency"] = lambda cents: f"${cents/100:,.2f}" if cents else "$0.00"
    env.filters["currency_int"] = lambda cents: f"${cents/100:,.0f}" if cents else "$0"
    return env


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
    HTML = _get_weasyprint()

    if business is None:
        business = _default_business()

    env = _make_env()
    template = env.get_template("quote.html")

    html_content = template.render(
        quote=quote,
        customer=customer or {},
        business=business,
        generated_at=sydney_now().strftime("%d %B %Y"),
        logo_uri=_logo_uri(),
        ciq_logo_uri=_concreteiq_logo_uri(),
    )

    pdf_buffer = BytesIO()
    HTML(string=html_content, base_url=str(PROJECT_ROOT)).write_pdf(pdf_buffer)

    return pdf_buffer.getvalue()


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
    HTML = _get_weasyprint()

    if business is None:
        business = _default_business(include_bank=True)

    env = _make_env()
    template = env.get_template("invoice.html")

    html_content = template.render(
        invoice=invoice,
        customer=customer or {},
        business=business,
        payments=payments or [],
        generated_at=sydney_now().strftime("%d %B %Y"),
        payment_terms_days=14,
        late_fee_percent=2,
        logo_uri=_logo_uri(),
        ciq_logo_uri=_concreteiq_logo_uri(),
    )

    pdf_buffer = BytesIO()
    HTML(string=html_content, base_url=str(PROJECT_ROOT)).write_pdf(pdf_buffer)

    return pdf_buffer.getvalue()


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
    HTML = _get_weasyprint()

    if business is None:
        business = _default_business()

    env = _make_env()
    template = env.get_template("receipt.html")

    html_content = template.render(
        payment=payment,
        invoice=invoice,
        customer=customer or {},
        business=business,
        generated_at=sydney_now().strftime("%d %B %Y"),
        logo_uri=_logo_uri(),
        ciq_logo_uri=_concreteiq_logo_uri(),
    )

    pdf_buffer = BytesIO()
    HTML(string=html_content, base_url=str(PROJECT_ROOT)).write_pdf(pdf_buffer)

    return pdf_buffer.getvalue()
