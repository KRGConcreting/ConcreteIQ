"""
Payment routes - Stripe checkout and manual payments.

Following the established route pattern.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import PaymentCreate, PaymentResponse, StripeCheckoutResponse
from app.core.auth import require_login, verify_csrf
from app.config import settings
from app.invoices import service as invoice_service
from app.payments import service as payment_service

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


@router.post("/api/checkout/{invoice_id}")
async def api_create_checkout(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    success_url: str = None,
    cancel_url: str = None,
) -> StripeCheckoutResponse:
    """
    Create Stripe Checkout session for invoice.

    Returns checkout_url to redirect customer to Stripe.
    """
    invoice = await invoice_service.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    if invoice.status == "voided":
        raise HTTPException(400, "Cannot pay voided invoice")

    if invoice.status == "paid":
        raise HTTPException(400, "Invoice is already paid")

    # Default URLs point back to invoice detail
    if not success_url:
        success_url = f"{settings.app_url}/invoices/{invoice_id}?payment=success"
    if not cancel_url:
        cancel_url = f"{settings.app_url}/invoices/{invoice_id}?payment=cancelled"

    try:
        result = await payment_service.create_checkout_session(
            db, invoice, success_url, cancel_url
        )
        return StripeCheckoutResponse(
            checkout_url=result["checkout_url"],
            session_id=result["session_id"],
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/record")
async def api_record_payment(
    data: PaymentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PaymentResponse:
    """
    Record a manual payment (cash, bank transfer, etc).

    For recording payments received outside of Stripe.
    """
    invoice = await invoice_service.get_invoice(db, data.invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    if invoice.status == "voided":
        raise HTTPException(400, "Cannot record payment for voided invoice")

    if invoice.status == "paid":
        raise HTTPException(400, "Invoice is already fully paid")

    # Validate payment amount doesn't exceed balance
    balance = invoice.total_cents - invoice.paid_cents
    if data.amount_cents > balance:
        raise HTTPException(400, f"Payment amount exceeds balance due (${balance/100:.2f})")

    ip_address = request.client.host if request.client else None

    payment = await invoice_service.record_payment(
        db=db,
        invoice=invoice,
        amount_cents=data.amount_cents,
        method=data.method,
        reference=data.reference,
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(payment)

    return PaymentResponse.model_validate(payment)
