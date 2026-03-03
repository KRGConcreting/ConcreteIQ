"""
Settings routes — Application settings and integrations management.
"""

import json
import logging
import shutil
from pathlib import Path
from datetime import datetime
from urllib.parse import quote as url_quote

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, Request, Query, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.auth import require_login, verify_csrf
from app.core.templates import templates
from app.config import settings
from app.settings import service as settings_service
from app.integrations.xero import get_xero_connection_status

try:
    from app.integrations.google_calendar import NSW_PUBLIC_HOLIDAYS
except ImportError:
    NSW_PUBLIC_HOLIDAYS = {}

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])

# Base paths for static files
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# File upload settings
MAX_LOGO_SIZE = 2 * 1024 * 1024  # 2MB
MAX_PDF_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_LOGO_TYPES = {"image/png", "image/jpeg", "image/svg+xml"}
ALLOWED_LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg"}
ALLOWED_PDF_TYPES = {"application/pdf"}
ALLOWED_PDF_EXTENSIONS = {".pdf"}


def _get_all_template_map():
    """Return a mapping of all editable template types to their file paths."""
    return {
        # PDF templates (5)
        "quote_pdf": TEMPLATES_DIR / "pdf" / "quote.html",
        "invoice_pdf": TEMPLATES_DIR / "pdf" / "invoice.html",
        "receipt_pdf": TEMPLATES_DIR / "pdf" / "receipt.html",
        # Email templates (15)
        "quote_sent": TEMPLATES_DIR / "emails" / "quote_sent.html",
        "amendment_sent": TEMPLATES_DIR / "emails" / "amendment_sent.html",
        "booking_confirmed": TEMPLATES_DIR / "emails" / "booking_confirmed.html",
        "invoice_sent": TEMPLATES_DIR / "emails" / "invoice_sent.html",
        "payment_receipt": TEMPLATES_DIR / "emails" / "payment_receipt.html",
        "job_reminder": TEMPLATES_DIR / "emails" / "job_reminder.html",
        "job_complete": TEMPLATES_DIR / "emails" / "job_complete.html",
        "progress_update": TEMPLATES_DIR / "emails" / "progress_update.html",
        "quote_followup": TEMPLATES_DIR / "emails" / "quote_followup.html",
        "quote_expiry_warning": TEMPLATES_DIR / "emails" / "quote_expiry_warning.html",
        "payment_reminder": TEMPLATES_DIR / "emails" / "payment_reminder.html",
        "payment_reminder_friendly": TEMPLATES_DIR / "emails" / "payment_reminder_friendly.html",
        "payment_reminder_firm": TEMPLATES_DIR / "emails" / "payment_reminder_firm.html",
        "payment_reminder_final": TEMPLATES_DIR / "emails" / "payment_reminder_final.html",
        "review_request": TEMPLATES_DIR / "emails" / "review_request.html",
        # Portal templates (8)
        "portal_quote": TEMPLATES_DIR / "portal" / "quote.html",
        "portal_accept": TEMPLATES_DIR / "portal" / "accept.html",
        "portal_select_date": TEMPLATES_DIR / "portal" / "select_date.html",
        "portal_success": TEMPLATES_DIR / "portal" / "success.html",
        "portal_expired": TEMPLATES_DIR / "portal" / "expired.html",
        "portal_amendment": TEMPLATES_DIR / "portal" / "amendment.html",
        "portal_invoice": TEMPLATES_DIR / "portal" / "invoice.html",
        "portal_dashboard": TEMPLATES_DIR / "portal" / "dashboard.html",
    }


# Template display names for the editor modal
TEMPLATE_DISPLAY_NAMES = {
    "quote_pdf": "Quote PDF",
    "invoice_pdf": "Invoice PDF",
    "receipt_pdf": "Payment Receipt PDF",
    "quote_sent": "Quote Sent Email",
    "amendment_sent": "Amendment Sent Email",
    "booking_confirmed": "Booking Confirmed Email",
    "invoice_sent": "Invoice Sent Email",
    "payment_receipt": "Payment Receipt Email",
    "job_reminder": "Job Reminder Email",
    "job_complete": "Job Complete Email",
    "progress_update": "Progress Update Email",
    "quote_followup": "Quote Follow-up Email",
    "quote_expiry_warning": "Quote Expiry Warning Email",
    "payment_reminder": "Payment Reminder Email",
    "payment_reminder_friendly": "Friendly Reminder Email",
    "payment_reminder_firm": "Firm Reminder Email",
    "payment_reminder_final": "Final Notice Email",
    "review_request": "Review Request Email",
    # Portal pages
    "portal_quote": "Portal: Quote View",
    "portal_accept": "Portal: Accept & Sign",
    "portal_select_date": "Portal: Date Selection",
    "portal_success": "Portal: Success Page",
    "portal_expired": "Portal: Quote Expired",
    "portal_amendment": "Portal: Amendment View",
    "portal_invoice": "Portal: Invoice View",
    "portal_dashboard": "Portal: Customer Dashboard",
}


# =============================================================================
# SETTINGS ROOT - Redirect to first section
# =============================================================================

@router.get("", name="settings:index")
async def settings_index():
    """Redirect /settings to /settings/pricing."""
    return RedirectResponse(url="/settings/pricing", status_code=302)


# =============================================================================
# PRICING SCHEDULE
# =============================================================================

@router.get("/pricing", name="settings:pricing")
async def settings_pricing(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Pricing Schedule settings page."""
    pricing = await settings_service.get_settings_by_category(db, 'pricing')
    return templates.TemplateResponse("settings/pricing.html", {
        "request": request,
        "pricing": pricing,
        "active_section": "pricing",
    })


# =============================================================================
# CREW PRODUCTIVITY REFERENCE
# =============================================================================

@router.get("/productivity", name="settings:productivity")
async def settings_productivity(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Crew Productivity reference page — quick-reference rates with estimator."""
    pricing = await settings_service.get_settings_by_category(db, 'pricing')
    return templates.TemplateResponse("settings/productivity.html", {
        "request": request,
        "pricing": pricing,
        "active_section": "productivity",
    })


# =============================================================================
# BUSINESS DETAILS
# =============================================================================

@router.get("/business", name="settings:business")
async def settings_business(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Business Details settings page."""
    business = await settings_service.get_settings_by_category(db, 'business')
    goals = await settings_service.get_settings_by_category(db, 'goals')
    return templates.TemplateResponse("settings/business.html", {
        "request": request,
        "business": business,
        "goals": goals,
        "active_section": "business",
    })


# =============================================================================
# EMAIL CONFIGURATION
# =============================================================================

@router.get("/email", name="settings:email")
async def settings_email(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Email Configuration settings page."""
    email = await settings_service.get_settings_by_category(db, 'email')
    # Check if Resend is configured via DB or environment
    pm_db_key = await settings_service.get_setting(db, 'integrations', 'resend_api_key')
    resend_configured = bool(pm_db_key or settings.resend_api_key)
    return templates.TemplateResponse("settings/email.html", {
        "request": request,
        "email": email,
        "resend_configured": resend_configured,
        "active_section": "email",
    })


# =============================================================================
# SMS CONFIGURATION
# =============================================================================

@router.get("/sms", name="settings:sms")
async def settings_sms(request: Request):
    """Redirect to Integrations — SMS is now configured there."""
    return RedirectResponse(url="/settings/integrations", status_code=302)


# =============================================================================
# QUOTATION SETTINGS
# =============================================================================

@router.get("/quotation", name="settings:quotation")
async def settings_quotation(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Quotation Settings page."""
    quotation = await settings_service.get_settings_by_category(db, 'quotation')
    return templates.TemplateResponse("settings/quotation.html", {
        "request": request,
        "quotation": quotation,
        "active_section": "quotation",
    })


# =============================================================================
# INVOICE SETTINGS
# =============================================================================

@router.get("/invoice", name="settings:invoice")
async def settings_invoice(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Invoice Settings page."""
    invoice = await settings_service.get_settings_by_category(db, 'invoice')
    return templates.TemplateResponse("settings/invoice.html", {
        "request": request,
        "invoice": invoice,
        "active_section": "invoice",
    })


# =============================================================================
# INTEGRATIONS (existing)
# =============================================================================

@router.get("/integrations", name="settings:integrations")
async def integrations_page(
    request: Request,
    xero: str = Query(None, description="Xero connection status message"),
    db: AsyncSession = Depends(get_db),
):
    """
    Integrations settings page.

    Shows status and configuration for all external integrations:
    - Xero (accounting)
    - Google Calendar
    - Resend (email)
    - Stripe (payments)
    """
    # Get Xero connection status
    xero_status = await get_xero_connection_status(db)

    # Check which integrations are configured (DB overrides env var)
    integration_settings = await settings_service.get_settings_by_category(db, 'integrations')
    xero_configured = bool(
        (integration_settings.get('xero_client_id') or settings.xero_client_id) and
        (integration_settings.get('xero_client_secret') or settings.xero_client_secret)
    )
    gcal_configured = bool(
        (integration_settings.get('google_credentials_json') or settings.google_credentials_json) and
        (integration_settings.get('google_calendar_id') or settings.google_calendar_id)
    )
    resend_configured = bool(
        integration_settings.get('resend_api_key') or settings.resend_api_key
    )
    stripe_configured = bool(
        (integration_settings.get('stripe_secret_key') or settings.stripe_secret_key) and
        (integration_settings.get('stripe_publishable_key') or settings.stripe_publishable_key)
    )

    # Check Google Maps/Places configuration
    google_maps_configured = bool(
        integration_settings.get('google_places_api_key') or settings.google_places_api_key
    )

    # Check Vonage SMS configuration
    sms_settings = await settings_service.get_settings_by_category(db, 'sms')
    vonage_configured = bool(
        sms_settings.get('vonage_api_key') and
        sms_settings.get('vonage_api_secret')
    )

    # Build masked credential values so users see that credentials are saved
    # Shows "••••" + last 4 chars for IDs, full mask for secrets
    def _mask(val: str, show_last: int = 4) -> str:
        if not val:
            return ""
        if len(val) <= show_last:
            return "••••" + val
        return "••••" + val[-show_last:]

    def _mask_secret(val: str) -> str:
        return "••••••••••••" if val else ""

    masked_credentials = {
        "xero_client_id": _mask(integration_settings.get('xero_client_id') or settings.xero_client_id or ""),
        "xero_client_secret": _mask_secret(integration_settings.get('xero_client_secret') or settings.xero_client_secret or ""),
        "google_calendar_id": _mask(integration_settings.get('google_calendar_id') or settings.google_calendar_id or "", show_last=8),
        "google_credentials_json": _mask_secret(integration_settings.get('google_credentials_json') or settings.google_credentials_json or ""),
        "stripe_publishable_key": _mask(integration_settings.get('stripe_publishable_key') or ""),
        "stripe_secret_key": _mask_secret(integration_settings.get('stripe_secret_key') or settings.stripe_secret_key or ""),
        "stripe_webhook_secret": _mask_secret(integration_settings.get('stripe_webhook_secret') or settings.stripe_webhook_secret or ""),
        "resend_api_key": _mask_secret(integration_settings.get('resend_api_key') or settings.resend_api_key or ""),
        "vonage_api_key": _mask(sms_settings.get('vonage_api_key') or ""),
        "vonage_api_secret": _mask_secret(sms_settings.get('vonage_api_secret') or ""),
        "vonage_from_number": sms_settings.get('vonage_from_number') or "",  # Phone numbers aren't secret
        "google_places_api_key": _mask_secret(integration_settings.get('google_places_api_key') or settings.google_places_api_key or ""),
    }

    response = templates.TemplateResponse("settings/integrations.html", {
        "request": request,
        "xero_status": xero_status,
        "xero_configured": xero_configured,
        "xero_connected": xero == "connected",
        "gcal_configured": gcal_configured,
        "resend_configured": resend_configured,
        "stripe_configured": stripe_configured,
        "vonage_configured": vonage_configured,
        "google_maps_configured": google_maps_configured,
        "sms_enabled": sms_settings.get('enabled', False),
        "holiday_years": sorted(NSW_PUBLIC_HOLIDAYS.keys()),
        "masked_credentials": masked_credentials,
        "active_section": "integrations",
    })
    # Prevent browser caching so credential status is always fresh
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


# =============================================================================
# NOTIFICATIONS / REMINDERS
# =============================================================================

@router.get("/notifications", name="settings:notifications")
async def settings_notifications(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Notification & Reminder Settings page."""
    reminders = await settings_service.get_settings_by_category(db, 'reminders')
    return templates.TemplateResponse("settings/notifications.html", {
        "request": request,
        "reminders": reminders,
        "active_section": "notifications",
    })


# =============================================================================
# DOCUMENTS & PDFS
# =============================================================================

@router.get("/documents", name="settings:documents")
async def settings_documents(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Documents & PDFs settings page."""
    quotation = await settings_service.get_settings_by_category(db, 'quotation')
    return templates.TemplateResponse("settings/documents.html", {
        "request": request,
        "quotation": quotation,
        "active_section": "documents",
    })


# =============================================================================
# CREW MANAGEMENT - Redirect to workers
# =============================================================================

@router.get("/crew", name="settings:crew")
async def settings_crew():
    """Crew Management - redirect to workers page."""
    return RedirectResponse(url="/workers", status_code=302)


# =============================================================================
# API ENDPOINTS - Save settings
# =============================================================================

@router.post("/api/pricing", name="settings:api:pricing")
async def save_pricing(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Save pricing settings."""
    data = await request.json()
    await settings_service.set_settings_bulk(db, 'pricing', data)
    return {"status": "ok", "message": "Pricing settings saved"}


@router.post("/api/business", name="settings:api:business")
async def save_business(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Save business details."""
    data = await request.json()
    await settings_service.set_settings_bulk(db, 'business', data)
    return {"status": "ok", "message": "Business details saved"}


@router.post("/api/sms", name="settings:api:sms")
async def save_sms(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Save SMS settings."""
    data = await request.json()
    await settings_service.set_settings_bulk(db, 'sms', data)
    return {"status": "ok", "message": "SMS settings saved"}


@router.post("/api/email", name="settings:api:email")
async def save_email(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Save email settings."""
    data = await request.json()
    await settings_service.set_settings_bulk(db, 'email', data)
    return {"status": "ok", "message": "Email settings saved"}


@router.post("/api/quotation", name="settings:api:quotation")
async def save_quotation(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Save quotation settings."""
    data = await request.json()
    await settings_service.set_settings_bulk(db, 'quotation', data)
    return {"status": "ok", "message": "Quotation settings saved"}


@router.post("/api/invoice", name="settings:api:invoice")
async def save_invoice(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Save invoice settings."""
    data = await request.json()
    await settings_service.set_settings_bulk(db, 'invoice', data)
    return {"status": "ok", "message": "Invoice settings saved"}


@router.post("/api/notifications", name="settings:api:notifications")
async def save_notifications(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Save notification settings."""
    data = await request.json()
    await settings_service.set_settings_bulk(db, 'reminders', data)
    return {"status": "ok", "message": "Notification settings saved"}


@router.post("/api/goals", name="settings:api:goals")
async def save_goals(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Save earnings goal settings."""
    data = await request.json()
    await settings_service.set_settings_bulk(db, 'goals', data)
    return {"status": "ok", "message": "Goals settings saved"}


# =============================================================================
# API ENDPOINTS - Test functions
# =============================================================================

@router.post("/api/sms/test", name="settings:api:sms:test")
async def test_sms(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Send a test SMS."""
    from app.notifications.sms import send_test_sms

    data = await request.json()
    phone_number = data.get('phone_number')

    if not phone_number:
        raise HTTPException(status_code=400, detail="Phone number required")

    result = await send_test_sms(db, phone_number)
    return result


@router.post("/api/email/test", name="settings:api:email:test")
async def test_email(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Send a test email."""
    from app.notifications.email import send_email

    data = await request.json()
    email_address = data.get('email_address')

    if not email_address:
        raise HTTPException(status_code=400, detail="Email address required")

    success = await send_email(
        to=email_address,
        subject="Test Email from ConcreteIQ",
        html_body="""
        <h2>Test Email</h2>
        <p>If you received this email, your email configuration is working correctly!</p>
        <p>Sent from ConcreteIQ</p>
        """,
        text_body="Test Email\n\nIf you received this email, your email configuration is working correctly!\n\nSent from ConcreteIQ",
        db=db,
    )

    if success:
        return {"success": True, "message": "Test email sent"}

    # Return the actual Resend error so the user can fix the issue
    detail = getattr(send_email, '_last_error', None) or "Unknown error"
    return {"success": False, "error": f"Failed to send email: {detail}"}


@router.get("/api/email/preview/{template_name}", name="settings:api:email:preview")
async def preview_email_template(template_name: str, request: Request):
    """Render an email template with sample data for preview."""

    template_map = {
        "quote_sent": "emails/quote_sent.html",
        "amendment_sent": "emails/amendment_sent.html",
        "booking_confirmed": "emails/booking_confirmed.html",
        "invoice_sent": "emails/invoice_sent.html",
        "payment_receipt": "emails/payment_receipt.html",
        "job_reminder": "emails/job_reminder.html",
        "job_complete": "emails/job_complete.html",
        "progress_update": "emails/progress_update.html",
        "quote_followup": "emails/quote_followup.html",
        "quote_expiry_warning": "emails/quote_expiry_warning.html",
        "payment_reminder": "emails/payment_reminder.html",
        "payment_reminder_friendly": "emails/payment_reminder_friendly.html",
        "payment_reminder_firm": "emails/payment_reminder_firm.html",
        "payment_reminder_final": "emails/payment_reminder_final.html",
        "review_request": "emails/review_request.html",
        "job_rescheduled": "emails/job_rescheduled.html",
        "sealer_followup": "emails/sealer_followup.html",
    }

    if template_name not in template_map:
        raise HTTPException(status_code=404, detail="Template not found")

    # Sample objects that mimic the real ORM models used in templates
    class SampleQuote:
        quote_number = "Q-2026-00042"
        job_name = "Exposed Aggregate Driveway"
        job_address = "15 Sample Street, Albury NSW 2640"
        subtotal_cents = 750000
        gst_cents = 75000
        discount_cents = 0
        expiry_date = None  # Will show fallback text

    class SampleCustomer:
        name = "John Smith"

    class SampleInvoice:
        invoice_number = "INV-2026-00018"
        subtotal_cents = 750000
        gst_cents = 75000
        description = "Booking deposit — Exposed Aggregate Driveway"
        paid_cents = 247500

    class SampleAmendment:
        amendment_number = "1"
        description = "Added extra 5m\u00b2 to driveway area and upgraded to premium exposed aggregate finish."
        amount_cents = 85000

    class SamplePayment:
        method = "stripe"
        reference = "pi_3ABC123def456"

    class SamplePaymentScheduleItem:
        def __init__(self, name, amount_cents):
            self.name = name
            self.amount_cents = amount_cents

    sample = {
        "request": request,
        # Business details
        "business_name": "KRG Concreting",
        "business_phone": "0423 005 129",
        "business_email": "admin@krgconcreting.com.au",
        "business_abn": "76 993 685 401",
        "business_address": "1/32 Whitton Drive, Thurgoona NSW 2640",
        # Logos
        "logo_url": "/static/images/KyleRGyoles_Concreting_Logo.png",
        "ciq_logo_url": "/static/images/ConcreteIQ_Logo_Nav.png",
        # Object references used by templates
        "quote": SampleQuote(),
        "customer": SampleCustomer(),
        "invoice": SampleInvoice(),
        "amendment": SampleAmendment(),
        "payment": SamplePayment(),
        # Formatted values
        "total_formatted": "$8,250.00",
        "paid_formatted": "$2,475.00",
        "balance_formatted": "$5,775.00",
        "amount_formatted": "$2,475.00",
        "original_total_formatted": "$8,250.00",
        "variation_formatted": "+$850.00",
        "adjusted_total_formatted": "$9,100.00",
        # Dates
        "start_date_formatted": "Monday 10 March 2026",
        "due_date_formatted": "15 March 2026",
        "payment_date_formatted": "27 February 2026",
        "job_date_formatted": "Monday 10 March 2026",
        "expiry_date_formatted": "15 March 2026",
        # Status/conditional fields
        "stage_label": "Deposit (30%)",
        "time_description": "tomorrow",
        "days_remaining": 3,
        "days_overdue": 7,
        "is_overdue": True,
        "balance_cents": 577500,
        # URLs
        "portal_url": "#",
        "portal_link": "#",
        "invoice_url": "#",
        "review_url": "#",
        # Bank details
        "bank_name": "Great Southern Bank",
        "bank_bsb": "834472",
        "bank_account": "491424410",
        "bank_account_name": "KYLE RICKY GYOLES",
        # Booking confirmed: payment schedule
        "payments": [
            SamplePaymentScheduleItem("Deposit (30%)", 247500),
            SamplePaymentScheduleItem("Pre-pour (60%)", 495000),
            SamplePaymentScheduleItem("Final (10%)", 82500),
        ],
        "first_payment_paid": True,
        # Progress update fields
        "update_title": "Formwork & Steel Complete",
        "update_message": "Hi John,\n\nJust a quick update on your driveway project. We've completed the formwork and steel reinforcement today. Everything is looking great and we're on track for the concrete pour tomorrow morning.\n\nWe'll arrive around 6:30am with the concrete truck. Please ensure the area is clear and accessible.\n\nCheers,\nKRG Concreting",
        "photos": [],
        # Job rescheduled fields
        "old_date_formatted": "Monday 10 March 2026",
        "new_date_formatted": "Thursday 13 March 2026",
        "reason": "Due to forecast rain on Monday, we've moved your pour date to Thursday to ensure the best result.",
        # Sealer followup fields
        "years_since": 3,
        "completed_date_formatted": "March 2023",
    }

    # Load portfolio photos for quote_sent and invoice_sent previews
    if template_name in ("quote_sent", "invoice_sent"):
        try:
            from app.documents.service import list_portfolio_photos
            raw_photos = list_portfolio_photos()
            sample["portfolio_photos"] = [
                {"url": p["url"], "title": p.get("title", "")}
                for p in raw_photos[:3]
            ]
        except Exception:
            sample["portfolio_photos"] = []
    else:
        sample["portfolio_photos"] = []

    # Add custom_intro and custom_cta defaults for preview
    preview_defaults = {
        "quote_sent": {"custom_intro": "Here is your quote for review. You can view the full details and accept online using the button below.", "custom_cta": "View Quote & Accept Online"},
        "amendment_sent": {"custom_intro": "There's been a change to the scope of your project. Please review the details below and let us know if you'd like to proceed.", "custom_cta": "Review & Respond"},
        "booking_confirmed": {"custom_intro": "Your start date is locked in. Here are the details for your upcoming job.", "custom_cta": "View Invoice & Payment Details"},
        "invoice_sent": {"custom_intro": "Please find your invoice below. Payment can be made by bank transfer using the details provided.", "custom_cta": "View Invoice & Payment Details"},
        "payment_receipt": {"custom_intro": "Thank you for your payment! This email confirms we have received your payment."},
        "payment_reminder": {"custom_intro": "This is a friendly reminder that your payment for invoice INV-2026-00018 is coming up soon.", "custom_cta": "View Invoice & Payment Details"},
        "payment_reminder_friendly": {"custom_intro": "Just a heads up \u2014 your payment for invoice INV-2026-00018 is coming up soon. We're sure it's just slipped through!", "custom_cta": "View Invoice & Payment Details"},
        "payment_reminder_firm": {"custom_intro": "Your payment for invoice INV-2026-00018 is now 7 days overdue. Please arrange payment as soon as possible.", "custom_cta": "View Invoice & Arrange Payment"},
        "payment_reminder_final": {"custom_intro": "Despite previous reminders, invoice INV-2026-00018 remains unpaid and is now 7 days overdue. Immediate payment is required.", "custom_cta": "View Invoice & Arrange Payment"},
        "job_reminder": {"custom_intro": "Just a friendly reminder that your concreting job is scheduled for tomorrow."},
        "job_complete": {"custom_intro": "Great news \u2014 your concreting project is now complete! We hope you're happy with the result.", "custom_cta": "View Invoice & Pay Balance"},
        "job_rescheduled": {"custom_intro": "Your job has been rescheduled to a new date. Here are the updated details."},
        "progress_update": {"custom_intro": "Here's an update on your concreting project:"},
        "quote_followup": {"custom_intro": "We sent you a quote recently and wanted to follow up. Your quote is still available to view and accept online.", "custom_cta": "View Your Quote"},
        "quote_expiry_warning": {"custom_intro": "Your quote from KRG Concreting expires on 15 March 2026. Lock in your current price by accepting online before this date.", "custom_cta": "View & Accept Your Quote"},
        "review_request": {"custom_intro": "Thank you for choosing KRG Concreting for your recent concreting project! We hope you're happy with the finished result.", "custom_cta": "Leave a Google Review"},
        "sealer_followup": {"custom_intro": "It's been about 3 years since we completed your concreting job. The sealer on your concrete is approaching the end of its lifespan.", "custom_cta": "Call 0423 005 129"},
    }
    sample.update(preview_defaults.get(template_name, {}))

    try:
        response = templates.TemplateResponse(template_map[template_name], sample)
        # Allow this page to be loaded in an iframe (preview modal)
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'self';"
        return response
    except Exception:
        return HTMLResponse(
            "<html><body style='font-family: sans-serif; padding: 40px; text-align: center;'>"
            "<h2>Template Preview Error</h2>"
            "<p>This template may require additional variables not available in preview mode.</p>"
            "</body></html>"
        )


# =============================================================================
# API ENDPOINTS - Reset settings
# =============================================================================

@router.post("/api/reset/{category}", name="settings:api:reset")
async def reset_settings(
    category: str,
    db: AsyncSession = Depends(get_db),
):
    """Reset a settings category to defaults."""
    valid_categories = ['pricing', 'business', 'sms', 'email', 'quotation', 'invoice', 'reminders', 'labour']

    if category not in valid_categories:
        raise HTTPException(status_code=400, detail=f"Invalid category. Must be one of: {', '.join(valid_categories)}")

    count = await settings_service.reset_category(db, category)
    return {"status": "ok", "message": f"Reset {count} settings to defaults"}


# =============================================================================
# INTEGRATION CREDENTIALS
# =============================================================================

@router.post("/integrations/{service}/credentials", name="settings:integrations:credentials")
async def save_integration_credentials(
    service: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Save API credentials for an integration.

    Supports: xero, google, stripe, resend, vonage
    """
    form = await request.form()

    # Define which keys each service uses
    credential_keys = {
        "xero": ["xero_client_id", "xero_client_secret"],
        "google": ["google_calendar_id", "google_credentials_json"],
        "stripe": ["stripe_publishable_key", "stripe_secret_key", "stripe_webhook_secret"],
        "resend": ["resend_api_key"],
        "vonage": ["vonage_api_key", "vonage_api_secret", "vonage_from_number"],
        "google_maps": ["google_places_api_key"],
    }

    if service not in credential_keys:
        raise HTTPException(400, f"Unknown service: {service}")

    # Determine category for storing credentials
    category_map = {
        "xero": "integrations",
        "google": "integrations",
        "stripe": "integrations",
        "resend": "integrations",
        "vonage": "sms",
        "google_maps": "integrations",
    }
    category = category_map.get(service, "integrations")

    # Save each credential
    saved_count = 0
    for key in credential_keys[service]:
        raw_value = form.get(key)
        value = str(raw_value).strip() if raw_value else ""
        if value and not value.startswith("••••"):  # Don't save masked values
            await settings_service.set_setting(db, category, key, value)
            saved_count += 1
            logger.info(f"Saved credential {category}.{key} ({len(value)} chars)")

    if saved_count == 0:
        return JSONResponse(
            status_code=400,
            content={"success": False, "detail": "No new credentials provided. Clear the field and enter your actual API key."},
        )

    await db.commit()
    logger.info(f"Committed {saved_count} credentials for {service}")

    # Verify the save persisted
    verify = await settings_service.get_settings_by_category(db, category)
    for key in credential_keys[service]:
        if verify.get(key):
            logger.info(f"Verified {category}.{key} persisted OK")
        else:
            logger.warning(f"WARNING: {category}.{key} NOT found after commit!")

    return {"success": True, "message": f"{service.title()} credentials saved", "saved": saved_count}


# =============================================================================
# FILE UPLOAD ENDPOINTS
# =============================================================================

@router.post("/api/upload/logo", name="settings:api:upload:logo")
async def upload_logo(
    file: UploadFile = File(...),
):
    """
    Upload a new company logo.

    Accepts PNG, JPG, or SVG files up to 2MB.
    Replaces the existing company logo at /static/images/KyleRGyoles_Concreting_Logo.png
    """
    # Validate file extension
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_LOGO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(ALLOWED_LOGO_EXTENSIONS)}"
        )

    # Validate content type
    if file.content_type not in ALLOWED_LOGO_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid content type: {file.content_type}"
        )

    # Read and validate size
    content = await file.read()
    if len(content) > MAX_LOGO_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {MAX_LOGO_SIZE // (1024*1024)}MB"
        )

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    # Save logo (always save as PNG for consistency)
    logo_path = STATIC_DIR / "images" / "KyleRGyoles_Concreting_Logo.png"

    # Create backup of existing logo
    if logo_path.exists():
        backup_path = STATIC_DIR / "images" / "KyleRGyoles_Concreting_Logo_backup.png"
        shutil.copy(logo_path, backup_path)

    # Write new logo
    with open(logo_path, "wb") as f:
        f.write(content)

    return {
        "success": True,
        "message": "Logo uploaded successfully",
        "path": "/static/images/KyleRGyoles_Concreting_Logo.png"
    }


@router.post("/api/upload/terms-pdf", name="settings:api:upload:terms")
async def upload_terms_pdf(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload Terms & Conditions PDF.

    Accepts PDF files up to 10MB.
    Saves to /static/ and updates the quotation.terms_pdf_path setting.
    """
    # Validate file extension
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_PDF_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Only PDF files are allowed."
        )

    # Validate content type
    if file.content_type not in ALLOWED_PDF_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid content type: {file.content_type}"
        )

    # Read and validate size
    content = await file.read()
    if len(content) > MAX_PDF_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {MAX_PDF_SIZE // (1024*1024)}MB"
        )

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    # Generate filename — save into tcs/ subfolder
    tcs_dir = STATIC_DIR / "documents" / "tcs"
    tcs_dir.mkdir(parents=True, exist_ok=True)

    pdf_filename = "KRG_Terms_and_Conditions.pdf"
    pdf_path = tcs_dir / pdf_filename

    # Create backup of existing file
    if pdf_path.exists():
        backup_path = tcs_dir / "KRG_Terms_and_Conditions_backup.pdf"
        shutil.copy(pdf_path, backup_path)

    # Write new PDF
    with open(pdf_path, "wb") as f:
        f.write(content)

    # Update setting
    await settings_service.set_setting(db, 'quotation', 'terms_pdf_path', f"/static/documents/tcs/{pdf_filename}")

    return {
        "success": True,
        "message": "Terms & Conditions PDF uploaded successfully",
        "path": f"/static/{pdf_filename}"
    }


@router.get("/api/template/{template_type}", name="settings:api:template:get")
async def get_template_content(
    template_type: str,
):
    """
    Get the content of a template file for editing.

    Valid template_type values:
    - PDF templates: quote_pdf, invoice_pdf, receipt_pdf
    - Email templates: quote_sent, amendment_sent, booking_confirmed, invoice_sent,
      payment_receipt, job_reminder, job_complete, progress_update,
      quote_followup, quote_expiry_warning, payment_reminder,
      payment_reminder_friendly, payment_reminder_firm, payment_reminder_final,
      review_request
    """
    template_map = _get_all_template_map()

    if template_type not in template_map:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid template type. Valid options: {', '.join(template_map.keys())}"
        )

    template_path = template_map[template_type]
    if not template_path.exists():
        raise HTTPException(status_code=404, detail="Template file not found")

    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()

    return {
        "template_type": template_type,
        "path": str(template_path.relative_to(BASE_DIR)),
        "content": content
    }


@router.post("/api/template/{template_type}", name="settings:api:template:save")
async def save_template_content(
    template_type: str,
    request: Request,
):
    """
    Save updated template content.

    Valid template_type values:
    - PDF templates: quote_pdf, invoice_pdf, receipt_pdf
    - Email templates: all 15 email templates
    """
    template_map = _get_all_template_map()

    if template_type not in template_map:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid template type. Valid options: {', '.join(template_map.keys())}"
        )

    template_path = template_map[template_type]

    # Get content from request body
    data = await request.json()
    content = data.get("content")

    if not content:
        raise HTTPException(status_code=400, detail="No content provided")

    # Create backup of existing template
    if template_path.exists():
        backup_path = template_path.with_suffix(".html.backup")
        shutil.copy(template_path, backup_path)

    # Write new content
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(content)

    return {
        "success": True,
        "message": f"Template '{template_type}' saved successfully"
    }


@router.get("/preview/template/{template_type}", name="settings:preview:template")
async def preview_template(
    template_type: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Preview a template with sample data.

    Valid template_type values:
    - quote_sent, invoice_sent, payment_reminder, payment_receipt
    """
    # Map template types to file paths (only email templates for now)
    template_map = {
        "quote_sent": "emails/quote_sent.html",
        "invoice_sent": "emails/invoice_sent.html",
        "payment_reminder": "emails/payment_reminder.html",
        "payment_receipt": "emails/payment_receipt.html",
    }

    if template_type not in template_map:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid template type for preview. Valid options: {', '.join(template_map.keys())}"
        )

    # Get business settings for sample data
    business = await settings_service.get_settings_by_category(db, 'business')

    business_name = business.get("trading_as") or business.get("name") or "KRG Concreting"
    business_phone = business.get("phone") or "0423 005 129"
    business_email = business.get("email") or "kyle@krgconcreting.au"

    # Create sample objects that match template expectations
    class SampleQuote:
        quote_number = "Q-2026-00042"
        job_name = "Driveway & Path"
        job_address = "123 Example Street, Albury NSW 2640"
        subtotal_cents = 1500000  # $15,000
        discount_cents = 0
        gst_cents = 150000  # $1,500
        total_cents = 1650000  # $16,500

    class SampleInvoice:
        invoice_number = "INV-2026-00018"
        job_name = "Driveway & Path"
        job_address = "123 Example Street, Albury NSW 2640"
        description = "Stage 2 - Driveway concrete pour"
        subtotal_cents = 525000  # $5,250
        gst_cents = 52500  # $525
        total_cents = 577500  # $5,775
        amount_due_cents = 577500
        paid_cents = 0  # No partial payment for preview
        due_date = "15 Feb 2026"

    class SampleCustomer:
        name = "John Smith"
        email = "john.smith@example.com"
        phone = "0412 345 678"

    class SamplePayment:
        method = "card"
        reference = "pi_abc123xyz"
        amount_cents = 525000

    # Sample data for preview - includes both flat vars and object vars
    sample_data = {
        "request": request,
        # Object-based (used by email templates)
        "quote": SampleQuote(),
        "invoice": SampleInvoice(),
        "customer": SampleCustomer(),
        "payment": SamplePayment(),
        # Flat variables (used by some templates)
        "customer_name": "John Smith",
        "customer_email": "john.smith@example.com",
        "quote_number": "Q-2026-00042",
        "quote_total": 16500.00,
        "quote_total_formatted": "$16,500.00",
        "total_formatted": "$16,500.00",
        "quote_url": "#",
        "portal_url": "#",
        "invoice_number": "INV-2026-00018",
        "invoice_total": 5775.00,
        "invoice_total_formatted": "$5,775.00",
        "invoice_due_date": "15 Feb 2026",
        "amount_due": 5775.00,
        "amount_due_cents": 577500,
        "amount_due_formatted": "$5,775.00",
        "payment_amount": 5250.00,
        "payment_amount_formatted": "$5,250.00",
        "payment_date": "1 Feb 2026",
        "days_overdue": 7,
        "is_overdue": True,
        "business_name": business_name,
        "business_phone": business_phone,
        "business_email": business_email,
        "job_address": "123 Example Street, Albury NSW 2640",
        "job_suburb": "Albury",
        # Payment reminder specific
        "paid_formatted": "$0.00",
        "balance_formatted": "$5,775.00",
        "due_date_formatted": "15 Feb 2026",
        "bank_name": "Great Southern Bank",
        "bank_bsb": "834472",
        "bank_account": "491424410",
        "bank_account_name": "KYLE RICKY GYOLES",
        # Payment receipt specific
        "amount_formatted": "$5,250.00",
        "payment_date_formatted": "1 Feb 2026",
        "balance_cents": 52500,  # Remaining balance after payment
        "total_formatted": "$5,775.00",
    }

    try:
        return templates.TemplateResponse(template_map[template_type], sample_data)
    except Exception as e:
        # Return a helpful error instead of 500
        return templates.TemplateResponse("settings/preview_error.html", {
            "request": request,
            "template_type": template_type,
            "error": str(e),
        })


@router.get("/preview/pdf/{template_type}", name="settings:preview:pdf")
async def preview_pdf_template(
    template_type: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Preview a PDF template with sample data.

    Valid template_type values:
    - quote, invoice, receipt
    """
    from datetime import date as date_type
    from types import SimpleNamespace

    pdf_template_map = {
        "quote": "pdf/quote.html",
        "invoice": "pdf/invoice.html",
        "receipt": "pdf/receipt.html",
    }

    if template_type not in pdf_template_map:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid PDF template type. Valid options: {', '.join(pdf_template_map.keys())}"
        )

    # Get business settings for sample data
    business = await settings_service.get_settings_by_category(db, 'business')

    business_name = business.get("trading_as") or business.get("name") or "KRG Concreting"
    business_phone = business.get("phone") or "0423 005 129"
    business_email = business.get("email") or "kyle@krgconcreting.au"
    business_address = business.get("address") or "1/32 Whitton Drive, Thurgoona NSW 2640"
    business_abn = business.get("abn") or "76 993 685 401"

    # Create sample objects that match what the PDF templates expect
    sample_business = SimpleNamespace(
        name=business.get("name") or "KRG Concreting",
        trading_as=business.get("trading_as") or business_name,
        phone=business_phone,
        email=business_email,
        address=business_address,
        abn=business_abn,
        bank_name=business.get("bank_name") or "Great Southern Bank",
        bank_account_name=business.get("bank_account_name") or "",
        bank_bsb=business.get("bank_bsb") or "",
        bsb=business.get("bank_bsb") or "",
        bank_account=business.get("bank_account") or "",
        account=business.get("bank_account") or "",
    )

    sample_customer = SimpleNamespace(
        name="John Smith",
        email="john.smith@example.com",
        phone="0412 345 678",
        address="456 Customer Ave, Wodonga VIC 3690",
        street="456 Customer Ave",
        city="Wodonga",
        state="VIC",
        postcode="3690",
    )

    sample_line_items = [
        {"description": "Concrete Driveway - 50sqm", "quantity": 1, "unit": "job", "total_cents": 750000},
        {"description": "Exposed Aggregate Finish", "quantity": 50, "unit": "sqm", "total_cents": 225000},
        {"description": "Formwork & Preparation", "quantity": 1, "unit": "job", "total_cents": 150000},
    ]

    sample_quote = SimpleNamespace(
        id=42,
        quote_number="Q-2026-00042",
        site_address="123 Example Street, Albury NSW 2640",
        job_name="Driveway & Path Concrete",
        slab_area=50,
        slab_thickness=100,
        volume_m3=5.0,
        concrete_grade="N25",
        reinforcement="SL82 Mesh",
        concrete_cost_cents=275000,
        labour_sell_cents=600000,
        setup_labour_cents=600000,
        setup_hours=4,
        pour_hours=6,
        subtotal_cents=1125000,
        gst_cents=112500,
        total_cents=1237500,
        discount_cents=0,
        line_items=sample_line_items,
        customer_line_items=[],
        payments=None,
        expiry_date=date_type(2026, 3, 3),
        signed_at=None,
    )

    sample_invoice = SimpleNamespace(
        id=18,
        invoice_number="INV-2026-00018",
        description="Driveway & Path Concrete - Completion Payment",
        issue_date=date_type(2026, 2, 1),
        due_date=date_type(2026, 2, 15),
        subtotal_cents=1125000,
        gst_cents=112500,
        total_cents=1237500,
        paid_cents=371250,
        balance_cents=866250,
        line_items=sample_line_items,
        status="sent",
        notes=None,
    )

    # Logo URIs — browser preview uses /static/ web paths (file:// URIs are blocked by browsers)
    logo_uri = "/static/images/KyleRGyoles_Concreting_Logo.png"
    ciq_logo_uri = "/static/images/ConcreteIQ_Logo_Nav.png"

    # Build context based on template type
    sample_context = {
        "request": request,
        "business": sample_business,
        "customer": sample_customer,
        "generated_at": "01 Feb 2026",
        "is_preview": True,
        "logo_uri": logo_uri,
        "ciq_logo_uri": ciq_logo_uri,
    }

    if template_type == "quote":
        sample_context["quote"] = sample_quote
    elif template_type == "invoice":
        sample_context["invoice"] = sample_invoice
        sample_context["payment_terms_days"] = 14
        sample_context["late_fee_percent"] = 2
    elif template_type == "receipt":
        sample_payment = SimpleNamespace(
            reference="PAY-2026-00012",
            amount_cents=618750,
            payment_date=date_type(2026, 2, 10),
            method="stripe",
        )
        sample_context["invoice"] = sample_invoice
        sample_context["payment"] = sample_payment

    try:
        return templates.TemplateResponse(pdf_template_map[template_type], sample_context)
    except Exception as e:
        # Return a helpful error
        return templates.TemplateResponse("settings/preview_error.html", {
            "request": request,
            "template_type": f"PDF: {template_type}",
            "error": str(e),
        })


# =============================================================================
# PORTAL PAGE PREVIEWS
# =============================================================================

@router.get("/preview/portal/{page_type}", name="settings:preview:portal")
async def preview_portal_page(
    page_type: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Preview a customer-facing portal page with sample data."""
    from types import SimpleNamespace
    from datetime import date, datetime

    portal_template_map = {
        "quote": "portal/quote.html",
        "accept": "portal/accept.html",
        "select_date": "portal/select_date.html",
        "success": "portal/success.html",
        "expired": "portal/expired.html",
        "amendment": "portal/amendment.html",
        "invoice": "portal/invoice.html",
        "dashboard": "portal/dashboard.html",
    }

    if page_type not in portal_template_map:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid portal page type. Valid options: {', '.join(portal_template_map.keys())}"
        )

    # Get business settings
    business = await settings_service.get_settings_by_category(db, 'business')
    quotation = await settings_service.get_settings_by_category(db, 'quotation')

    business_name = business.get("trading_as") or business.get("name") or "KRG Concreting"
    business_phone = business.get("phone") or "0423 005 129"
    business_email = business.get("email") or "kyle@krgconcreting.au"
    business_abn = business.get("abn") or "76 993 685 401"

    business_dict = {
        "name": business.get("name") or "KRG Concreting",
        "trading_as": business_name,
        "phone": business_phone,
        "email": business_email,
        "abn": business_abn,
        "bank_name": business.get("bank_name") or "Great Southern Bank",
        "bank_account_name": business.get("bank_account_name") or "KYLE RICKY GYOLES",
        "bank_bsb": business.get("bank_bsb") or "834472",
        "bsb": business.get("bank_bsb") or "834472",
        "bank_account": business.get("bank_account") or "491424410",
        "account": business.get("bank_account") or "491424410",
    }

    terms_pdf_path = quotation.get('terms_pdf_path') or '/static/documents/tcs/KRG_Terms_and_Conditions_v3.1.pdf'

    # Sample customer
    sample_customer = SimpleNamespace(
        id=1,
        name="John Smith",
        email="john.smith@example.com",
        phone="0412 345 678",
        address="45 Example Street",
        city="Albury",
        state="NSW",
        postcode="2640",
        portal_access_token="sample-dashboard-token",
    )

    # Sample quote
    sample_quote = SimpleNamespace(
        id=1,
        quote_number="Q-2026-00042",
        job_name="Driveway & Path",
        job_address="45 Example Street, Albury NSW 2640",
        job_suburb="Albury",
        status="sent",
        quote_date=date(2026, 2, 15),
        expiry_date=date(2026, 3, 15),
        total_cents=825000,
        subtotal_cents=750000,
        gst_cents=75000,
        customer_id=1,
        signed_at=None,
        signature_name=None,
        discount_cents=0,
        confirmed_start_date=None,
        requested_start_date=None,
        customer_line_items=[
            SimpleNamespace(
                category="Concrete Driveway",
                total_cents=600000,
                show_sub_prices=True,
                sub_items=[
                    SimpleNamespace(description="Excavation & preparation", price_cents=120000),
                    SimpleNamespace(description="Formwork & steel reinforcement", price_cents=180000),
                    SimpleNamespace(description="Concrete supply & pour (25MPa)", price_cents=200000),
                    SimpleNamespace(description="Broom finish", price_cents=100000),
                ]
            ),
            SimpleNamespace(
                category="Garden Path",
                total_cents=150000,
                show_sub_prices=True,
                sub_items=[
                    SimpleNamespace(description="Excavation & base prep", price_cents=50000),
                    SimpleNamespace(description="Concrete supply & pour", price_cents=60000),
                    SimpleNamespace(description="Exposed aggregate finish", price_cents=40000),
                ]
            ),
        ],
        line_items=[],
        calculator_result={
            "payments": [
                {"name": "Deposit (30%)", "amount_cents": 247500, "percent": 0.30},
                {"name": "Pre-pour (60%)", "amount_cents": 495000, "percent": 0.60},
                {"name": "Final (10%)", "amount_cents": 82500, "percent": 0.10},
            ]
        },
        notes="Includes removal of existing pavers. Weather dependent scheduling.",
        scope_of_work="Supply and install new concrete driveway with broom finish and exposed aggregate garden path.",
        inclusions="All labour, materials, concrete supply, formwork, and finishing.",
        exclusions="Landscaping, fencing, plumbing or electrical works.",
        created_at=datetime(2026, 2, 15, 10, 0, 0),
    )

    # Sample amendment
    sample_amendment = SimpleNamespace(
        id=1,
        amendment_number="A-001",
        quote_id=1,
        title="Add Retaining Wall",
        description="Customer requested a small retaining wall along the driveway edge to manage the slope. Includes excavation, formwork, steel reinforcement, and concrete pour with smooth finish.",
        amount_cents=185000,
        status="sent",
        signature_name=None,
        accepted_at=None,
        declined_at=None,
        decline_reason=None,
        created_at=datetime(2026, 2, 20, 14, 30, 0),
    )

    # Sample invoice
    sample_invoice = SimpleNamespace(
        id=1,
        invoice_number="INV-2026-00018",
        quote_id=1,
        customer_id=1,
        status="sent",
        total_cents=247500,
        subtotal_cents=225000,
        gst_cents=22500,
        paid_cents=0,
        issue_date=date(2026, 2, 16),
        due_date=date(2026, 3, 1),
        paid_date=None,
        description="Deposit (30%) — Driveway & Path",
        notes="Payment due before works commence.",
        line_items=[
            SimpleNamespace(description="Deposit — Driveway & Path (30%)", quantity=1, unit="", total_cents=225000),
        ],
        created_at=datetime(2026, 2, 16, 9, 0, 0),
    )

    # Sample payments list
    sample_payments = [
        SimpleNamespace(
            id=1,
            invoice_id=1,
            amount_cents=247500,
            method="stripe",
            reference="pi_3ABC123",
            created_at=datetime(2026, 2, 17, 11, 30, 0),
        ),
    ]

    # Build context based on page type
    sample_context = {
        "request": request,
        "is_preview": True,
    }

    if page_type == "quote":
        sample_context.update({
            "quote": sample_quote,
            "customer": sample_customer,
            "business": business_dict,
            "today": date.today(),
            "token": "preview-sample-token",
            "terms_pdf_path": terms_pdf_path,
        })

    elif page_type == "accept":
        sample_context.update({
            "quote": sample_quote,
            "customer": sample_customer,
            "token": "preview-sample-token",
            "deposit_amount": 2475.00,
            "deposit_percent": 30,
            "terms_pdf_path": terms_pdf_path,
            "business": business_dict,
        })

    elif page_type == "select_date":
        sample_quote.signed_at = datetime(2026, 2, 18, 14, 0, 0)
        sample_quote.signature_name = "John Smith"
        sample_context.update({
            "quote": sample_quote,
            "customer": sample_customer,
            "token": "preview-sample-token",
            "busy_dates": [
                date(2026, 3, 10).isoformat(),
                date(2026, 3, 11).isoformat(),
                date(2026, 3, 12).isoformat(),
                date(2026, 3, 17).isoformat(),
                date(2026, 3, 18).isoformat(),
            ],
            "deposit_amount": 2475.00,
            "business": business_dict,
        })

    elif page_type == "success":
        sample_quote.signed_at = datetime(2026, 2, 18, 14, 0, 0)
        sample_quote.requested_start_date = date(2026, 3, 24)
        sample_context.update({
            "quote": sample_quote,
            "customer": sample_customer,
            "token": "preview-sample-token",
            "deposit_amount": 2475.00,
            "business": business_dict,
        })

    elif page_type == "expired":
        sample_quote.status = "expired"
        sample_quote.expiry_date = date(2026, 2, 1)
        sample_context.update({
            "quote": sample_quote,
            "customer": sample_customer,
            "business": business_dict,
        })

    elif page_type == "amendment":
        sample_context.update({
            "amendment": sample_amendment,
            "quote": sample_quote,
            "customer": sample_customer,
            "token": "preview-sample-token",
            "original_total": 825000,
            "variation_amount": 185000,
            "adjusted_total": 1010000,
            "business": business_dict,
        })

    elif page_type == "invoice":
        sample_context.update({
            "invoice": sample_invoice,
            "customer": sample_customer,
            "payments": [],
            "balance_cents": 247500,
            "business": business_dict,
            "token": "preview-sample-token",
            "stripe_enabled": False,
            "stripe_publishable_key": "",
        })

    elif page_type == "dashboard":
        sample_quote2 = SimpleNamespace(
            id=2,
            quote_number="Q-2026-00038",
            job_name="Patio Slab",
            job_address="45 Example Street, Albury NSW 2640",
            status="completed",
            quote_date=date(2025, 11, 5),
            total_cents=420000,
            customer_id=1,
            created_at=datetime(2025, 11, 5, 10, 0, 0),
        )
        sample_invoice2 = SimpleNamespace(
            id=2,
            invoice_number="INV-2025-00012",
            quote_id=2,
            customer_id=1,
            status="paid",
            total_cents=420000,
            paid_cents=420000,
            due_date=date(2025, 12, 1),
            description="Full payment",
            created_at=datetime(2025, 11, 10, 9, 0, 0),
        )
        sample_context.update({
            "customer": sample_customer,
            "quotes": [sample_quote, sample_quote2],
            "invoices": [sample_invoice, sample_invoice2],
            "payments": sample_payments,
            "invoice_map": {1: sample_invoice, 2: sample_invoice2},
            "total_quoted_cents": 1245000,
            "total_invoiced_cents": 667500,
            "total_paid_cents": 420000,
            "outstanding_cents": 247500,
            "today": date.today(),
            "business": business_dict,
        })

    try:
        response = templates.TemplateResponse(portal_template_map[page_type], sample_context)
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'self';"
        return response
    except Exception as e:
        return templates.TemplateResponse("settings/preview_error.html", {
            "request": request,
            "template_type": f"Portal: {page_type}",
            "error": str(e),
        })


# =============================================================================
# SECURITY (2FA / TOTP)
# =============================================================================

@router.get("/security", name="settings:security")
async def settings_security(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Security settings page — password, sessions, 2FA."""
    from app.core.totp import is_totp_enabled

    totp_enabled = await is_totp_enabled(db)

    # Get recovery code count for display
    recovery_count = 0
    if totp_enabled:
        from app.core.totp import get_recovery_code_count
        recovery_count = await get_recovery_code_count(db)

    return templates.TemplateResponse("settings/security.html", {
        "request": request,
        "active_section": "security",
        "totp_enabled": totp_enabled,
        "setup_mode": False,
        "password_error": None,
        "password_success": None,
        "recovery_code_count": recovery_count,
    })


@router.post("/security/password", name="settings:change_password")
async def change_password(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Change admin password."""
    from app.core.totp import is_totp_enabled
    from app.core.auth import verify_password
    import bcrypt

    form = await request.form()
    current_password = form.get("current_password", "")
    new_password = form.get("new_password", "")
    confirm_password = form.get("confirm_password", "")

    totp_enabled = await is_totp_enabled(db)

    # Get recovery code count for template context
    recovery_count = 0
    if totp_enabled:
        from app.core.totp import get_recovery_code_count
        recovery_count = await get_recovery_code_count(db)

    base_ctx = {
        "request": request,
        "active_section": "security",
        "totp_enabled": totp_enabled,
        "setup_mode": False,
        "password_error": None,
        "password_success": None,
        "recovery_code_count": recovery_count,
    }

    # Validate required fields
    if not current_password or not new_password:
        base_ctx["password_error"] = "All fields are required."
        return templates.TemplateResponse("settings/security.html", base_ctx)

    # Get the current effective password hash (DB override first, then env var)
    db_hash = await settings_service.get_setting(db, "security", "admin_password_hash")
    effective_hash = db_hash if db_hash else settings.admin_password

    # Verify current password
    if not verify_password(current_password, effective_hash):
        base_ctx["password_error"] = "Current password is incorrect."
        return templates.TemplateResponse("settings/security.html", base_ctx)

    if new_password != confirm_password:
        base_ctx["password_error"] = "New passwords do not match."
        return templates.TemplateResponse("settings/security.html", base_ctx)

    if len(new_password) < 8:
        base_ctx["password_error"] = "Password must be at least 8 characters."
        return templates.TemplateResponse("settings/security.html", base_ctx)

    # Hash and store in database (overrides env var)
    new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    await settings_service.set_setting(db, "security", "admin_password_hash", new_hash)
    await db.commit()

    base_ctx["password_success"] = "Password changed successfully."
    return templates.TemplateResponse("settings/security.html", base_ctx)


@router.post("/security/revoke-sessions", name="settings:revoke_sessions")
async def revoke_all_sessions(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Invalidate all active sessions by incrementing session version."""
    current_version = await settings_service.get_setting(db, "security", "session_version")
    current_int = int(current_version) if current_version else 1
    await settings_service.set_setting(db, "security", "session_version", str(current_int + 1))
    await db.commit()

    # Clear current user's session and redirect to login
    from app.core.auth import clear_session
    response = RedirectResponse("/login", status_code=302)
    clear_session(response)
    return response


@router.post("/security/totp/setup", name="settings:totp_setup")
async def totp_setup(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Generate TOTP secret and QR code for setup."""
    from app.core.totp import setup_totp, is_totp_enabled

    if await is_totp_enabled(db):
        return RedirectResponse(url="/settings/security", status_code=302)

    secret, qr_base64, uri = await setup_totp(db)

    return templates.TemplateResponse("settings/security.html", {
        "request": request,
        "active_section": "security",
        "totp_enabled": False,
        "setup_mode": True,
        "qr_code": qr_base64,
        "totp_secret": secret,
        "password_error": None,
        "password_success": None,
        "recovery_code_count": 0,
        "error": None,
    })


@router.post("/security/totp/enable", name="settings:totp_enable")
async def totp_enable(
    request: Request,
    totp_secret: str = Form(...),
    totp_code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Verify code and enable TOTP."""
    from app.core.totp import enable_totp, get_totp_uri, generate_qr_code_base64

    success = await enable_totp(db, totp_secret, totp_code.strip())

    if success:
        # Generate recovery codes
        from app.core.totp import generate_recovery_codes, store_recovery_codes
        plain_codes = generate_recovery_codes()
        await store_recovery_codes(db, plain_codes)
        await db.commit()

        return templates.TemplateResponse("settings/security.html", {
            "request": request,
            "active_section": "security",
            "totp_enabled": True,
            "setup_mode": False,
            "show_recovery_codes": True,
            "recovery_codes": plain_codes,
            "recovery_code_count": len(plain_codes),
            "password_error": None,
            "password_success": None,
            "success": "Two-factor authentication has been enabled. Save your recovery codes below!",
        })
    else:
        # Code was wrong — show QR again
        uri = get_totp_uri(totp_secret)
        qr_base64 = generate_qr_code_base64(uri)
        return templates.TemplateResponse("settings/security.html", {
            "request": request,
            "active_section": "security",
            "totp_enabled": False,
            "setup_mode": True,
            "qr_code": qr_base64,
            "totp_secret": totp_secret,
            "password_error": None,
            "password_success": None,
            "recovery_code_count": 0,
            "error": "Invalid code. Scan the QR code again and enter the current 6-digit code.",
        })


@router.post("/security/totp/disable", name="settings:totp_disable")
async def totp_disable(
    request: Request,
    totp_code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Disable TOTP after verifying current code."""
    from app.core.totp import disable_totp

    success = await disable_totp(db, totp_code.strip())

    if success:
        # Also clear recovery codes when disabling 2FA
        from app.core.totp import clear_recovery_codes
        await clear_recovery_codes(db)
        await db.commit()
        return templates.TemplateResponse("settings/security.html", {
            "request": request,
            "active_section": "security",
            "totp_enabled": False,
            "setup_mode": False,
            "password_error": None,
            "password_success": None,
            "recovery_code_count": 0,
            "success": "Two-factor authentication has been disabled.",
        })
    else:
        from app.core.totp import get_recovery_code_count
        recovery_count = await get_recovery_code_count(db)
        return templates.TemplateResponse("settings/security.html", {
            "request": request,
            "active_section": "security",
            "totp_enabled": True,
            "setup_mode": False,
            "password_error": None,
            "password_success": None,
            "recovery_code_count": recovery_count,
            "error": "Invalid code. Enter the current 6-digit code from your authenticator app.",
        })


# =============================================================================
# GOOGLE REVIEWS
# =============================================================================

@router.get("/reviews", name="settings:reviews")
async def settings_reviews(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Google Reviews automation settings page."""
    reviews = await settings_service.get_settings_by_category(db, 'reviews')
    return templates.TemplateResponse("settings/reviews.html", {
        "request": request,
        "reviews": reviews,
        "google_review_url": settings.google_review_url or reviews.get('google_review_url', ''),
        "active_section": "reviews",
    })


@router.post("/reviews", name="settings:reviews:save")
async def save_reviews_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Save Google Reviews settings."""
    form = await request.form()

    await settings_service.save_setting(db, 'reviews', 'enabled', str(form.get('enabled', 'false')).lower() == 'true' or 'enabled' in dict(form), 'bool')
    await settings_service.save_setting(db, 'reviews', 'google_review_url', form.get('google_review_url', ''), 'string')
    await settings_service.save_setting(db, 'reviews', 'delay_days', form.get('delay_days', '1'), 'int')

    await db.commit()

    from app.core.templates import flash
    flash(request, "Reviews settings saved", "success")
    return RedirectResponse(url="/settings/reviews", status_code=303)


# =============================================================================
# SMS TEMPLATES
# =============================================================================

@router.get("/sms-templates", name="settings:sms_templates")
async def settings_sms_templates(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """SMS template settings page."""
    sms_templates = await settings_service.get_settings_by_category(db, 'sms_templates')
    return templates.TemplateResponse("settings/sms_templates.html", {
        "request": request,
        "sms_templates": sms_templates,
        "active_section": "sms_templates",
    })


@router.post("/sms-templates", name="settings:sms_templates:save")
async def save_sms_templates(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Save SMS template settings."""
    form = await request.form()

    # Quotes & Jobs
    await settings_service.save_setting(db, 'sms_templates', 'quote_sent', form.get('quote_sent', ''), 'string')
    await settings_service.save_setting(db, 'sms_templates', 'quote_followup', form.get('quote_followup', ''), 'string')
    await settings_service.save_setting(db, 'sms_templates', 'amendment_sent', form.get('amendment_sent', ''), 'string')
    # Scheduling
    await settings_service.save_setting(db, 'sms_templates', 'day_before_reminder', form.get('day_before_reminder', ''), 'string')
    await settings_service.save_setting(db, 'sms_templates', 'job_reminder', form.get('job_reminder', ''), 'string')
    await settings_service.save_setting(db, 'sms_templates', 'on_my_way', form.get('on_my_way', ''), 'string')
    await settings_service.save_setting(db, 'sms_templates', 'on_my_way_eta', form.get('on_my_way_eta', ''), 'string')
    # Invoicing & Payments
    await settings_service.save_setting(db, 'sms_templates', 'invoice_sent', form.get('invoice_sent', ''), 'string')
    await settings_service.save_setting(db, 'sms_templates', 'payment_reminder', form.get('payment_reminder', ''), 'string')
    await settings_service.save_setting(db, 'sms_templates', 'payment_reminder_overdue', form.get('payment_reminder_overdue', ''), 'string')
    # Post-Job
    await settings_service.save_setting(db, 'sms_templates', 'job_complete', form.get('job_complete', ''), 'string')
    await settings_service.save_setting(db, 'sms_templates', 'progress_update', form.get('progress_update', ''), 'string')
    await settings_service.save_setting(db, 'sms_templates', 'review_request', form.get('review_request', ''), 'string')

    await db.commit()

    from app.core.templates import flash
    flash(request, "SMS templates saved", "success")
    return RedirectResponse(url="/settings/sms-templates", status_code=303)


# =============================================================================
# EMAIL & PDF TEMPLATES GALLERY
# =============================================================================

@router.get("/email-templates", name="settings:email_templates")
async def settings_email_templates(request: Request, db: AsyncSession = Depends(get_db)):
    """Email & PDF template customisation page."""
    # Load saved email template customisations
    email_customizations = await settings_service.get_settings_by_category(db, 'email_templates')

    email_templates_quotes = [
        {"key": "quote_sent", "name": "Quote Sent", "description": "New quote notification"},
        {"key": "amendment_sent", "name": "Variation Sent", "description": "Amendment/variation for review"},
        {"key": "booking_confirmed", "name": "Booking Confirmed", "description": "Job booked & deposit received"},
        {"key": "quote_followup", "name": "Quote Follow-up", "description": "Reminder about unanswered quote"},
        {"key": "quote_expiry_warning", "name": "Quote Expiry Warning", "description": "Quote about to expire"},
    ]
    email_templates_payments = [
        {"key": "invoice_sent", "name": "Invoice Sent", "description": "New invoice notification"},
        {"key": "payment_receipt", "name": "Payment Receipt", "description": "Confirmation of payment received"},
        {"key": "payment_reminder", "name": "Payment Reminder", "description": "Standard payment reminder"},
        {"key": "payment_reminder_friendly", "name": "Friendly Reminder", "description": "Gentle payment nudge"},
        {"key": "payment_reminder_firm", "name": "Firm Reminder", "description": "Firmer overdue notice"},
        {"key": "payment_reminder_final", "name": "Final Notice", "description": "Last payment reminder"},
    ]
    email_templates_jobs = [
        {"key": "job_reminder", "name": "Job Reminder", "description": "Upcoming job reminder with checklist"},
        {"key": "job_complete", "name": "Job Complete", "description": "Job finished notification"},
        {"key": "job_rescheduled", "name": "Job Rescheduled", "description": "Date change notification"},
        {"key": "progress_update", "name": "Progress Update", "description": "Job progress with photos"},
        {"key": "review_request", "name": "Review Request", "description": "Google review request"},
        {"key": "sealer_followup", "name": "Sealer Follow-up", "description": "Maintenance reminder after years"},
    ]
    pdf_templates = [
        {"key": "quote", "name": "Quote PDF", "description": "Detailed quote document"},
        {"key": "invoice", "name": "Invoice PDF", "description": "Tax invoice document"},
        {"key": "receipt", "name": "Receipt PDF", "description": "Payment receipt document"},
    ]

    return templates.TemplateResponse("settings/email_templates.html", {
        "request": request,
        "email_templates_quotes": email_templates_quotes,
        "email_templates_payments": email_templates_payments,
        "email_templates_jobs": email_templates_jobs,
        "pdf_templates": pdf_templates,
        "email_customizations": email_customizations,
        "active_section": "email_templates",
    })


@router.post("/email-templates", name="settings:save_email_templates")
async def save_email_templates(request: Request, db: AsyncSession = Depends(get_db)):
    """Save email template customisations."""
    form = await request.form()

    # All template keys
    template_keys = [
        'quote_sent', 'amendment_sent', 'booking_confirmed', 'quote_followup',
        'quote_expiry_warning', 'invoice_sent', 'payment_receipt', 'payment_reminder',
        'payment_reminder_friendly', 'payment_reminder_firm', 'payment_reminder_final',
        'job_reminder', 'job_complete', 'job_rescheduled', 'progress_update',
        'review_request', 'sealer_followup',
    ]

    # Templates that have CTA buttons
    templates_with_cta = {
        'quote_sent', 'amendment_sent', 'booking_confirmed', 'quote_followup',
        'quote_expiry_warning', 'invoice_sent', 'payment_reminder',
        'payment_reminder_friendly', 'payment_reminder_firm', 'payment_reminder_final',
        'job_complete', 'review_request', 'sealer_followup',
    }

    for key in template_keys:
        # Save subject
        await settings_service.set_setting(
            db, 'email_templates', f'{key}_subject',
            form.get(f'{key}_subject', '')
        )
        # Save intro
        await settings_service.set_setting(
            db, 'email_templates', f'{key}_intro',
            form.get(f'{key}_intro', '')
        )
        # Save CTA (only for templates that have one)
        if key in templates_with_cta:
            await settings_service.set_setting(
                db, 'email_templates', f'{key}_cta',
                form.get(f'{key}_cta', '')
            )

    await db.commit()

    return RedirectResponse(
        url="/settings/email-templates?saved=1",
        status_code=303,
    )


# =============================================================================
# DATABASE BACKUP
# =============================================================================

@router.get("/backup", name="settings:backup_page")
async def backup_page(request: Request):
    """Backup & Restore management page."""
    import logging
    logger = logging.getLogger(__name__)

    backups = []
    try:
        backup_dir = BASE_DIR / "backups"
        backup_dir.mkdir(exist_ok=True)

        for f in sorted(backup_dir.iterdir(), reverse=True):
            if f.is_file() and f.suffix in (".db", ".sql", ".json"):
                stat = f.stat()
                backups.append({
                    "filename": f.name,
                    "size_mb": round(stat.st_size / (1024 * 1024), 1),
                    "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%d %b %Y %H:%M"),
                })
    except Exception as e:
        logger.warning(f"Could not list backups: {e}")

    return templates.TemplateResponse("settings/backup.html", {
        "request": request,
        "active_section": "backup",
        "backups": backups[:20],
    })


@router.get("/backup/download", name="settings:backup_download")
async def download_backup(request: Request):
    """
    Download a database backup.

    For SQLite: copies the .db file.
    For PostgreSQL: runs pg_dump and streams the result.
    """
    import logging
    from datetime import datetime
    from app.database import is_sqlite
    from app.core.dates import sydney_now
    from fastapi.responses import FileResponse

    logger = logging.getLogger(__name__)

    timestamp = sydney_now().strftime("%Y%m%d_%H%M%S")

    if is_sqlite:
        # SQLite — just copy the file
        db_path = Path(settings.database_url.replace("sqlite+aiosqlite:///", "")).resolve()
        if not db_path.exists():
            raise HTTPException(status_code=500, detail="Database file not found")

        backup_dir = BASE_DIR / "backups"
        backup_dir.mkdir(exist_ok=True)
        backup_path = backup_dir / f"concreteiq_backup_{timestamp}.db"

        # Copy with WAL checkpoint for consistency
        import sqlite3
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(backup_path))
        src.backup(dst)
        dst.close()
        src.close()

        logger.info(f"Database backup created: {backup_path}")

        return FileResponse(
            path=str(backup_path),
            filename=f"concreteiq_backup_{timestamp}.db",
            media_type="application/octet-stream",
        )
    else:
        # PostgreSQL — use pg_dump
        import subprocess
        import tempfile

        backup_file = tempfile.NamedTemporaryFile(
            suffix=".sql", prefix=f"concreteiq_backup_{timestamp}_", delete=False
        )
        backup_file.close()

        # Extract connection URL for pg_dump
        db_url = settings.database_url
        if db_url.startswith("postgresql+asyncpg://"):
            db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

        try:
            result = subprocess.run(
                ["pg_dump", db_url, "-f", backup_file.name],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=f"pg_dump failed: {result.stderr}")
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="pg_dump not found. Install PostgreSQL client tools.")
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=500, detail="Database backup timed out")

        from fastapi.responses import FileResponse
        return FileResponse(
            path=backup_file.name,
            filename=f"concreteiq_backup_{timestamp}.sql",
            media_type="application/sql",
        )


@router.post("/backup/restore", name="settings:backup_restore")
async def restore_backup(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Restore a database from an uploaded backup file.

    Only supports SQLite .db files. Creates a backup of the current DB first.
    """
    import sqlite3
    from app.database import is_sqlite
    from app.core.dates import sydney_now
    from fastapi.responses import RedirectResponse

    if not is_sqlite:
        raise HTTPException(400, "Restore is currently only supported for SQLite databases")

    if not file.filename.endswith((".db", ".sqlite", ".sqlite3")):
        raise HTTPException(400, "Invalid file type. Upload a .db or .sqlite file.")

    # Read uploaded file
    content = await file.read()
    if len(content) > 500 * 1024 * 1024:  # 500MB max
        raise HTTPException(400, "File too large (max 500MB)")

    if len(content) < 100:
        raise HTTPException(400, "File too small to be a valid database")

    timestamp = sydney_now().strftime("%Y%m%d_%H%M%S")

    # Save uploaded file to temp location
    backup_dir = BASE_DIR / "backups"
    backup_dir.mkdir(exist_ok=True)
    upload_path = backup_dir / f"restore_upload_{timestamp}.db"
    upload_path.write_bytes(content)

    # Validate the uploaded file is actually a valid SQLite database
    try:
        test_conn = sqlite3.connect(str(upload_path))
        test_conn.execute("SELECT count(*) FROM sqlite_master")
        test_conn.close()
    except Exception:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(400, "Invalid SQLite database file")

    # Create safety backup of current database
    db_path = Path(settings.database_url.replace("sqlite+aiosqlite:///", "")).resolve()
    safety_backup = backup_dir / f"concreteiq_pre_restore_{timestamp}.db"

    try:
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(safety_backup))
        src.backup(dst)
        dst.close()
        src.close()
    except Exception as e:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(500, f"Failed to create safety backup: {e}")

    # Replace the database with the uploaded file
    try:
        shutil.copy2(str(upload_path), str(db_path))
    except Exception as e:
        # Attempt to restore from safety backup
        shutil.copy2(str(safety_backup), str(db_path))
        upload_path.unlink(missing_ok=True)
        raise HTTPException(500, f"Restore failed, original database preserved: {e}")

    # Clean up upload temp file
    upload_path.unlink(missing_ok=True)

    # Log security event
    try:
        from app.security.service import log_security_event
        await log_security_event(
            db, "backup_restored",
            f"Database restored from uploaded file: {file.filename}",
            ip_address=request.client.host if request.client else None,
        )
    except Exception:
        pass  # DB may be different now

    return RedirectResponse(url="/settings/backup?restored=1", status_code=303)


@router.get("/backup/export-json", name="settings:backup_export_json")
async def export_json(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Export all data as JSON — portable backup format.

    Exports all major tables as a single JSON file.
    """
    from sqlalchemy import select, text
    from fastapi.responses import JSONResponse
    from app.core.dates import sydney_now
    from app.models import (
        Customer, Quote, Invoice, Payment, Worker,
        Notification, Setting, ActivityLog, CommunicationLog,
    )

    timestamp = sydney_now().strftime("%Y%m%d_%H%M%S")

    export = {
        "_meta": {
            "app": "ConcreteIQ",
            "version": "1.0",
            "exported_at": sydney_now().isoformat(),
            "format": "json",
        },
        "tables": {},
    }

    # Export each table
    table_models = {
        "customers": Customer,
        "quotes": Quote,
        "invoices": Invoice,
        "payments": Payment,
        "workers": Worker,
        "settings": Setting,
    }

    for table_name, model in table_models.items():
        try:
            result = await db.execute(select(model))
            rows = result.scalars().all()
            export["tables"][table_name] = []
            for row in rows:
                row_dict = {}
                for col in row.__table__.columns:
                    val = getattr(row, col.name)
                    if isinstance(val, (datetime,)):
                        val = val.isoformat()
                    elif isinstance(val, memoryview):
                        val = None  # Skip binary data
                    elif hasattr(val, 'isoformat'):
                        val = val.isoformat()
                    row_dict[col.name] = val
                export["tables"][table_name].append(row_dict)
        except Exception as e:
            export["tables"][table_name] = {"error": str(e)}

    # Log security event
    try:
        from app.security.service import log_security_event
        await log_security_event(
            db, "backup_downloaded",
            f"JSON export downloaded ({len(export['tables'])} tables)",
            ip_address=request.client.host if request.client else None,
        )
        await db.commit()
    except Exception:
        pass

    from starlette.responses import Response
    json_bytes = json.dumps(export, indent=2, default=str).encode("utf-8")
    return Response(
        content=json_bytes,
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{url_quote(f'concreteiq_export_{timestamp}.json')}",
        },
    )


# =============================================================================
# DATA MANAGEMENT
# =============================================================================

@router.get("/data-management", name="settings:data_management")
async def data_management_page(request: Request):
    """Data management page — reset test data."""
    return templates.TemplateResponse("settings/data-management.html", {
        "request": request,
        "active_section": "data_management",
    })


@router.post("/reset-data")
async def reset_test_data(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Delete ALL test/transactional data from the database.

    Preserves system configuration: settings, workers, suppliers,
    OAuth tokens, Xero mappings, earnings snapshots.

    Resets quote/invoice number sequences back to 1.

    Requires confirmation token to prevent accidental use.
    """
    try:
        form = await request.form()
    except Exception:
        raise HTTPException(400, "Invalid request")

    confirm = form.get("confirm_text", "").strip().upper()
    if confirm != "RESET":
        return JSONResponse({"success": False, "detail": "Type RESET to confirm"}, status_code=400)

    from sqlalchemy import delete, update
    from app.models import (
        TimeEntry, JobAssignment, Payment, EmailLog, SMSLog,
        CommunicationLog, Notification, ActivityLog, FollowUp,
        ProgressUpdate, Reminder, PourResult, PourPlan, JobCosting,
        Expense, Photo, QuoteAmendment, Invoice, Quote, Customer,
        Sequence,
    )

    # FK-safe deletion order: leaf nodes first, root entities last
    tables_to_clear = [
        ("Time entries", TimeEntry),
        ("Job assignments", JobAssignment),
        ("Payments", Payment),
        ("Email logs", EmailLog),
        ("SMS logs", SMSLog),
        ("Communication logs", CommunicationLog),
        ("Notifications", Notification),
        ("Activity logs", ActivityLog),
        ("Follow-ups", FollowUp),
        ("Progress updates", ProgressUpdate),
        ("Reminders", Reminder),
        ("Pour results", PourResult),
        ("Pour plans", PourPlan),
        ("Job costings", JobCosting),
        ("Expenses", Expense),
        ("Photos", Photo),
        ("Quote amendments", QuoteAmendment),
        ("Invoices", Invoice),
        ("Quotes", Quote),
        ("Customers", Customer),
    ]

    total_deleted = 0
    details = []

    try:
        for label, model in tables_to_clear:
            result = await db.execute(delete(model))
            count = result.rowcount
            total_deleted += count
            if count > 0:
                details.append(f"{label}: {count}")

        # Reset sequences back to 1
        await db.execute(
            update(Sequence).values(current_value=0)
        )
        details.append("Sequences: reset to 1")

        await db.commit()

        # Log the reset action
        try:
            from app.security.service import log_security_event
            await log_security_event(
                db, "data_reset",
                f"Test data reset: {total_deleted} records deleted",
                ip_address=request.client.host if request.client else None,
            )
            await db.commit()
        except Exception:
            pass

        logger.warning(f"TEST DATA RESET: {total_deleted} records deleted across {len(details)} tables")

        return JSONResponse({
            "success": True,
            "total_deleted": total_deleted,
            "details": details,
        })

    except Exception as e:
        logger.error(f"Reset data failed: {e}")
        await db.rollback()
        return JSONResponse({"success": False, "detail": str(e)}, status_code=500)

