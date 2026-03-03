"""
Invoice module tests.

Run with: pytest tests/test_invoices.py -v

Note: Some tests require a PostgreSQL database connection.
Tests that require the database are marked and will be skipped
if the database is not available.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.invoices.service import (
    generate_portal_token,
    hash_portal_token,
    calculate_stage_amount,
)
from app.core.money import calculate_payment_split

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
            "password": "admin",  # Default password from settings
            "next": "/",
        }, follow_redirects=False)
        yield ac


# =============================================================================
# PORTAL TOKEN TESTS (No DB Required)
# =============================================================================

def test_generate_portal_token_returns_tuple():
    """Portal token generation returns (raw, hashed) tuple."""
    raw, hashed = generate_portal_token()

    assert isinstance(raw, str)
    assert isinstance(hashed, str)
    assert len(raw) == 64  # token_urlsafe(48) = 64 chars
    assert len(hashed) == 64  # SHA256 hex = 64 chars
    assert raw != hashed  # Should not be the same


def test_hash_portal_token_is_consistent():
    """Same raw token should produce same hash."""
    raw = "test-invoice-token-12345"
    hash1 = hash_portal_token(raw)
    hash2 = hash_portal_token(raw)

    assert hash1 == hash2


def test_hash_portal_token_is_different_for_different_tokens():
    """Different raw tokens should produce different hashes."""
    hash1 = hash_portal_token("invoice-token-a")
    hash2 = hash_portal_token("invoice-token-b")

    assert hash1 != hash2


# =============================================================================
# STAGE AMOUNT CALCULATIONS (No DB Required)
# =============================================================================

class TestStageAmounts:
    """Test invoice stage amount calculations."""

    def test_booking_is_30_percent(self):
        """Booking stage is 30% of total."""
        total_cents = 100000  # $1,000.00
        amount = calculate_stage_amount(total_cents, "booking")

        # 30% of $1,000 = $300.00 = 30000 cents
        assert amount == 30000

    def test_prepour_is_60_percent(self):
        """Pre-pour stage is 60% of total."""
        total_cents = 100000  # $1,000.00
        amount = calculate_stage_amount(total_cents, "prepour")

        # 60% of $1,000 = $600.00 = 60000 cents
        assert amount == 60000

    def test_completion_is_10_percent(self):
        """Completion stage is 10% of total."""
        total_cents = 100000  # $1,000.00
        amount = calculate_stage_amount(total_cents, "completion")

        # 10% of $1,000 = $100.00 = 10000 cents
        assert amount == 10000

    def test_stages_sum_to_total(self):
        """All stages should sum to 100%."""
        total_cents = 334210  # $3,342.10 - real KRG quote amount

        booking = calculate_stage_amount(total_cents, "booking")
        prepour = calculate_stage_amount(total_cents, "prepour")
        completion = calculate_stage_amount(total_cents, "completion")

        # Should equal original total
        assert booking + prepour + completion == total_cents

    def test_invalid_stage_raises_error(self):
        """Invalid stage should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            calculate_stage_amount(100000, "invalid")

        assert "Unknown stage" in str(exc_info.value)

    def test_payment_split_matches_stages(self):
        """Payment split function should match stage calculations.

        calculate_payment_split() returns canonical keys: deposit, prepour, final.
        calculate_stage_amount() accepts both canonical and legacy names.
        """
        total_cents = 100000

        split = calculate_payment_split(total_cents)

        # Use canonical keys from split dict, with legacy stage names for stage_amount
        assert split["deposit"] == calculate_stage_amount(total_cents, "booking")
        assert split["deposit"] == calculate_stage_amount(total_cents, "deposit")
        assert split["prepour"] == calculate_stage_amount(total_cents, "prepour")
        assert split["final"] == calculate_stage_amount(total_cents, "completion")
        assert split["final"] == calculate_stage_amount(total_cents, "final")


# =============================================================================
# AUTH TESTS
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_invoices_list_requires_auth(client):
    """Invoice list page requires authentication."""
    response = await client.get("/invoices", follow_redirects=False)

    # Should redirect to login
    assert response.status_code in (302, 303, 307)
    assert "/login" in response.headers.get("location", "")


@requires_db
@pytest.mark.anyio
async def test_invoices_api_requires_auth(client):
    """Invoice API requires authentication."""
    response = await client.get("/invoices/api/list")

    # Should redirect to login or return 401
    assert response.status_code in (302, 303, 307, 401)


# =============================================================================
# PAGE RENDER TESTS
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_invoices_list_page_renders(authenticated_client):
    """Invoice list page renders for authenticated user."""
    response = await authenticated_client.get("/invoices")

    assert response.status_code == 200
    assert b"Invoices" in response.content


# =============================================================================
# API TESTS
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_invoices_api_list(authenticated_client):
    """Invoice API list returns paginated results."""
    response = await authenticated_client.get("/invoices/api/list")

    assert response.status_code == 200
    data = response.json()

    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert "pages" in data


@requires_db
@pytest.mark.anyio
async def test_invoice_not_found(authenticated_client):
    """Non-existent invoice returns 404."""
    response = await authenticated_client.get("/invoices/api/999999")

    assert response.status_code == 404


# =============================================================================
# PORTAL TESTS (No Auth Required)
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_invoice_portal_invalid_token(client):
    """Invalid portal token returns 404."""
    response = await client.get("/p/invoice/invalid-token-12345")

    assert response.status_code == 404


# =============================================================================
# GST CALCULATION TESTS
# =============================================================================

def test_gst_calculated_correctly():
    """GST should be 10% of subtotal."""
    from app.core.money import calculate_gst, add_gst

    subtotal_cents = 100000  # $1,000.00
    gst_cents = calculate_gst(subtotal_cents)

    # 10% of $1,000 = $100 = 10000 cents
    assert gst_cents == 10000

    # Total should be subtotal + GST
    total = add_gst(subtotal_cents)
    assert total == 110000  # $1,100.00
