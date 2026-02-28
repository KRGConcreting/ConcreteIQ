"""
Xero integration tests.

Run with: pytest tests/test_xero.py -v

Tests OAuth flow, token management, and sync functions.
Xero API calls are mocked to avoid requiring real credentials.
"""

import asyncio
import time

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from datetime import datetime, timedelta

from app.integrations.xero import (
    get_authorization_url,
    _encrypt_token,
    _decrypt_token,
    get_xero_connection_status,
    _XeroRateLimiter,
    _throttled_xero_request,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# =============================================================================
# OAUTH URL GENERATION TESTS (No API Required)
# =============================================================================

def test_authorization_url_generation():
    """Authorization URL contains required OAuth parameters."""
    with patch('app.integrations.xero.settings') as mock_settings:
        mock_settings.xero_client_id = "test-client-id"
        mock_settings.xero_redirect_uri = "https://example.com/callback"
        mock_settings.xero_scopes = "openid profile"
        mock_settings.app_url = "https://example.com"

        url = get_authorization_url(state="test-state-123")

        assert "login.xero.com" in url
        assert "client_id=test-client-id" in url
        assert "redirect_uri=" in url
        assert "response_type=code" in url
        assert "state=test-state-123" in url


def test_authorization_url_requires_client_id():
    """Authorization URL raises error without client ID."""
    with patch('app.integrations.xero.settings') as mock_settings:
        mock_settings.xero_client_id = None

        with pytest.raises(ValueError) as exc_info:
            get_authorization_url()

        assert "client id" in str(exc_info.value).lower()


def test_authorization_url_generates_state_if_not_provided():
    """Authorization URL generates random state if not provided."""
    with patch('app.integrations.xero.settings') as mock_settings:
        mock_settings.xero_client_id = "test-client-id"
        mock_settings.xero_redirect_uri = "https://example.com/callback"
        mock_settings.xero_scopes = "openid profile"
        mock_settings.app_url = "https://example.com"

        url = get_authorization_url()

        assert "state=" in url


# =============================================================================
# TOKEN ENCRYPTION TESTS (No API Required)
# =============================================================================

def test_token_encryption_roundtrip():
    """Encrypted token can be decrypted back to original."""
    with patch('app.integrations.xero.settings') as mock_settings:
        mock_settings.secret_key = "test-secret-key-for-encryption"
        # Reset the cached key
        import app.integrations.xero as xero_module
        xero_module._encryption_key = None

        original = "test-access-token-12345"
        encrypted = _encrypt_token(original)
        decrypted = _decrypt_token(encrypted)

        assert encrypted != original  # Should be encrypted
        assert decrypted == original  # Should decrypt back


def test_token_encryption_different_for_same_input():
    """Same token encrypted twice produces different ciphertext (due to IV)."""
    with patch('app.integrations.xero.settings') as mock_settings:
        mock_settings.secret_key = "test-secret-key-for-encryption"
        import app.integrations.xero as xero_module
        xero_module._encryption_key = None

        original = "test-access-token-12345"
        encrypted1 = _encrypt_token(original)
        encrypted2 = _encrypt_token(original)

        # Fernet uses random IV, so ciphertexts should differ
        # But both should decrypt to same value
        assert encrypted1 != encrypted2
        assert _decrypt_token(encrypted1) == original
        assert _decrypt_token(encrypted2) == original


def test_empty_token_encryption():
    """Empty token returns empty string."""
    with patch('app.integrations.xero.settings') as mock_settings:
        mock_settings.secret_key = "test-secret-key-for-encryption"
        import app.integrations.xero as xero_module
        xero_module._encryption_key = None

        assert _encrypt_token("") == ""
        assert _decrypt_token("") == ""


# =============================================================================
# TOKEN REFRESH TESTS (Mocked API)
# =============================================================================

@pytest.mark.anyio
async def test_refresh_access_token_calls_xero_api():
    """Token refresh makes correct API call to Xero."""
    with patch('app.integrations.xero.settings') as mock_settings:
        mock_settings.xero_client_id = "test-client-id"
        mock_settings.xero_client_secret = "test-client-secret"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 1800,
        }

        with patch('httpx.AsyncClient') as mock_client:
            mock_client_instance = AsyncMock()
            mock_client_instance.post.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            from app.integrations.xero import refresh_access_token
            result = await refresh_access_token("old-refresh-token")

            assert result["access_token"] == "new-access-token"
            assert result["refresh_token"] == "new-refresh-token"
            mock_client_instance.post.assert_called_once()


@pytest.mark.anyio
async def test_refresh_token_handles_api_error():
    """Token refresh raises exception on API error."""
    with patch('app.integrations.xero.settings') as mock_settings:
        mock_settings.xero_client_id = "test-client-id"
        mock_settings.xero_client_secret = "test-client-secret"

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Invalid refresh token"

        with patch('httpx.AsyncClient') as mock_client:
            mock_client_instance = AsyncMock()
            mock_client_instance.post.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            from app.integrations.xero import refresh_access_token

            with pytest.raises(Exception) as exc_info:
                await refresh_access_token("bad-refresh-token")

            assert "failed" in str(exc_info.value).lower()


# =============================================================================
# INVOICE SYNC TESTS (Mocked API)
# =============================================================================

@pytest.mark.anyio
async def test_sync_invoice_to_xero_without_connection():
    """Invoice sync returns None when Xero not connected."""
    mock_db = AsyncMock()

    with patch('app.integrations.xero.get_valid_access_token') as mock_token:
        mock_token.return_value = None

        from app.integrations.xero import sync_invoice_to_xero

        # Create mock invoice
        mock_invoice = MagicMock()
        mock_invoice.invoice_number = "INV-2026-00001"

        result = await sync_invoice_to_xero(mock_db, mock_invoice)

        assert result is None


@pytest.mark.anyio
async def test_sync_invoice_creates_xero_invoice():
    """Invoice sync creates invoice in Xero and returns ID."""
    mock_db = AsyncMock()

    # Mock customer loaded from db.get()
    mock_customer = MagicMock()
    mock_customer.xero_contact_id = "xero-contact-123"
    mock_customer.name = "Test Customer"
    mock_customer.email = "test@example.com"
    mock_customer.phone = None
    mock_customer.phone2 = None
    mock_customer.street = None
    mock_customer.city = None
    mock_customer.business_name = None
    mock_db.get.return_value = mock_customer

    with patch('app.integrations.xero.get_valid_access_token') as mock_token:
        mock_token.return_value = ("test-access-token", "test-tenant-id")

        with patch('app.integrations.xero.sync_customer_to_xero') as mock_customer_sync:
            mock_customer_sync.return_value = "xero-contact-123"

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "Invoices": [{"InvoiceID": "xero-invoice-456"}]
            }

            with patch('httpx.AsyncClient') as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.post.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                from app.integrations.xero import sync_invoice_to_xero

                # Create mock invoice
                mock_invoice = MagicMock()
                mock_invoice.invoice_number = "INV-2026-00001"
                mock_invoice.customer_id = 1
                mock_invoice.xero_invoice_id = None
                mock_invoice.subtotal_cents = 100000
                mock_invoice.line_items = None
                mock_invoice.description = "Test Invoice"
                mock_invoice.issue_date = datetime.now().date()
                mock_invoice.due_date = datetime.now().date()

                result = await sync_invoice_to_xero(mock_db, mock_invoice)

                assert result == "xero-invoice-456"
                assert mock_invoice.xero_invoice_id == "xero-invoice-456"


# =============================================================================
# PAYMENT SYNC TESTS (Mocked API)
# =============================================================================

@pytest.mark.anyio
async def test_sync_payment_without_invoice_xero_id():
    """Payment sync attempts to sync invoice first if no Xero ID (safety net)."""
    mock_db = AsyncMock()

    # Mock db.get to return the invoice when loading by ID
    mock_invoice = MagicMock()
    mock_invoice.xero_invoice_id = None
    mock_invoice.invoice_number = "INV-2026-00001"
    mock_db.get.return_value = mock_invoice

    with patch('app.integrations.xero.get_valid_access_token') as mock_token:
        mock_token.return_value = ("test-access-token", "test-tenant-id")

        with patch('app.integrations.xero.sync_invoice_to_xero') as mock_invoice_sync:
            mock_invoice_sync.return_value = None  # Invoice sync fails

            from app.integrations.xero import sync_payment_to_xero

            # Create mock payment with invoice_id
            mock_payment = MagicMock()
            mock_payment.id = 1
            mock_payment.invoice_id = 42

            result = await sync_payment_to_xero(mock_db, mock_payment)

            assert result is None
            mock_invoice_sync.assert_called_once()


# =============================================================================
# CONNECTION STATUS TESTS
# =============================================================================

@pytest.mark.anyio
async def test_connection_status_not_connected():
    """Connection status returns not connected when no token."""
    mock_db = AsyncMock()

    with patch('app.integrations.xero.get_xero_token') as mock_get_token:
        mock_get_token.return_value = None

        status = await get_xero_connection_status(mock_db)

        assert status["connected"] is False
        assert status["tenant_id"] is None


@pytest.mark.anyio
async def test_connection_status_connected():
    """Connection status returns connected with token details."""
    mock_db = AsyncMock()

    mock_token = MagicMock()
    mock_token.extra_data = {"tenant_id": "test-tenant-123"}
    mock_token.expires_at = datetime.now() + timedelta(hours=1)
    mock_token.updated_at = datetime.now()

    with patch('app.integrations.xero.get_xero_token') as mock_get_token:
        mock_get_token.return_value = mock_token

        with patch('app.integrations.xero.sydney_now') as mock_now:
            mock_now.return_value = datetime.now()

            status = await get_xero_connection_status(mock_db)

            assert status["connected"] is True
            assert status["tenant_id"] == "test-tenant-123"
            assert status["is_expired"] is False


@pytest.mark.anyio
async def test_connection_status_expired():
    """Connection status shows expired when token is expired."""
    mock_db = AsyncMock()

    mock_token = MagicMock()
    mock_token.extra_data = {"tenant_id": "test-tenant-123"}
    mock_token.expires_at = datetime.now() - timedelta(hours=1)  # Expired
    mock_token.updated_at = datetime.now() - timedelta(hours=2)

    with patch('app.integrations.xero.get_xero_token') as mock_get_token:
        mock_get_token.return_value = mock_token

        with patch('app.integrations.xero.sydney_now') as mock_now:
            mock_now.return_value = datetime.now()

            status = await get_xero_connection_status(mock_db)

            assert status["connected"] is True
            assert status["is_expired"] is True


# =============================================================================
# XERO SYNC GRACEFUL FAILURE TESTS
# =============================================================================

@pytest.mark.anyio
async def test_sync_fails_gracefully_on_api_error():
    """Sync returns None instead of raising on API error."""
    mock_db = AsyncMock()

    # Mock customer loaded from db.get()
    mock_customer = MagicMock()
    mock_customer.xero_contact_id = "contact-123"
    mock_customer.name = "Test"
    mock_customer.email = "test@example.com"
    mock_customer.phone = None
    mock_customer.phone2 = None
    mock_customer.street = None
    mock_customer.city = None
    mock_customer.business_name = None
    mock_db.get.return_value = mock_customer

    with patch('app.integrations.xero.get_valid_access_token') as mock_token:
        mock_token.return_value = ("test-access-token", "test-tenant-id")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch('httpx.AsyncClient') as mock_client:
            mock_client_instance = AsyncMock()
            mock_client_instance.post.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            from app.integrations.xero import sync_invoice_to_xero

            mock_invoice = MagicMock()
            mock_invoice.invoice_number = "INV-2026-00001"
            mock_invoice.customer_id = 1
            mock_invoice.xero_invoice_id = None
            mock_invoice.subtotal_cents = 100000
            mock_invoice.line_items = None
            mock_invoice.description = "Test"
            mock_invoice.issue_date = datetime.now().date()
            mock_invoice.due_date = datetime.now().date()

            # Should NOT raise, just return None
            result = await sync_invoice_to_xero(mock_db, mock_invoice)

            assert result is None


@pytest.mark.anyio
async def test_sync_fails_gracefully_on_network_error():
    """Sync returns None instead of raising on network error."""
    mock_db = AsyncMock()

    # Mock customer loaded from db.get()
    mock_customer = MagicMock()
    mock_customer.xero_contact_id = "contact-123"
    mock_customer.name = "Test"
    mock_customer.email = "test@example.com"
    mock_customer.phone = None
    mock_customer.phone2 = None
    mock_customer.street = None
    mock_customer.city = None
    mock_customer.business_name = None
    mock_db.get.return_value = mock_customer

    with patch('app.integrations.xero.get_valid_access_token') as mock_token:
        mock_token.return_value = ("test-access-token", "test-tenant-id")

        with patch('httpx.AsyncClient') as mock_client:
            mock_client_instance = AsyncMock()
            mock_client_instance.post.side_effect = Exception("Network error")
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            from app.integrations.xero import sync_invoice_to_xero

            mock_invoice = MagicMock()
            mock_invoice.invoice_number = "INV-2026-00001"
            mock_invoice.customer_id = 1
            mock_invoice.xero_invoice_id = None
            mock_invoice.subtotal_cents = 100000
            mock_invoice.line_items = None
            mock_invoice.description = "Test"
            mock_invoice.issue_date = datetime.now().date()
            mock_invoice.due_date = datetime.now().date()

            # Should NOT raise, just return None
            result = await sync_invoice_to_xero(mock_db, mock_invoice)

            assert result is None


# =============================================================================
# RATE LIMITER TESTS
# =============================================================================

@pytest.mark.anyio
async def test_rate_limiter_allows_calls_within_limit():
    """Rate limiter does not block when under the limit."""
    limiter = _XeroRateLimiter(max_calls=5, period=60.0)

    start = time.monotonic()
    for _ in range(5):
        await limiter.acquire()
    elapsed = time.monotonic() - start

    # 5 calls within limit should complete almost instantly (< 1s)
    assert elapsed < 1.0


@pytest.mark.anyio
async def test_rate_limiter_blocks_when_limit_exceeded():
    """Rate limiter blocks when call count exceeds the limit."""
    # Tiny window (2s) and low limit (2 calls) for fast testing
    limiter = _XeroRateLimiter(max_calls=2, period=2.0)

    # First 2 calls should be instant
    await limiter.acquire()
    await limiter.acquire()

    # 3rd call should block until the window slides (~2s)
    start = time.monotonic()
    await limiter.acquire()
    elapsed = time.monotonic() - start

    # Should have waited roughly 2 seconds (±0.5s tolerance)
    assert elapsed >= 1.5
    assert elapsed < 3.0


@pytest.mark.anyio
async def test_throttled_request_makes_api_call():
    """_throttled_xero_request makes an HTTP request and returns response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True}

    with patch('httpx.AsyncClient') as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get.return_value = mock_response
        mock_client.return_value.__aenter__.return_value = mock_instance

        # Use a fresh limiter for this test to avoid cross-test contamination
        with patch('app.integrations.xero._xero_limiter', _XeroRateLimiter(max_calls=50, period=60.0)):
            response = await _throttled_xero_request(
                "get",
                "https://api.xero.com/api.xro/2.0/Contacts",
                headers={"Authorization": "Bearer test", "Content-Type": "application/json"},
            )

    assert response.status_code == 200
    mock_instance.get.assert_called_once()


@pytest.mark.anyio
async def test_throttled_request_retries_on_429():
    """_throttled_xero_request retries after 429 with Retry-After header."""
    # First response: 429 with short Retry-After
    mock_429 = MagicMock()
    mock_429.status_code = 429
    mock_429.headers = {"Retry-After": "1"}

    # Second response: 200 success
    mock_200 = MagicMock()
    mock_200.status_code = 200
    mock_200.json.return_value = {"ok": True}

    with patch('httpx.AsyncClient') as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get.side_effect = [mock_429, mock_200]
        mock_client.return_value.__aenter__.return_value = mock_instance

        with patch('app.integrations.xero._xero_limiter', _XeroRateLimiter(max_calls=50, period=60.0)):
            response = await _throttled_xero_request(
                "get",
                "https://api.xero.com/api.xro/2.0/Contacts",
                headers={"Authorization": "Bearer test", "Content-Type": "application/json"},
                _retries=3,
            )

    # Should have retried and returned the 200
    assert response.status_code == 200
    assert mock_instance.get.call_count == 2


@pytest.mark.anyio
async def test_throttled_request_exhausts_retries_on_persistent_429():
    """_throttled_xero_request returns last 429 if all retries exhausted."""
    mock_429 = MagicMock()
    mock_429.status_code = 429
    mock_429.headers = {"Retry-After": "1"}

    with patch('httpx.AsyncClient') as mock_client:
        mock_instance = AsyncMock()
        mock_instance.post.return_value = mock_429
        mock_client.return_value.__aenter__.return_value = mock_instance

        with patch('app.integrations.xero._xero_limiter', _XeroRateLimiter(max_calls=50, period=60.0)):
            response = await _throttled_xero_request(
                "post",
                "https://api.xero.com/api.xro/2.0/Invoices",
                headers={"Authorization": "Bearer test", "Content-Type": "application/json"},
                json={"Invoices": []},
                _retries=2,
            )

    # Should return the 429 after exhausting retries
    assert response.status_code == 429
    assert mock_instance.post.call_count == 2


@pytest.mark.anyio
async def test_oauth_token_exchange_is_not_throttled():
    """OAuth token exchange uses raw httpx, not _throttled_xero_request."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "new-token",
        "refresh_token": "new-refresh",
        "expires_in": 1800,
    }

    with patch('app.integrations.xero.settings') as mock_settings:
        mock_settings.xero_client_id = "test-id"
        mock_settings.xero_client_secret = "test-secret"

        with patch('httpx.AsyncClient') as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_instance

            # Patch limiter to track if it's called
            with patch('app.integrations.xero._xero_limiter') as mock_limiter:
                from app.integrations.xero import exchange_code_for_tokens
                result = await exchange_code_for_tokens("auth-code-123")

                # Limiter should NOT have been called
                mock_limiter.acquire.assert_not_called()

    assert result["access_token"] == "new-token"
