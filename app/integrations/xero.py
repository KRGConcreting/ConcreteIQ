"""
Xero Integration — OAuth2 and sync for contacts, invoices, and payments.

Uses OAuth 2.0 authorization code flow for user authentication.
Tokens are encrypted at rest and automatically refreshed.
Sync fails gracefully - Xero errors should not block main flows.
"""

import asyncio
import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import func

from app.config import settings
from app.core.dates import sydney_now
from app.models import OAuthToken, Customer, Invoice, Payment, ActivityLog, Expense, XeroAccountMapping

logger = logging.getLogger(__name__)


# =============================================================================
# DB-FIRST CREDENTIAL HELPERS
# =============================================================================

async def _get_xero_credentials(db: Optional[AsyncSession] = None) -> tuple[str, str]:
    """Get Xero client ID and secret — checks DB first, falls back to env vars."""
    client_id = settings.xero_client_id or ""
    client_secret = settings.xero_client_secret or ""

    if db:
        try:
            from app.settings import service as settings_service
            db_settings = await settings_service.get_settings_by_category(db, "integrations")
            client_id = db_settings.get("xero_client_id") or client_id
            client_secret = db_settings.get("xero_client_secret") or client_secret
        except Exception:
            pass

    return client_id, client_secret


def _get_xero_credentials_sync() -> tuple[str, str]:
    """Get Xero credentials from env vars only (for sync/non-async contexts)."""
    return settings.xero_client_id or "", settings.xero_client_secret or ""

# =============================================================================
# XERO API ENDPOINTS
# =============================================================================

XERO_AUTH_URL = "https://login.xero.com/identity/connect/authorize"
XERO_TOKEN_URL = "https://identity.xero.com/connect/token"
XERO_CONNECTIONS_URL = "https://api.xero.com/connections"
XERO_API_URL = "https://api.xero.com/api.xro/2.0"

XERO_RATE_LIMIT = 50  # Conservative limit (Xero allows 60 calls/min)
XERO_RATE_PERIOD = 60.0  # Window in seconds
XERO_BULK_BATCH_SIZE = 50  # Max items per bulk sync run


# =============================================================================
# RATE LIMITER — Token bucket for Xero API calls
# =============================================================================

class _XeroRateLimiter:
    """Simple async token-bucket rate limiter for Xero API (50 calls / 60 s)."""

    def __init__(self, max_calls: int = XERO_RATE_LIMIT, period: float = XERO_RATE_PERIOD):
        self._max_calls = max_calls
        self._period = period
        self._calls: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Wait until a request slot is available."""
        async with self._lock:
            now = time.monotonic()
            # Remove calls outside the sliding window
            self._calls = [t for t in self._calls if now - t < self._period]

            if len(self._calls) >= self._max_calls:
                # Wait until the oldest call exits the window
                sleep_for = self._period - (now - self._calls[0])
                if sleep_for > 0:
                    logger.info(f"Xero rate limit: pausing {sleep_for:.1f}s")
                    await asyncio.sleep(sleep_for)
                    now = time.monotonic()
                    self._calls = [t for t in self._calls if now - t < self._period]

            self._calls.append(time.monotonic())


_xero_limiter = _XeroRateLimiter()


async def _throttled_xero_request(
    method: str,
    url: str,
    *,
    headers: dict,
    json: Optional[dict] = None,
    params: Optional[dict] = None,
    timeout: float = 30.0,
    _retries: int = 3,
) -> httpx.Response:
    """
    Send an HTTP request to the Xero API with rate limiting and 429 retry.

    NOT used for OAuth token exchange (those hit identity.xero.com,
    which has its own limits).
    """
    response: Optional[httpx.Response] = None

    for attempt in range(1, _retries + 1):
        await _xero_limiter.acquire()

        async with httpx.AsyncClient() as client:
            kwargs: dict = {"headers": headers, "timeout": timeout}
            if json is not None:
                kwargs["json"] = json
            if params is not None:
                kwargs["params"] = params

            request_fn = getattr(client, method.lower())
            response = await request_fn(url, **kwargs)

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "60"))
            logger.warning(
                f"Xero 429 rate-limited (attempt {attempt}/{_retries}), "
                f"retrying in {retry_after}s"
            )
            await asyncio.sleep(retry_after)
            continue

        return response

    # Exhausted retries — return the last 429 response so the caller
    # can log it and continue gracefully.
    return response  # type: ignore[return-value]


# Token encryption key derived from secret_key
# In production, use a separate encryption key stored securely
_encryption_key: Optional[bytes] = None


def _get_encryption_key() -> bytes:
    """Derive encryption key from secret_key."""
    global _encryption_key
    if _encryption_key is None:
        # Use SHA256 of secret_key as Fernet key (requires 32 bytes base64-encoded)
        key_hash = hashlib.sha256(settings.secret_key.encode()).digest()
        import base64
        _encryption_key = base64.urlsafe_b64encode(key_hash)
    return _encryption_key


def _encrypt_token(token: str) -> str:
    """Encrypt a token for storage."""
    if not token:
        return ""
    f = Fernet(_get_encryption_key())
    return f.encrypt(token.encode()).decode()


def _decrypt_token(encrypted: str) -> str:
    """Decrypt a stored token."""
    if not encrypted:
        return ""
    f = Fernet(_get_encryption_key())
    return f.decrypt(encrypted.encode()).decode()


# =============================================================================
# OAUTH FLOW
# =============================================================================

def get_authorization_url(state: Optional[str] = None, client_id: Optional[str] = None) -> str:
    """
    Generate Xero OAuth authorization URL.

    Args:
        state: Optional state parameter for CSRF protection
        client_id: Xero client ID (if None, reads from env var)

    Returns:
        Full authorization URL to redirect user to
    """
    xero_client_id = client_id or settings.xero_client_id
    if not xero_client_id:
        raise ValueError("Xero client ID not configured")

    if state is None:
        state = secrets.token_urlsafe(32)

    params = {
        "response_type": "code",
        "client_id": xero_client_id,
        "redirect_uri": settings.xero_redirect_uri or f"{settings.app_url}/integrations/xero/callback",
        "scope": settings.xero_scopes,
        "state": state,
    }

    return f"{XERO_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_tokens(code: str, db: Optional[AsyncSession] = None) -> dict:
    """
    Exchange authorization code for access and refresh tokens.

    Args:
        code: Authorization code from OAuth callback
        db: Optional database session for reading credentials from DB

    Returns:
        Dict with access_token, refresh_token, expires_in

    Raises:
        Exception on API error
    """
    client_id, client_secret = await _get_xero_credentials(db)
    if not client_id or not client_secret:
        raise ValueError("Xero credentials not configured")

    redirect_uri = settings.xero_redirect_uri or f"{settings.app_url}/integrations/xero/callback"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            XERO_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            auth=(client_id, client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )

        if response.status_code != 200:
            logger.error(f"Xero token exchange failed: {response.status_code} {response.text}")
            raise Exception(f"Xero token exchange failed: {response.text}")

        return response.json()


async def refresh_access_token(refresh_token: str, db: Optional[AsyncSession] = None) -> dict:
    """
    Refresh access token using refresh token.

    Args:
        refresh_token: Current refresh token

    Returns:
        Dict with new access_token, refresh_token, expires_in

    Raises:
        Exception on API error
    """
    client_id, client_secret = await _get_xero_credentials(db)
    if not client_id or not client_secret:
        raise ValueError("Xero credentials not configured")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            XERO_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            auth=(client_id, client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )

        if response.status_code != 200:
            logger.error(f"Xero token refresh failed: {response.status_code} {response.text}")
            raise Exception(f"Xero token refresh failed: {response.text}")

        return response.json()


async def get_tenant_id(access_token: str) -> str:
    """
    Get Xero tenant ID from connections endpoint.

    Args:
        access_token: Valid Xero access token

    Returns:
        Tenant ID (first connected organization)

    Raises:
        Exception if no connections or API error
    """
    response = await _throttled_xero_request(
        "get",
        XERO_CONNECTIONS_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )

    if response.status_code != 200:
        logger.error(f"Xero connections failed: {response.status_code} {response.text}")
        raise Exception(f"Failed to get Xero connections: {response.text}")

    connections = response.json()
    if not connections:
        raise Exception("No Xero organizations connected")

    # Return first tenant ID
    return connections[0]["tenantId"]


# =============================================================================
# TOKEN MANAGEMENT
# =============================================================================

async def save_xero_tokens(
    db: AsyncSession,
    access_token: str,
    refresh_token: str,
    expires_in: int,
    tenant_id: str,
) -> OAuthToken:
    """
    Save Xero tokens to database (encrypted).

    Args:
        db: Database session
        access_token: Access token
        refresh_token: Refresh token
        expires_in: Seconds until expiry
        tenant_id: Xero tenant ID

    Returns:
        OAuthToken record
    """
    expires_at = sydney_now() + timedelta(seconds=expires_in)

    # Check for existing token
    result = await db.execute(
        select(OAuthToken).where(OAuthToken.provider == "xero")
    )
    token = result.scalar_one_or_none()

    if token:
        # Update existing
        token.access_token = _encrypt_token(access_token)
        token.refresh_token = _encrypt_token(refresh_token)
        token.expires_at = expires_at
        token.extra_data = {"tenant_id": tenant_id}
        token.updated_at = sydney_now()
    else:
        # Create new
        token = OAuthToken(
            provider="xero",
            access_token=_encrypt_token(access_token),
            refresh_token=_encrypt_token(refresh_token),
            expires_at=expires_at,
            extra_data={"tenant_id": tenant_id},
        )
        db.add(token)

    return token


async def get_xero_token(db: AsyncSession) -> Optional[OAuthToken]:
    """
    Get Xero token from database.

    Args:
        db: Database session

    Returns:
        OAuthToken or None if not connected
    """
    result = await db.execute(
        select(OAuthToken).where(OAuthToken.provider == "xero")
    )
    return result.scalar_one_or_none()


async def get_valid_access_token(db: AsyncSession) -> Optional[tuple[str, str]]:
    """
    Get valid Xero access token, refreshing if needed.

    Args:
        db: Database session

    Returns:
        Tuple of (access_token, tenant_id) or None if not connected
    """
    token = await get_xero_token(db)
    if not token:
        return None

    tenant_id = token.extra_data.get("tenant_id") if token.extra_data else None
    if not tenant_id:
        logger.warning("Xero token missing tenant_id")
        return None

    # Check if token needs refresh (refresh 5 minutes before expiry)
    if token.expires_at and token.expires_at <= sydney_now() + timedelta(minutes=5):
        try:
            decrypted_refresh = _decrypt_token(token.refresh_token)
            new_tokens = await refresh_access_token(decrypted_refresh, db=db)

            await save_xero_tokens(
                db,
                new_tokens["access_token"],
                new_tokens["refresh_token"],
                new_tokens["expires_in"],
                tenant_id,
            )
            await db.commit()

            return new_tokens["access_token"], tenant_id
        except Exception as e:
            logger.error(f"Failed to refresh Xero token: {e}")
            return None

    # Token still valid
    return _decrypt_token(token.access_token), tenant_id


async def delete_xero_token(db: AsyncSession) -> bool:
    """
    Delete Xero token (disconnect).

    Args:
        db: Database session

    Returns:
        True if deleted, False if not found
    """
    token = await get_xero_token(db)
    if token:
        await db.delete(token)
        return True
    return False


# =============================================================================
# SYNC FUNCTIONS
# =============================================================================

async def sync_customer_to_xero(
    db: AsyncSession,
    customer: Customer,
) -> Optional[str]:
    """
    Create or update customer as Xero contact.

    Args:
        db: Database session
        customer: Customer to sync

    Returns:
        Xero contact ID if successful, None on failure
    """
    creds = await get_valid_access_token(db)
    if not creds:
        logger.info("Xero not connected, skipping customer sync")
        return None

    access_token, tenant_id = creds

    # Build contact payload
    contact = {
        "Name": customer.business_name or customer.name,
        "FirstName": customer.name.split()[0] if customer.name else "",
        "LastName": " ".join(customer.name.split()[1:]) if customer.name and len(customer.name.split()) > 1 else "",
        "EmailAddress": customer.email or "",
    }

    # Add phone numbers
    if customer.phone:
        contact["Phones"] = [{"PhoneType": "MOBILE", "PhoneNumber": customer.phone}]
        if customer.phone2:
            contact["Phones"].append({"PhoneType": "DEFAULT", "PhoneNumber": customer.phone2})

    # Add address
    if customer.street or customer.city:
        contact["Addresses"] = [{
            "AddressType": "STREET",
            "AddressLine1": customer.street or "",
            "City": customer.city or "",
            "Region": customer.state or "",
            "PostalCode": customer.postcode or "",
            "Country": "Australia",
        }]

    try:
        xero_headers = {
            "Authorization": f"Bearer {access_token}",
            "xero-tenant-id": tenant_id,
            "Content-Type": "application/json",
        }

        if customer.xero_contact_id:
            # Update existing contact
            contact["ContactID"] = customer.xero_contact_id
            response = await _throttled_xero_request(
                "post",
                f"{XERO_API_URL}/Contacts/{customer.xero_contact_id}",
                json={"Contacts": [contact]},
                headers=xero_headers,
            )
        else:
            # Create new contact
            response = await _throttled_xero_request(
                "post",
                f"{XERO_API_URL}/Contacts",
                json={"Contacts": [contact]},
                headers=xero_headers,
            )

        if response.status_code in (200, 201):
            data = response.json()
            contact_id = data["Contacts"][0]["ContactID"]
            customer.xero_contact_id = contact_id
            logger.info(f"Synced customer {customer.id} to Xero contact {contact_id}")
            return contact_id
        else:
            logger.error(f"Xero contact sync failed: {response.status_code} {response.text}")
            return None

    except Exception as e:
        logger.error(f"Failed to sync customer {customer.id} to Xero: {e}")
        return None


async def sync_invoice_to_xero(
    db: AsyncSession,
    invoice: Invoice,
) -> Optional[str]:
    """
    Create or update invoice in Xero.

    Args:
        db: Database session
        invoice: Invoice to sync

    Returns:
        Xero invoice ID if successful, None on failure
    """
    creds = await get_valid_access_token(db)
    if not creds:
        logger.info("Xero not connected, skipping invoice sync")
        return None

    access_token, tenant_id = creds

    # Explicitly load customer (async sessions don't support lazy loading)
    customer = await db.get(Customer, invoice.customer_id)
    if not customer:
        logger.warning(f"Customer {invoice.customer_id} not found for invoice {invoice.invoice_number}")
        return None

    # Decrypt PII so we have name/email/phone for Xero contact
    try:
        from app.core.security import decrypt_customer_pii
        decrypt_customer_pii(customer)
    except Exception:
        pass  # PII may already be decrypted or not encrypted

    # Ensure customer is synced first
    if not customer.xero_contact_id:
        contact_id = await sync_customer_to_xero(db, customer)
        if not contact_id:
            logger.warning(f"Could not sync customer {customer.id}, skipping invoice sync")
            return None

    # Build line items
    line_items = []
    if invoice.line_items:
        for item in invoice.line_items:
            line_items.append({
                "Description": item.get("description", "Service"),
                "Quantity": item.get("quantity", 1),
                "UnitAmount": item.get("amount_cents", 0) / 100,
                "AccountCode": "200",  # Sales account - adjust as needed
                "TaxType": "OUTPUT2",  # AU GST on Income
            })
    else:
        # Single line item for the total
        line_items.append({
            "Description": invoice.description or f"Invoice {invoice.invoice_number}",
            "Quantity": 1,
            "UnitAmount": invoice.subtotal_cents / 100,
            "AccountCode": "200",
            "TaxType": "OUTPUT2",  # AU GST on Income
        })

    # Build invoice payload
    xero_invoice = {
        "Type": "ACCREC",  # Accounts Receivable
        "Contact": {"ContactID": customer.xero_contact_id},
        "Date": invoice.issue_date.isoformat() if invoice.issue_date else sydney_now().date().isoformat(),
        "DueDate": invoice.due_date.isoformat() if invoice.due_date else (invoice.issue_date + timedelta(days=14)).isoformat() if invoice.issue_date else (sydney_now().date() + timedelta(days=14)).isoformat(),
        "LineItems": line_items,
        "Reference": invoice.invoice_number,
        "Status": "AUTHORISED",  # Ready to send
        "LineAmountTypes": "Exclusive",  # Line items are ex GST
    }

    try:
        xero_headers = {
            "Authorization": f"Bearer {access_token}",
            "xero-tenant-id": tenant_id,
            "Content-Type": "application/json",
        }

        if invoice.xero_invoice_id:
            # Update existing invoice
            xero_invoice["InvoiceID"] = invoice.xero_invoice_id
            response = await _throttled_xero_request(
                "post",
                f"{XERO_API_URL}/Invoices/{invoice.xero_invoice_id}",
                json={"Invoices": [xero_invoice]},
                headers=xero_headers,
            )
        else:
            # Create new invoice
            response = await _throttled_xero_request(
                "post",
                f"{XERO_API_URL}/Invoices",
                json={"Invoices": [xero_invoice]},
                headers=xero_headers,
            )

        if response.status_code in (200, 201):
            data = response.json()
            xero_id = data["Invoices"][0]["InvoiceID"]
            invoice.xero_invoice_id = xero_id
            invoice.xero_synced_at = sydney_now()
            logger.info(f"Synced invoice {invoice.invoice_number} to Xero {xero_id}")
            return xero_id
        else:
            logger.error(f"Xero invoice sync failed: {response.status_code} {response.text}")
            return None

    except Exception as e:
        logger.error(f"Failed to sync invoice {invoice.invoice_number} to Xero: {e}")
        return None


async def sync_payment_to_xero(
    db: AsyncSession,
    payment: Payment,
) -> Optional[str]:
    """
    Record payment in Xero against the invoice.

    Args:
        db: Database session
        payment: Payment to sync

    Returns:
        Xero payment ID if successful, None on failure
    """
    creds = await get_valid_access_token(db)
    if not creds:
        logger.info("Xero not connected, skipping payment sync")
        return None

    access_token, tenant_id = creds

    # SAFETY NET: Explicitly load invoice (async sessions don't support lazy loading)
    invoice = await db.get(Invoice, payment.invoice_id)
    if not invoice:
        logger.warning(f"Invoice {payment.invoice_id} not found for payment {payment.id}")
        return None

    if not invoice.xero_invoice_id:
        # Safety net: auto-sync invoice to Xero before recording payment
        logger.info(f"Payment safety net: syncing invoice {invoice.invoice_number} to Xero before payment")
        xero_invoice_id = await sync_invoice_to_xero(db, invoice)
        if not xero_invoice_id:
            logger.warning(f"Could not sync invoice {invoice.invoice_number} to Xero, skipping payment sync")
            return None

    # Build payment payload
    xero_payment = {
        "Invoice": {"InvoiceID": invoice.xero_invoice_id},
        "Account": {"Code": settings.xero_bank_account_code},  # Use configurable bank account
        "Date": payment.payment_date.isoformat() if payment.payment_date else sydney_now().date().isoformat(),
        "Amount": payment.amount_cents / 100,
        "Reference": payment.reference or f"Payment {payment.id}",
    }

    try:
        response = await _throttled_xero_request(
            "put",
            f"{XERO_API_URL}/Payments",
            json={"Payments": [xero_payment]},
            headers={
                "Authorization": f"Bearer {access_token}",
                "xero-tenant-id": tenant_id,
                "Content-Type": "application/json",
            },
        )

        if response.status_code in (200, 201):
            data = response.json()
            xero_payment_id = data["Payments"][0]["PaymentID"]
            payment.xero_payment_id = xero_payment_id
            logger.info(f"Synced payment {payment.id} to Xero {xero_payment_id}")
            return xero_payment_id
        else:
            logger.error(f"Xero payment sync failed: {response.status_code} {response.text}")
            return None

    except Exception as e:
        logger.error(f"Failed to sync payment {payment.id} to Xero: {e}")
        return None


async def get_xero_connection_status(db: AsyncSession) -> dict:
    """
    Get current Xero connection status.

    Args:
        db: Database session

    Returns:
        Dict with connected status and details
    """
    token = await get_xero_token(db)
    if not token:
        return {
            "connected": False,
            "tenant_id": None,
            "expires_at": None,
        }

    tenant_id = token.extra_data.get("tenant_id") if token.extra_data else None
    is_expired = token.expires_at and token.expires_at <= sydney_now()

    return {
        "connected": True,
        "tenant_id": tenant_id,
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        "is_expired": is_expired,
        "updated_at": token.updated_at.isoformat() if token.updated_at else None,
    }


# =============================================================================
# VOID INVOICE IN XERO
# =============================================================================

async def void_invoice_in_xero(
    db: AsyncSession,
    invoice: Invoice,
) -> bool:
    """
    Void an invoice in Xero (set status to VOIDED).

    Must be called when a ConcreteIQ invoice is voided so Xero stays in sync.
    Only applies if the invoice was previously synced to Xero.

    Args:
        db: Database session
        invoice: Invoice being voided

    Returns:
        True if voided in Xero, False otherwise
    """
    if not invoice.xero_invoice_id:
        logger.info(f"Invoice {invoice.invoice_number} has no Xero ID, nothing to void")
        return False

    creds = await get_valid_access_token(db)
    if not creds:
        logger.info("Xero not connected, skipping void sync")
        return False

    access_token, tenant_id = creds

    # Xero requires setting status to VOIDED
    xero_payload = {
        "InvoiceID": invoice.xero_invoice_id,
        "Status": "VOIDED",
    }

    try:
        response = await _throttled_xero_request(
            "post",
            f"{XERO_API_URL}/Invoices/{invoice.xero_invoice_id}",
            json={"Invoices": [xero_payload]},
            headers={
                "Authorization": f"Bearer {access_token}",
                "xero-tenant-id": tenant_id,
                "Content-Type": "application/json",
            },
        )

        if response.status_code in (200, 201):
            logger.info(f"Voided invoice {invoice.invoice_number} in Xero ({invoice.xero_invoice_id})")
            invoice.xero_synced_at = sydney_now()
            return True
        else:
            logger.error(f"Xero void failed for {invoice.invoice_number}: {response.status_code} {response.text}")
            return False

    except Exception as e:
        logger.error(f"Failed to void invoice {invoice.invoice_number} in Xero: {e}")
        return False


# =============================================================================
# BULK SYNC FUNCTIONS
# =============================================================================

async def bulk_sync_customers(db: AsyncSession) -> dict:
    """
    Sync all customers that don't have a xero_contact_id to Xero.

    Processes every unsynced customer with a 1 s pause between each.
    The ``_throttled_xero_request`` token-bucket handles Xero's 60/min
    limit; the sleep provides extra breathing room.

    Returns:
        Dict with synced count, failed count, and error details
    """
    result = await db.execute(
        select(Customer).where(Customer.xero_contact_id.is_(None))
    )
    all_unsynced = result.scalars().all()
    total = len(all_unsynced)

    synced = 0
    failed = 0
    errors = []

    for idx, customer in enumerate(all_unsynced, 1):
        logger.info(f"Xero bulk sync customers: {idx}/{total}")

        try:
            from app.core.security import decrypt_customer_pii
            decrypt_customer_pii(customer)
        except Exception:
            pass

        contact_id = await sync_customer_to_xero(db, customer)
        if contact_id:
            synced += 1
        else:
            failed += 1
            errors.append(f"Customer {customer.id}: {customer.name}")

        # Commit each batch of XERO_BULK_BATCH_SIZE so progress isn't lost
        if idx % XERO_BULK_BATCH_SIZE == 0:
            await db.commit()

        await asyncio.sleep(1.0)

    return {
        "total": total,
        "synced": synced,
        "failed": failed,
        "errors": errors[:10],
    }


async def bulk_sync_invoices(db: AsyncSession) -> dict:
    """
    Sync all invoices that don't have a xero_invoice_id to Xero.

    Only syncs non-draft, non-voided invoices.
    Processes every unsynced invoice with a 1 s pause between each.

    Returns:
        Dict with synced count, failed count, and error details
    """
    result = await db.execute(
        select(Invoice).where(
            Invoice.xero_invoice_id.is_(None),
            Invoice.status.notin_(["draft", "voided"]),
        )
    )
    all_unsynced = result.scalars().all()
    total = len(all_unsynced)

    synced = 0
    failed = 0
    errors = []

    for idx, invoice in enumerate(all_unsynced, 1):
        logger.info(f"Xero bulk sync invoices: {idx}/{total}")

        xero_id = await sync_invoice_to_xero(db, invoice)
        if xero_id:
            synced += 1
        else:
            failed += 1
            errors.append(f"Invoice {invoice.invoice_number}")

        if idx % XERO_BULK_BATCH_SIZE == 0:
            await db.commit()

        await asyncio.sleep(1.0)

    return {
        "total": total,
        "synced": synced,
        "failed": failed,
        "errors": errors[:10],
    }


async def bulk_sync_payments(db: AsyncSession) -> dict:
    """
    Sync all payments that don't have a xero_payment_id to Xero.

    Processes every unsynced payment with a 1 s pause between each.

    Returns:
        Dict with synced count, failed count, and error details
    """
    result = await db.execute(
        select(Payment).where(Payment.xero_payment_id.is_(None))
    )
    all_unsynced = result.scalars().all()
    total = len(all_unsynced)

    synced = 0
    failed = 0
    errors = []

    for idx, payment in enumerate(all_unsynced, 1):
        logger.info(f"Xero bulk sync payments: {idx}/{total}")

        xero_id = await sync_payment_to_xero(db, payment)
        if xero_id:
            synced += 1
        else:
            failed += 1
            errors.append(f"Payment {payment.id}")

        if idx % XERO_BULK_BATCH_SIZE == 0:
            await db.commit()

        await asyncio.sleep(1.0)

    return {
        "total": total,
        "synced": synced,
        "failed": failed,
        "errors": errors[:10],
    }


# =============================================================================
# SYNC STATUS
# =============================================================================

async def get_sync_status(db: AsyncSession) -> dict:
    """
    Get comprehensive sync status — what's in Xero and what's missing.

    Returns counts for customers, invoices, and payments showing
    synced vs unsynced totals.
    """
    # Customer counts
    total_customers = (await db.execute(
        select(func.count(Customer.id))
    )).scalar() or 0
    synced_customers = (await db.execute(
        select(func.count(Customer.id)).where(Customer.xero_contact_id.isnot(None))
    )).scalar() or 0

    # Invoice counts (only non-draft, non-voided)
    active_invoice_filter = Invoice.status.notin_(["draft", "voided"])
    total_invoices = (await db.execute(
        select(func.count(Invoice.id)).where(active_invoice_filter)
    )).scalar() or 0
    synced_invoices = (await db.execute(
        select(func.count(Invoice.id)).where(
            active_invoice_filter,
            Invoice.xero_invoice_id.isnot(None),
        )
    )).scalar() or 0

    # Voided invoices still in Xero (voided locally but may need Xero void)
    voided_with_xero_id = (await db.execute(
        select(func.count(Invoice.id)).where(
            Invoice.status == "voided",
            Invoice.xero_invoice_id.isnot(None),
        )
    )).scalar() or 0

    # Payment counts
    total_payments = (await db.execute(
        select(func.count(Payment.id))
    )).scalar() or 0
    synced_payments = (await db.execute(
        select(func.count(Payment.id)).where(Payment.xero_payment_id.isnot(None))
    )).scalar() or 0

    return {
        "customers": {
            "total": total_customers,
            "synced": synced_customers,
            "unsynced": total_customers - synced_customers,
        },
        "invoices": {
            "total": total_invoices,
            "synced": synced_invoices,
            "unsynced": total_invoices - synced_invoices,
            "voided_needs_sync": voided_with_xero_id,
        },
        "payments": {
            "total": total_payments,
            "synced": synced_payments,
            "unsynced": total_payments - synced_payments,
        },
    }


# =============================================================================
# CHART OF ACCOUNTS (Part E — One-time Xero setup)
# =============================================================================

async def fetch_chart_of_accounts(db: AsyncSession) -> Optional[list[dict]]:
    """
    Pull chart of accounts from Xero.

    Returns list of accounts with Code, Name, Type, TaxType.
    Filters to EXPENSE and DIRECTCOSTS type accounts.
    """
    creds = await get_valid_access_token(db)
    if not creds:
        logger.info("Xero not connected, cannot fetch chart of accounts")
        return None

    access_token, tenant_id = creds

    try:
        response = await _throttled_xero_request(
            "get",
            f"{XERO_API_URL}/Accounts",
            headers={
                "Authorization": f"Bearer {access_token}",
                "xero-tenant-id": tenant_id,
                "Content-Type": "application/json",
            },
        )

        if response.status_code == 200:
            data = response.json()
            accounts = data.get("Accounts", [])

            # Filter to expense-type accounts (useful for spend money)
            expense_accounts = [
                {
                    "code": acct.get("Code", ""),
                    "name": acct.get("Name", ""),
                    "type": acct.get("Type", ""),
                    "tax_type": acct.get("TaxType", ""),
                    "status": acct.get("Status", ""),
                    "class": acct.get("Class", ""),
                }
                for acct in accounts
                if acct.get("Status") == "ACTIVE"
                and acct.get("Type") in ("EXPENSE", "DIRECTCOSTS", "OVERHEADS", "CURRENT")
            ]

            logger.info(f"Fetched {len(expense_accounts)} expense accounts from Xero")
            return expense_accounts
        else:
            logger.error(f"Xero accounts fetch failed: {response.status_code} {response.text}")
            return None

    except Exception as e:
        logger.error(f"Failed to fetch Xero chart of accounts: {e}")
        return None


async def fetch_bank_accounts(db: AsyncSession) -> Optional[list[dict]]:
    """
    Pull bank accounts from Xero.

    Returns list of BANK type accounts for the Spend Money "paid from" dropdown.
    """
    creds = await get_valid_access_token(db)
    if not creds:
        return None

    access_token, tenant_id = creds

    try:
        response = await _throttled_xero_request(
            "get",
            f"{XERO_API_URL}/Accounts",
            params={"where": 'Type=="BANK"'},
            headers={
                "Authorization": f"Bearer {access_token}",
                "xero-tenant-id": tenant_id,
                "Content-Type": "application/json",
            },
        )

        if response.status_code == 200:
            data = response.json()
            accounts = data.get("Accounts", [])
            return [
                {
                    "code": acct.get("Code", ""),
                    "name": acct.get("Name", ""),
                    "account_id": acct.get("AccountID", ""),
                }
                for acct in accounts
                if acct.get("Status") == "ACTIVE"
            ]
        else:
            logger.error(f"Xero bank accounts fetch failed: {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"Failed to fetch Xero bank accounts: {e}")
        return None


async def get_account_mappings(db: AsyncSession) -> list[XeroAccountMapping]:
    """Get all expense category → Xero account mappings."""
    result = await db.execute(
        select(XeroAccountMapping).order_by(XeroAccountMapping.category)
    )
    return list(result.scalars().all())


async def save_account_mapping(
    db: AsyncSession,
    category: str,
    xero_account_code: str,
    xero_account_name: str = "",
    xero_tax_type: str = "INPUT",
) -> XeroAccountMapping:
    """Save or update an expense category → Xero account mapping."""
    result = await db.execute(
        select(XeroAccountMapping).where(XeroAccountMapping.category == category)
    )
    mapping = result.scalar_one_or_none()

    if mapping:
        mapping.xero_account_code = xero_account_code
        mapping.xero_account_name = xero_account_name
        mapping.xero_tax_type = xero_tax_type
    else:
        mapping = XeroAccountMapping(
            category=category,
            xero_account_code=xero_account_code,
            xero_account_name=xero_account_name,
            xero_tax_type=xero_tax_type,
        )
        db.add(mapping)

    return mapping


async def get_mapping_for_category(db: AsyncSession, category: str) -> Optional[str]:
    """Get Xero account code for an expense category. Returns None if unmapped."""
    result = await db.execute(
        select(XeroAccountMapping.xero_account_code).where(XeroAccountMapping.category == category)
    )
    return result.scalar_one_or_none()


# =============================================================================
# EXPENSE SYNC TO XERO (Part F — Spend Money transactions)
# =============================================================================

async def sync_expense_to_xero(
    db: AsyncSession,
    expense: Expense,
) -> Optional[str]:
    """
    Sync an expense to Xero as a Spend Money transaction (bank transaction).

    Uses the category → account code mapping to assign the correct Xero account.
    Falls back to a default expense account if no mapping exists.

    Args:
        db: Database session
        expense: Expense to sync

    Returns:
        Xero bank transaction ID if successful, None on failure
    """
    creds = await get_valid_access_token(db)
    if not creds:
        logger.info("Xero not connected, skipping expense sync")
        return None

    access_token, tenant_id = creds

    # Get account code from mapping (or use default)
    account_code = await get_mapping_for_category(db, expense.category)
    if not account_code:
        account_code = "400"  # Default general expense account
        logger.info(f"No Xero mapping for category '{expense.category}', using default {account_code}")

    # Get bank account code from settings
    bank_account_code = settings.xero_bank_account_code

    # Build Spend Money (bank transaction) payload
    tax_type = "NONE" if expense.gst_free else "INPUT"

    line_items = [{
        "Description": expense.description or f"Expense {expense.expense_number}",
        "Quantity": 1,
        "UnitAmount": expense.amount_cents / 100,  # Ex GST
        "AccountCode": account_code,
        "TaxType": tax_type,
    }]

    bank_transaction = {
        "Type": "SPEND",
        "Contact": {
            "Name": expense.vendor or "Supplier",
        },
        "Date": expense.expense_date.isoformat(),
        "LineItems": line_items,
        "BankAccount": {"Code": bank_account_code},
        "Reference": expense.expense_number,
        "LineAmountTypes": "Exclusive",  # Amounts are ex GST
    }

    try:
        if expense.xero_bill_id:
            # Already synced — Xero doesn't support PUT updates for bank transactions
            logger.info(f"Expense {expense.expense_number} already synced to Xero ({expense.xero_bill_id})")
            return expense.xero_bill_id

        response = await _throttled_xero_request(
            "put",
            f"{XERO_API_URL}/BankTransactions",
            json={"BankTransactions": [bank_transaction]},
            headers={
                "Authorization": f"Bearer {access_token}",
                "xero-tenant-id": tenant_id,
                "Content-Type": "application/json",
            },
        )

        if response.status_code in (200, 201):
            data = response.json()
            xero_id = data["BankTransactions"][0]["BankTransactionID"]
            expense.xero_bill_id = xero_id
            expense.synced_to_xero_at = sydney_now()
            expense.xero_sync_error = None
            logger.info(f"Synced expense {expense.expense_number} to Xero as Spend Money {xero_id}")
            return xero_id
        else:
            error_msg = f"Xero API {response.status_code}: {response.text[:200]}"
            expense.xero_sync_error = error_msg
            logger.error(f"Xero expense sync failed for {expense.expense_number}: {error_msg}")
            return None

    except Exception as e:
        error_msg = str(e)[:200]
        expense.xero_sync_error = error_msg
        logger.error(f"Failed to sync expense {expense.expense_number} to Xero: {e}")
        return None


async def bulk_sync_expenses(db: AsyncSession) -> dict:
    """
    Sync all unsynced expenses to Xero.

    Processes every unsynced expense with a 1 s pause between each.

    Returns:
        Dict with synced count, failed count, and error details
    """
    result = await db.execute(
        select(Expense).where(Expense.xero_bill_id.is_(None))
    )
    all_unsynced = result.scalars().all()
    total = len(all_unsynced)

    synced = 0
    failed = 0
    errors = []

    for idx, expense in enumerate(all_unsynced, 1):
        logger.info(f"Xero bulk sync expenses: {idx}/{total}")

        xero_id = await sync_expense_to_xero(db, expense)
        if xero_id:
            synced += 1
        else:
            failed += 1
            errors.append(f"{expense.expense_number}: {expense.xero_sync_error or 'Unknown error'}")

        if idx % XERO_BULK_BATCH_SIZE == 0:
            await db.commit()

        await asyncio.sleep(1.0)

    return {
        "total": total,
        "synced": synced,
        "failed": failed,
        "errors": errors[:10],
    }
