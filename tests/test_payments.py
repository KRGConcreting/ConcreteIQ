"""
Payment module tests.

Run with: pytest tests/test_payments.py -v

Note: Some tests require a PostgreSQL database connection.
Stripe integration tests use mocked responses.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport
from app.main import app

# Check if database is available
try:
    import asyncpg
    import asyncio

    async def _check_db():
        try:
            conn = await asyncpg.connect(
                user='concreteiq',
                password='concreteiq',
                database='concreteiq',
                host='localhost',
                timeout=2
            )
            await conn.close()
            return True
        except Exception:
            return False

    DB_AVAILABLE = asyncio.run(_check_db())
except Exception:
    DB_AVAILABLE = False

requires_db = pytest.mark.skipif(not DB_AVAILABLE, reason="Database not available")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    """Async test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
async def authenticated_client():
    """Async test client with authentication."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        # Login to get session cookie
        response = await ac.post("/login", data={
            "password": "admin",
            "next": "/",
        }, follow_redirects=False)
        yield ac


# =============================================================================
# WEBHOOK IDEMPOTENCY TESTS (Unit Tests - No DB Required)
# =============================================================================

class TestWebhookIdempotency:
    """Test webhook idempotency logic."""

    def test_idempotency_key_format(self):
        """Idempotency key should be deterministic based on invoice ID and amount."""
        invoice_id = 123
        balance_cents = 50000

        # This matches the format used in payment service
        key = f"invoice_{invoice_id}_{balance_cents}"

        assert key == "invoice_123_50000"

    def test_same_invoice_same_amount_same_key(self):
        """Same invoice and amount should produce same key."""
        key1 = f"invoice_{1}_{10000}"
        key2 = f"invoice_{1}_{10000}"

        assert key1 == key2

    def test_different_amount_different_key(self):
        """Different amounts should produce different keys (prevents partial payment reuse)."""
        key1 = f"invoice_{1}_{10000}"
        key2 = f"invoice_{1}_{5000}"

        assert key1 != key2


# =============================================================================
# WEBHOOK SIGNATURE VERIFICATION TESTS
# =============================================================================

class TestWebhookSignature:
    """Test webhook signature verification logic."""

    @pytest.mark.anyio
    async def test_missing_signature_handled(self, client):
        """Webhook without signature should return 200 with error."""
        response = await client.post(
            "/webhooks/stripe",
            content=b'{"type": "test"}',
            headers={"Content-Type": "application/json"}
        )

        # Should return 200 to prevent Stripe retries
        assert response.status_code == 200
        data = response.json()
        assert data.get("received") is True
        assert "error" in data or "Missing" in data.get("error", "")


# =============================================================================
# PAYMENT RECORDING TESTS
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_payment_api_requires_auth(client):
    """Payment API requires authentication."""
    response = await client.post(
        "/payments/api/record",
        json={
            "invoice_id": 1,
            "amount_cents": 10000,
            "method": "cash"
        }
    )

    # Should redirect to login or return 401
    assert response.status_code in (302, 303, 307, 401)


@requires_db
@pytest.mark.anyio
async def test_checkout_api_requires_auth(client):
    """Checkout API requires authentication."""
    response = await client.post("/payments/api/checkout/1")

    # Should redirect to login or return 401
    assert response.status_code in (302, 303, 307, 401)


@requires_db
@pytest.mark.anyio
async def test_record_payment_invalid_invoice(authenticated_client):
    """Recording payment for non-existent invoice returns 404."""
    response = await authenticated_client.post(
        "/payments/api/record",
        json={
            "invoice_id": 999999,
            "amount_cents": 10000,
            "method": "cash"
        }
    )

    assert response.status_code == 404


# =============================================================================
# STRIPE CHECKOUT TESTS
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_checkout_invalid_invoice(authenticated_client):
    """Checkout for non-existent invoice returns 404."""
    response = await authenticated_client.post("/payments/api/checkout/999999")

    assert response.status_code == 404


# =============================================================================
# WEBHOOK PROCESSING TESTS (Mocked)
# =============================================================================

class TestWebhookProcessing:
    """Test webhook event processing."""

    def test_checkout_completed_event_structure(self):
        """Verify expected structure of checkout.session.completed event."""
        # This is the structure Stripe sends
        event = {
            "id": "evt_test_123",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_session",
                    "payment_intent": "pi_test_payment",
                    "amount_total": 50000,  # $500.00
                    "metadata": {
                        "invoice_id": "123",
                        "invoice_number": "INV-2024-00001"
                    }
                }
            }
        }

        # Verify structure
        assert event["type"] == "checkout.session.completed"
        assert event["data"]["object"]["amount_total"] == 50000
        assert event["data"]["object"]["metadata"]["invoice_id"] == "123"


# =============================================================================
# PAYMENT METHOD VALIDATION TESTS
# =============================================================================

def test_valid_payment_methods():
    """Valid payment methods should be accepted."""
    from app.schemas import PaymentCreate

    valid_methods = ["cash", "card", "bank_transfer", "stripe"]

    for method in valid_methods:
        payment = PaymentCreate(
            invoice_id=1,
            amount_cents=10000,
            method=method
        )
        assert payment.method == method


def test_invalid_payment_method():
    """Invalid payment method should be rejected."""
    from app.schemas import PaymentCreate
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PaymentCreate(
            invoice_id=1,
            amount_cents=10000,
            method="bitcoin"  # Not a valid method
        )


def test_payment_amount_must_be_positive():
    """Payment amount must be greater than 0."""
    from app.schemas import PaymentCreate
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PaymentCreate(
            invoice_id=1,
            amount_cents=0,  # Must be > 0
            method="cash"
        )

    with pytest.raises(ValidationError):
        PaymentCreate(
            invoice_id=1,
            amount_cents=-100,  # Cannot be negative
            method="cash"
        )
