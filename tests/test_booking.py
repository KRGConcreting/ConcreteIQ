"""
Booking confirmation tests.

Run with: pytest tests/test_booking.py -v

Tests the booking confirmation flow from quote acceptance to invoice creation.
"""

import pytest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
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
async def authenticated_client():
    """Async test client with authentication."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        # Login to get session cookie
        await ac.post("/login", data={
            "password": "admin",
            "next": "/",
        }, follow_redirects=False)
        yield ac


# =============================================================================
# STATUS TRANSITION TESTS (No DB Required)
# =============================================================================


class TestBookingStatusTransitions:
    """Test status transition validation for booking confirmation."""

    def test_valid_transitions_include_accepted_to_confirmed(self):
        """Status transitions allow accepted -> confirmed."""
        from app.quotes.service import VALID_TRANSITIONS

        assert "confirmed" in VALID_TRANSITIONS.get("accepted", [])

    def test_cannot_confirm_from_draft(self):
        """Cannot confirm a draft quote."""
        from app.quotes.service import validate_status_transition

        assert validate_status_transition("draft", "confirmed") is False

    def test_cannot_confirm_from_sent(self):
        """Cannot confirm a sent quote."""
        from app.quotes.service import validate_status_transition

        assert validate_status_transition("sent", "confirmed") is False

    def test_cannot_confirm_from_declined(self):
        """Cannot confirm a declined quote."""
        from app.quotes.service import validate_status_transition

        assert validate_status_transition("declined", "confirmed") is False


# =============================================================================
# CONFIRM BOOKING SERVICE TESTS
# =============================================================================


class TestConfirmBookingService:
    """Test confirm_booking service function."""

    @pytest.mark.anyio
    async def test_confirm_booking_requires_accepted_status(self):
        """confirm_booking raises ValueError if quote not accepted."""
        from app.quotes.service import confirm_booking

        # Mock quote in 'sent' status
        mock_quote = MagicMock()
        mock_quote.status = "sent"

        mock_db = AsyncMock()
        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"

        with pytest.raises(ValueError) as exc_info:
            await confirm_booking(
                db=mock_db,
                quote=mock_quote,
                confirmed_date=date.today(),
                request=mock_request
            )

        assert "Cannot confirm booking" in str(exc_info.value)
        assert "sent" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_confirm_booking_requires_accepted_not_declined(self):
        """confirm_booking raises ValueError if quote is declined."""
        from app.quotes.service import confirm_booking

        mock_quote = MagicMock()
        mock_quote.status = "declined"

        mock_db = AsyncMock()
        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"

        with pytest.raises(ValueError) as exc_info:
            await confirm_booking(
                db=mock_db,
                quote=mock_quote,
                confirmed_date=date.today(),
                request=mock_request
            )

        assert "Cannot confirm booking" in str(exc_info.value)


# =============================================================================
# API ENDPOINT TESTS (Require DB)
# =============================================================================


@requires_db
@pytest.mark.anyio
async def test_confirm_booking_endpoint_requires_auth(client):
    """Confirm booking endpoint requires authentication."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/quotes/api/1/confirm-booking",
            json={"confirmed_date": "2024-03-15"}
        )

        # Should redirect to login or return 401
        assert response.status_code in (302, 303, 307, 401)


@requires_db
@pytest.mark.anyio
async def test_confirm_booking_nonexistent_quote(authenticated_client):
    """Confirm booking returns 404 for non-existent quote."""
    response = await authenticated_client.post(
        "/quotes/api/999999/confirm-booking",
        json={"confirmed_date": "2024-03-15"}
    )

    assert response.status_code == 404


@requires_db
@pytest.mark.anyio
async def test_confirm_booking_requires_date(authenticated_client):
    """Confirm booking requires a date in the request body."""
    response = await authenticated_client.post(
        "/quotes/api/1/confirm-booking",
        json={}
    )

    # Should fail validation
    assert response.status_code == 422


# =============================================================================
# SCHEMA VALIDATION TESTS
# =============================================================================


class TestConfirmBookingSchema:
    """Test ConfirmBookingRequest schema validation."""

    def test_schema_requires_date(self):
        """Schema requires confirmed_date field."""
        from app.schemas import ConfirmBookingRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ConfirmBookingRequest()

    def test_schema_accepts_valid_date(self):
        """Schema accepts valid date."""
        from app.schemas import ConfirmBookingRequest

        request = ConfirmBookingRequest(confirmed_date=date(2024, 3, 15))

        assert request.confirmed_date == date(2024, 3, 15)

    def test_schema_accepts_date_string(self):
        """Schema accepts date string in ISO format."""
        from app.schemas import ConfirmBookingRequest

        request = ConfirmBookingRequest(confirmed_date="2024-03-15")

        assert request.confirmed_date == date(2024, 3, 15)


# =============================================================================
# INVOICE CREATION TESTS
# =============================================================================


class TestBookingInvoiceCreation:
    """Test that booking confirmation creates correct invoice."""

    def test_booking_stage_is_30_percent(self):
        """Booking invoice should be 30% of quote total."""
        from app.invoices.service import calculate_stage_amount

        total_cents = 100000  # $1,000
        booking_amount = calculate_stage_amount(total_cents, "booking")

        assert booking_amount == 30000  # $300

    def test_booking_stage_calculation_for_real_amount(self):
        """Booking calculation works for typical quote amounts."""
        from app.invoices.service import calculate_stage_amount

        # Typical concreting job: $5,500
        total_cents = 550000
        booking_amount = calculate_stage_amount(total_cents, "booking")

        # 30% of $5,500 = $1,650 = 165000 cents
        assert booking_amount == 165000
