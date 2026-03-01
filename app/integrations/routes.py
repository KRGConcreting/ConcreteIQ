"""
Integration routes — Xero OAuth, sync endpoints, and Google Calendar holiday sync.
"""

import secrets
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.auth import require_login, verify_csrf
from app.core.dates import sydney_now
from app.core.templates import templates
from app.models import Customer, Invoice, Payment, ActivityLog
from app.integrations.google_calendar import (
    sync_nsw_holidays,
    get_synced_holidays,
    NSW_PUBLIC_HOLIDAYS,
    sync_compliance_deadlines,
    get_synced_compliance_deadlines,
)
from app.integrations.xero import (
    get_authorization_url,
    exchange_code_for_tokens,
    get_tenant_id,
    save_xero_tokens,
    delete_xero_token,
    get_xero_connection_status,
    sync_customer_to_xero,
    sync_invoice_to_xero,
    sync_payment_to_xero,
    void_invoice_in_xero,
    bulk_sync_customers,
    bulk_sync_invoices,
    bulk_sync_payments,
    get_sync_status,
    fetch_chart_of_accounts,
    fetch_bank_accounts,
    get_account_mappings,
    save_account_mapping,
    sync_expense_to_xero,
    bulk_sync_expenses,
)
from app.models import Expense

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


# =============================================================================
# XERO OAUTH FLOW
# =============================================================================

@router.get("/xero/connect", name="integrations:xero_connect")
async def xero_connect(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Start Xero OAuth flow.

    Redirects to Xero authorization page.
    """
    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)
    request.session["xero_oauth_state"] = state

    try:
        # Read Xero client ID from DB first, then env var
        from app.integrations.xero import _get_xero_credentials
        client_id, _ = await _get_xero_credentials(db)
        auth_url = get_authorization_url(state, client_id=client_id)
        return RedirectResponse(url=auth_url, status_code=302)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/xero/callback", name="integrations:xero_callback")
async def xero_callback(
    request: Request,
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
    error_description: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Xero OAuth callback handler.

    Exchanges authorization code for tokens and stores them.
    """
    # Handle OAuth errors
    if error:
        raise HTTPException(400, f"Xero authorization failed: {error_description or error}")

    if not code:
        raise HTTPException(400, "No authorization code received")

    # Validate state (CSRF protection)
    expected_state = request.session.get("xero_oauth_state")
    if not expected_state or state != expected_state:
        raise HTTPException(400, "Invalid state parameter")

    # Clear state from session
    request.session.pop("xero_oauth_state", None)

    try:
        # Exchange code for tokens (reads credentials from DB first)
        tokens = await exchange_code_for_tokens(code, db=db)

        # Get tenant ID
        tenant_id = await get_tenant_id(tokens["access_token"])

        # Save tokens
        await save_xero_tokens(
            db,
            tokens["access_token"],
            tokens["refresh_token"],
            tokens["expires_in"],
            tenant_id,
        )

        # Log activity
        activity = ActivityLog(
            action="xero_connected",
            description="Xero integration connected",
            entity_type="integration",
            ip_address=request.client.host if request.client else None,
            extra_data={"tenant_id": tenant_id},
        )
        db.add(activity)
        await db.commit()

        # Redirect to settings page with success message
        return RedirectResponse(url="/settings/integrations?xero=connected", status_code=302)

    except Exception as e:
        raise HTTPException(400, f"Failed to complete Xero connection: {str(e)}")


@router.post("/xero/disconnect", name="integrations:xero_disconnect")
async def xero_disconnect(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Disconnect Xero integration.

    Removes stored tokens.
    """
    deleted = await delete_xero_token(db)

    if deleted:
        # Log activity
        activity = ActivityLog(
            action="xero_disconnected",
            description="Xero integration disconnected",
            entity_type="integration",
            ip_address=request.client.host if request.client else None,
        )
        db.add(activity)
        await db.commit()

    return {"success": True, "disconnected": deleted}


@router.get("/xero/status", name="integrations:xero_status")
async def xero_status(db: AsyncSession = Depends(get_db)):
    """
    Get current Xero connection status.
    """
    status = await get_xero_connection_status(db)
    return status


# =============================================================================
# MANUAL SYNC ENDPOINTS
# =============================================================================

@router.post("/xero/sync/customer/{customer_id}", name="integrations:xero_sync_customer")
async def xero_sync_customer(
    customer_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Manually sync a customer to Xero.
    """
    customer = await db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404, "Customer not found")

    xero_id = await sync_customer_to_xero(db, customer)

    if xero_id:
        # Log activity
        activity = ActivityLog(
            action="xero_customer_synced",
            description=f"Customer {customer.name} synced to Xero",
            entity_type="customer",
            entity_id=customer_id,
            ip_address=request.client.host if request.client else None,
            extra_data={"xero_contact_id": xero_id},
        )
        db.add(activity)
        await db.commit()

        return {"success": True, "xero_contact_id": xero_id}
    else:
        raise HTTPException(500, "Failed to sync customer to Xero")


@router.post("/xero/sync/invoice/{invoice_id}", name="integrations:xero_sync_invoice")
async def xero_sync_invoice(
    invoice_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Manually sync an invoice to Xero.
    """
    invoice = await db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    xero_id = await sync_invoice_to_xero(db, invoice)

    if xero_id:
        # Log activity
        activity = ActivityLog(
            action="xero_invoice_synced",
            description=f"Invoice {invoice.invoice_number} synced to Xero",
            entity_type="invoice",
            entity_id=invoice_id,
            ip_address=request.client.host if request.client else None,
            extra_data={"xero_invoice_id": xero_id},
        )
        db.add(activity)
        await db.commit()

        return {"success": True, "xero_invoice_id": xero_id}
    else:
        raise HTTPException(500, "Failed to sync invoice to Xero")


@router.post("/xero/sync/payment/{payment_id}", name="integrations:xero_sync_payment")
async def xero_sync_payment(
    payment_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Manually sync a payment to Xero.
    """
    payment = await db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Payment not found")

    xero_id = await sync_payment_to_xero(db, payment)

    if xero_id:
        # Log activity
        activity = ActivityLog(
            action="xero_payment_synced",
            description=f"Payment {payment.id} synced to Xero",
            entity_type="payment",
            entity_id=payment_id,
            ip_address=request.client.host if request.client else None,
            extra_data={"xero_payment_id": xero_id},
        )
        db.add(activity)
        await db.commit()

        return {"success": True, "xero_payment_id": xero_id}
    else:
        raise HTTPException(500, "Failed to sync payment to Xero")


# =============================================================================
# BULK SYNC ENDPOINTS
# =============================================================================

@router.post("/xero/sync/bulk/customers", name="integrations:xero_bulk_sync_customers")
async def xero_bulk_sync_customers(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Bulk sync all unsynced customers to Xero.

    Returns counts of synced, failed, and errors.
    """
    result = await bulk_sync_customers(db)

    # Log activity
    activity = ActivityLog(
        action="xero_bulk_sync_customers",
        description=f"Bulk synced {result['synced']} customers to Xero ({result['failed']} failed)",
        entity_type="integration",
        ip_address=request.client.host if request.client else None,
        extra_data=result,
    )
    db.add(activity)
    await db.commit()

    return result


@router.post("/xero/sync/bulk/invoices", name="integrations:xero_bulk_sync_invoices")
async def xero_bulk_sync_invoices(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Bulk sync all unsynced invoices to Xero.

    Only syncs non-draft, non-voided invoices.
    Returns counts of synced, failed, and errors.
    """
    result = await bulk_sync_invoices(db)

    # Log activity
    activity = ActivityLog(
        action="xero_bulk_sync_invoices",
        description=f"Bulk synced {result['synced']} invoices to Xero ({result['failed']} failed)",
        entity_type="integration",
        ip_address=request.client.host if request.client else None,
        extra_data=result,
    )
    db.add(activity)
    await db.commit()

    return result


@router.post("/xero/sync/bulk/payments", name="integrations:xero_bulk_sync_payments")
async def xero_bulk_sync_payments(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Bulk sync all unsynced payments to Xero.

    Automatically syncs the parent invoice first if needed (safety net).
    Returns counts of synced, failed, and errors.
    """
    result = await bulk_sync_payments(db)

    # Log activity
    activity = ActivityLog(
        action="xero_bulk_sync_payments",
        description=f"Bulk synced {result['synced']} payments to Xero ({result['failed']} failed)",
        entity_type="integration",
        ip_address=request.client.host if request.client else None,
        extra_data=result,
    )
    db.add(activity)
    await db.commit()

    return result


# =============================================================================
# SYNC STATUS API
# =============================================================================

@router.get("/xero/sync/status", name="integrations:xero_sync_status")
async def xero_sync_status(db: AsyncSession = Depends(get_db)):
    """
    Get comprehensive Xero sync status.

    Shows what's synced vs what's missing for customers, invoices, and payments.
    Used to power the sync dashboard and identify gaps.
    """
    connection = await get_xero_connection_status(db)
    sync = await get_sync_status(db)

    return {
        "connection": connection,
        "sync": sync,
    }


# =============================================================================
# XERO SETUP — Chart of Accounts & Category Mapping (Part E)
# =============================================================================

@router.get("/xero/setup/accounts", name="integrations:xero_accounts")
async def xero_get_accounts(db: AsyncSession = Depends(get_db)):
    """
    Pull chart of accounts from Xero.

    Returns expense-type accounts for category mapping.
    """
    accounts = await fetch_chart_of_accounts(db)
    if accounts is None:
        raise HTTPException(500, "Failed to fetch accounts from Xero. Check connection.")
    return {"accounts": accounts}


@router.get("/xero/setup/bank-accounts", name="integrations:xero_bank_accounts")
async def xero_get_bank_accounts(db: AsyncSession = Depends(get_db)):
    """
    Pull bank accounts from Xero.

    Returns bank accounts for the "paid from" selection.
    """
    accounts = await fetch_bank_accounts(db)
    if accounts is None:
        raise HTTPException(500, "Failed to fetch bank accounts from Xero.")
    return {"bank_accounts": accounts}


@router.get("/xero/setup/mappings", name="integrations:xero_mappings")
async def xero_get_mappings(db: AsyncSession = Depends(get_db)):
    """Get current expense category → Xero account mappings."""
    mappings = await get_account_mappings(db)
    return {
        "mappings": [
            {
                "category": m.category,
                "xero_account_code": m.xero_account_code,
                "xero_account_name": m.xero_account_name,
                "xero_tax_type": m.xero_tax_type,
            }
            for m in mappings
        ]
    }


@router.post("/xero/setup/mappings", name="integrations:xero_save_mappings")
async def xero_save_mappings(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Save expense category → Xero account mappings.

    Expects JSON body:
    {
      "mappings": [
        {"category": "materials", "xero_account_code": "300", "xero_account_name": "Materials"},
        ...
      ],
      "bank_account_code": "090"
    }
    """
    data = await request.json()
    mappings = data.get("mappings", [])
    bank_code = data.get("bank_account_code")

    saved = 0
    for m in mappings:
        if m.get("category") and m.get("xero_account_code"):
            await save_account_mapping(
                db,
                category=m["category"],
                xero_account_code=m["xero_account_code"],
                xero_account_name=m.get("xero_account_name", ""),
                xero_tax_type=m.get("xero_tax_type", "INPUT"),
            )
            saved += 1

    # Log activity
    activity = ActivityLog(
        action="xero_mappings_saved",
        description=f"Saved {saved} Xero account mappings",
        entity_type="integration",
        ip_address=request.client.host if request.client else None,
        extra_data={"saved": saved, "bank_account_code": bank_code},
    )
    db.add(activity)
    await db.commit()

    return {"success": True, "saved": saved}


# =============================================================================
# EXPENSE SYNC TO XERO (Part F)
# =============================================================================

@router.post("/xero/sync/expense/{expense_id}", name="integrations:xero_sync_expense")
async def xero_sync_expense(
    expense_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Manually sync a single expense to Xero as a Spend Money transaction.
    """
    expense = await db.get(Expense, expense_id)
    if not expense:
        raise HTTPException(404, "Expense not found")

    xero_id = await sync_expense_to_xero(db, expense)

    if xero_id:
        activity = ActivityLog(
            action="xero_expense_synced",
            description=f"Expense {expense.expense_number} synced to Xero",
            entity_type="expense",
            entity_id=expense_id,
            ip_address=request.client.host if request.client else None,
            extra_data={"xero_bill_id": xero_id},
        )
        db.add(activity)
        await db.commit()

        return {"success": True, "xero_bill_id": xero_id}
    else:
        await db.commit()  # Commit to save the sync error on the expense
        raise HTTPException(500, f"Failed to sync expense: {expense.xero_sync_error or 'Unknown error'}")


@router.post("/xero/sync/bulk/expenses", name="integrations:xero_bulk_sync_expenses")
async def xero_bulk_sync_expenses(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Bulk sync all unsynced expenses to Xero."""
    result = await bulk_sync_expenses(db)

    activity = ActivityLog(
        action="xero_bulk_sync_expenses",
        description=f"Bulk synced {result['synced']} expenses to Xero ({result['failed']} failed)",
        entity_type="integration",
        ip_address=request.client.host if request.client else None,
        extra_data=result,
    )
    db.add(activity)
    await db.commit()

    return result


# =============================================================================
# GOOGLE CALENDAR — NSW PUBLIC HOLIDAY SYNC
# =============================================================================

@router.post("/api/sync-holidays", name="integrations:sync_holidays")
async def api_sync_holidays(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Sync NSW public holidays to Google Calendar for a given year.

    Expects JSON body: {"year": 2026}  (optional — defaults to current year)
    """
    try:
        body = await request.json()
        year = body.get("year")
    except Exception:
        year = None

    if year is not None:
        year = int(year)
        if year not in NSW_PUBLIC_HOLIDAYS:
            raise HTTPException(
                400,
                f"No holiday data available for {year}. "
                f"Available years: {', '.join(str(y) for y in sorted(NSW_PUBLIC_HOLIDAYS.keys()))}",
            )

    result = await sync_nsw_holidays(year)

    # Log activity
    actual_year = result.get("year", year or sydney_now().year)
    activity = ActivityLog(
        action="holidays_synced",
        description=f"NSW public holidays synced to Google Calendar for {actual_year}",
        entity_type="integration",
        ip_address=request.client.host if request.client else None,
        extra_data=result,
    )
    db.add(activity)
    await db.commit()

    return result


@router.get("/api/holidays/{year}", name="integrations:holidays_status")
async def api_holidays_status(year: int):
    """
    Get NSW public holiday list for a year with sync status.

    Returns list of holidays with whether each is already in Google Calendar.
    """
    if year not in NSW_PUBLIC_HOLIDAYS:
        raise HTTPException(
            400,
            f"No holiday data available for {year}. "
            f"Available years: {', '.join(str(y) for y in sorted(NSW_PUBLIC_HOLIDAYS.keys()))}",
        )

    holidays = await get_synced_holidays(year)
    return {
        "year": year,
        "holidays": holidays,
        "available_years": sorted(NSW_PUBLIC_HOLIDAYS.keys()),
    }


# =============================================================================
# GOOGLE CALENDAR — ATO COMPLIANCE DEADLINE SYNC
# =============================================================================

@router.post("/api/sync-compliance", name="integrations:sync_compliance")
async def api_sync_compliance(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Sync ATO compliance deadlines to Google Calendar for a given FY.

    Expects JSON body: {"fy": 2026}  (optional — defaults to current FY)

    Creates colour-coded events with 1-week and 1-day reminders for:
    BAS (x4), Super (x4), TPAR, Tax Return, Workers Comp, Public Liability.
    """
    try:
        body = await request.json()
        fy = body.get("fy")
    except Exception:
        fy = None

    if fy is not None:
        fy = int(fy)

    result = await sync_compliance_deadlines(fy)

    # Log activity
    actual_fy = result.get("fy", fy)
    activity = ActivityLog(
        action="compliance_deadlines_synced",
        description=f"ATO compliance deadlines synced to Google Calendar for FY{actual_fy}",
        entity_type="integration",
        ip_address=request.client.host if request.client else None,
        extra_data=result,
    )
    db.add(activity)
    await db.commit()

    return result


@router.get("/api/compliance/{fy}", name="integrations:compliance_status")
async def api_compliance_status(fy: int):
    """
    Get ATO compliance deadline list for a FY with sync status.

    Returns list of deadlines with whether each is already in Google Calendar.
    """
    deadlines = await get_synced_compliance_deadlines(fy)
    return {
        "fy": fy,
        "deadlines": deadlines,
    }
