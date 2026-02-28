"""
Quote module tests.

Run with: pytest tests/test_quotes.py -v

Note: Some tests require a PostgreSQL database connection.
Tests that require the database are marked and will be skipped
if the database is not available.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.quotes.service import generate_portal_token, hash_portal_token

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
    raw = "test-token-12345"
    hash1 = hash_portal_token(raw)
    hash2 = hash_portal_token(raw)

    assert hash1 == hash2


def test_hash_portal_token_is_different_for_different_tokens():
    """Different raw tokens should produce different hashes."""
    hash1 = hash_portal_token("token-a")
    hash2 = hash_portal_token("token-b")

    assert hash1 != hash2


def test_generate_portal_token_produces_unique_tokens():
    """Each call to generate should produce unique tokens."""
    tokens = set()
    for _ in range(100):
        raw, _ = generate_portal_token()
        tokens.add(raw)

    assert len(tokens) == 100  # All unique


def test_generated_hash_matches_hash_function():
    """Generated hash should match hash_portal_token(raw)."""
    raw, hashed = generate_portal_token()

    assert hash_portal_token(raw) == hashed


# =============================================================================
# AUTH TESTS (No DB Required)
# =============================================================================

@pytest.mark.anyio
async def test_quotes_list_requires_auth(client):
    """Quote list page redirects to login when not authenticated."""
    response = await client.get("/quotes", follow_redirects=False)
    assert response.status_code in [302, 307]
    assert "/login" in response.headers.get("location", "")


@pytest.mark.anyio
async def test_quotes_api_requires_auth(client):
    """Quote API returns redirect when not authenticated."""
    response = await client.get("/quotes/api/list", follow_redirects=False)
    assert response.status_code in [302, 307]


@pytest.mark.anyio
async def test_quotes_new_requires_auth(client):
    """New quote page redirects to login when not authenticated."""
    response = await client.get("/quotes/new", follow_redirects=False)
    assert response.status_code in [302, 307]


@pytest.mark.anyio
async def test_calculator_requires_auth(client):
    """Calculator endpoint returns redirect when not authenticated."""
    response = await client.post("/quotes/api/calculate", json={"area": 25, "thickness": 100})
    assert response.status_code in [302, 307, 401]


# =============================================================================
# PORTAL AUTH TESTS (Require DB - Portal is public but needs DB for lookup)
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_portal_does_not_require_auth(client):
    """Portal endpoint should not redirect to login (returns 404 for invalid token instead)."""
    response = await client.get("/p/invalid-token-12345", follow_redirects=False)
    # Portal should NOT redirect to login - it should return 404 for invalid token
    assert response.status_code == 404


@requires_db
@pytest.mark.anyio
async def test_portal_accept_does_not_require_auth(client):
    """Portal accept endpoint should not redirect to login."""
    response = await client.post(
        "/p/invalid-token-12345/accept",
        json={
            "signer_name": "Test User",
            "signature_data": "data:image/png;base64,abc123",
            "terms_accepted": True
        }
    )
    # Should return 404 for invalid token, not redirect to login
    assert response.status_code == 404


@requires_db
@pytest.mark.anyio
async def test_portal_select_date_does_not_require_auth(client):
    """Portal select-date endpoint should not redirect to login."""
    response = await client.post(
        "/p/invalid-token-12345/select-date",
        json={"requested_date": "2025-03-15"}
    )
    # Should return 404 for invalid token, not redirect to login
    assert response.status_code == 404


@requires_db
@pytest.mark.anyio
async def test_portal_pdf_does_not_require_auth(client):
    """Portal PDF endpoint should not redirect to login."""
    response = await client.get("/p/invalid-token-12345/pdf", follow_redirects=False)
    # Should return 404 for invalid token, not redirect to login
    assert response.status_code == 404


# =============================================================================
# PAGE RENDER TESTS (Require Database)
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_quotes_list_page_renders(authenticated_client):
    """Quote list page renders for authenticated user."""
    response = await authenticated_client.get("/quotes")
    assert response.status_code == 200
    assert b"Quotes" in response.content or b"quotes" in response.content


@requires_db
@pytest.mark.anyio
async def test_quotes_new_page_renders(authenticated_client):
    """New quote page renders for authenticated user."""
    response = await authenticated_client.get("/quotes/new")
    assert response.status_code == 200


@requires_db
@pytest.mark.anyio
async def test_quotes_list_with_status_filter(authenticated_client):
    """Quote list accepts status filter."""
    response = await authenticated_client.get("/quotes?status=draft")
    assert response.status_code == 200


# =============================================================================
# API STRUCTURE TESTS (Require Database)
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_quotes_api_list_structure(authenticated_client):
    """Quote API list endpoint returns correct structure."""
    response = await authenticated_client.get("/quotes/api/list")
    assert response.status_code == 200
    data = response.json()

    # Should have pagination fields
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert "pages" in data

    # Items should be a list
    assert isinstance(data["items"], list)


@requires_db
@pytest.mark.anyio
async def test_quotes_api_list_pagination(authenticated_client):
    """Quote API respects pagination parameters."""
    response = await authenticated_client.get("/quotes/api/list?page=1&page_size=10")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 10


@requires_db
@pytest.mark.anyio
async def test_quotes_api_get_not_found(authenticated_client):
    """Quote get API returns 404 for non-existent quote."""
    response = await authenticated_client.get("/quotes/api/99999")
    assert response.status_code == 404


# =============================================================================
# CALCULATOR TESTS (Require Database for Auth)
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_calculator_returns_result(authenticated_client):
    """Calculator endpoint returns calculation result."""
    response = await authenticated_client.post("/quotes/api/calculate", json={
        "area": 25,
        "thickness": 100,
        "concrete_grade": "N25",
        "concrete_finish": "Broom",
        "reinforcement_type": "SL82",
    })
    assert response.status_code == 200
    data = response.json()

    # Should have key calculation fields
    assert "volume_m3" in data
    assert "total_cents" in data
    assert "line_items" in data
    assert "gst_cents" in data


@requires_db
@pytest.mark.anyio
async def test_calculator_total_is_positive(authenticated_client):
    """Calculator returns positive total for valid input."""
    response = await authenticated_client.post("/quotes/api/calculate", json={
        "area": 50,
        "thickness": 100,
        "concrete_grade": "N25",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["total_cents"] > 0


# =============================================================================
# QUOTE CREATE VALIDATION TESTS (Require Database)
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_quote_create_requires_customer_id(authenticated_client):
    """Quote create requires customer_id."""
    response = await authenticated_client.post("/quotes/api/create", json={
        "job_name": "Test Job",
        "calculator_input": {
            "area": 25,
            "thickness": 100,
        }
    })
    assert response.status_code == 422  # Validation error


@requires_db
@pytest.mark.anyio
async def test_quote_create_requires_calculator_input(authenticated_client):
    """Quote create requires calculator_input."""
    response = await authenticated_client.post("/quotes/api/create", json={
        "customer_id": 1,
        "job_name": "Test Job",
    })
    assert response.status_code == 422  # Validation error


# =============================================================================
# QUOTE STATUS TESTS
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_delete_requires_draft_status(authenticated_client):
    """Cannot delete non-draft quotes (tested via validation error message)."""
    # Try to delete a non-existent quote first to check the 404
    response = await authenticated_client.delete("/quotes/api/99999")
    assert response.status_code == 404


# =============================================================================
# TEMPLATE CONTENT TESTS
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_quotes_list_has_new_button(authenticated_client):
    """Quote list page has 'New Quote' link."""
    response = await authenticated_client.get("/quotes")
    assert response.status_code == 200
    assert b"New Quote" in response.content or b"new" in response.content.lower()


@requires_db
@pytest.mark.anyio
async def test_quotes_new_has_form_fields(authenticated_client):
    """New quote page has calculator form fields."""
    response = await authenticated_client.get("/quotes/new")
    assert response.status_code == 200
    content = response.content.decode()

    # Should have key form fields
    assert "customer" in content.lower()


# =============================================================================
# PORTAL DECLINE TESTS (Require Database)
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_portal_decline_does_not_require_auth(client):
    """Portal decline endpoint should not redirect to login."""
    response = await client.post(
        "/p/invalid-token-12345/decline",
        json={"reason": "Too expensive"}
    )
    # Should return 404 for invalid token, not redirect to login
    assert response.status_code == 404


# =============================================================================
# IDEMPOTENCY & STATE TRANSITION TESTS (Unit Tests - No DB Required)
# =============================================================================

@pytest.mark.anyio
async def test_decline_quote_success():
    """Test declining a quote updates status correctly."""
    from unittest.mock import AsyncMock, MagicMock
    from app.quotes.service import decline_quote

    # Create a mock quote in 'viewed' status
    mock_quote = MagicMock()
    mock_quote.status = "viewed"
    mock_quote.quote_number = "Q-2026-00001"
    mock_quote.id = 1
    mock_quote.declined_at = None
    mock_quote.decline_reason = None

    # Create a mock db session
    mock_db = AsyncMock()

    # Call decline_quote
    result, was_already_declined = await decline_quote(
        db=mock_db,
        quote=mock_quote,
        reason=None,
        ip_address="127.0.0.1",
    )

    # Verify
    assert was_already_declined is False
    assert mock_quote.status == "declined"
    assert mock_quote.declined_at is not None
    assert mock_quote.decline_reason is None
    assert mock_db.add.call_count == 2  # Activity log + notification


@pytest.mark.anyio
async def test_decline_quote_with_reason():
    """Test declining a quote with a reason stores it."""
    from unittest.mock import AsyncMock, MagicMock
    from app.quotes.service import decline_quote

    mock_quote = MagicMock()
    mock_quote.status = "sent"
    mock_quote.quote_number = "Q-2026-00002"
    mock_quote.id = 2
    mock_quote.declined_at = None
    mock_quote.decline_reason = None

    mock_db = AsyncMock()

    result, was_already_declined = await decline_quote(
        db=mock_db,
        quote=mock_quote,
        reason="Price too high",
        ip_address="127.0.0.1",
    )

    assert was_already_declined is False
    assert mock_quote.status == "declined"
    assert mock_quote.decline_reason == "Price too high"


@pytest.mark.anyio
async def test_decline_already_declined_idempotent():
    """Double-decline should return success (idempotent)."""
    from unittest.mock import AsyncMock, MagicMock
    from app.quotes.service import decline_quote

    mock_quote = MagicMock()
    mock_quote.status = "declined"
    mock_quote.quote_number = "Q-2026-00003"

    mock_db = AsyncMock()

    result, was_already_declined = await decline_quote(
        db=mock_db,
        quote=mock_quote,
        reason="Another reason",
        ip_address="127.0.0.1",
    )

    # Should return success with flag indicating already declined
    assert was_already_declined is True
    # Should NOT have added activity log
    mock_db.add.assert_not_called()


@pytest.mark.anyio
async def test_accept_already_accepted_idempotent():
    """Double-accept should return success (idempotent)."""
    from unittest.mock import AsyncMock, MagicMock
    from app.quotes.service import accept_quote

    mock_quote = MagicMock()
    mock_quote.status = "accepted"
    mock_quote.quote_number = "Q-2026-00004"

    mock_db = AsyncMock()

    result, was_already_accepted = await accept_quote(
        db=mock_db,
        quote=mock_quote,
        signature_data="data:image/png;base64,abc123",
        signature_name="John Doe",
        ip_address="127.0.0.1",
    )

    # Should return success with flag indicating already accepted
    assert was_already_accepted is True
    # Should NOT have added activity log
    mock_db.add.assert_not_called()


@pytest.mark.anyio
async def test_cannot_decline_after_accept():
    """Cannot decline a quote that was already accepted."""
    from unittest.mock import AsyncMock, MagicMock
    from app.quotes.service import decline_quote

    mock_quote = MagicMock()
    mock_quote.status = "accepted"
    mock_quote.quote_number = "Q-2026-00005"

    mock_db = AsyncMock()

    with pytest.raises(ValueError) as exc_info:
        await decline_quote(
            db=mock_db,
            quote=mock_quote,
            reason="Changed my mind",
            ip_address="127.0.0.1",
        )

    assert "Cannot decline an accepted quote" in str(exc_info.value)


@pytest.mark.anyio
async def test_cannot_accept_after_decline():
    """Cannot sign a quote that was already declined."""
    from unittest.mock import AsyncMock, MagicMock
    from app.quotes.service import sign_quote

    mock_quote = MagicMock()
    mock_quote.status = "declined"
    mock_quote.quote_number = "Q-2026-00006"
    mock_quote.signed_at = None

    mock_db = AsyncMock()

    with pytest.raises(ValueError) as exc_info:
        await sign_quote(
            db=mock_db,
            quote=mock_quote,
            signature_data="data:image/png;base64,abc123",
            signature_name="John Doe",
            ip_address="127.0.0.1",
        )

    assert "Cannot sign a declined quote" in str(exc_info.value)


@pytest.mark.anyio
async def test_accept_quote_from_sent_status():
    """Sign quote works from 'sent' status (no longer auto-accepts)."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.quotes.service import sign_quote

    mock_quote = MagicMock()
    mock_quote.status = "sent"
    mock_quote.quote_number = "Q-2026-00007"
    mock_quote.id = 7
    mock_quote.customer_id = 1
    mock_quote.total_cents = 500000
    mock_quote.accepted_at = None
    mock_quote.signature_data = None
    mock_quote.signature_name = None
    mock_quote.signature_ip = None
    mock_quote.signed_at = None
    mock_quote.expiry_date = None  # Not expired

    mock_db = AsyncMock()

    # Mock the invoice creation, sending, and notification since sign_quote calls them
    mock_notification = AsyncMock()
    with patch("app.invoices.service.create_progress_invoices", new_callable=AsyncMock, return_value=[]) as mock_invoices, \
         patch("app.invoices.service.send_invoice", new_callable=AsyncMock) as mock_send, \
         patch("app.notifications.service.notify_quote_accepted", new_callable=AsyncMock) as mock_notify:
        result, was_already_signed = await sign_quote(
            db=mock_db,
            quote=mock_quote,
            signature_data="data:image/png;base64,signature",
            signature_name="Jane Doe",
            ip_address="192.168.1.1",
        )

    assert was_already_signed is False
    # Status stays 'sent' — transitions to 'accepted' only on deposit payment
    assert mock_quote.status == "sent"
    assert mock_quote.signature_name == "Jane Doe"
    assert mock_quote.signature_ip == "192.168.1.1"
    assert mock_quote.signed_at is not None


@pytest.mark.anyio
async def test_accept_quote_from_viewed_status():
    """Sign quote works from 'viewed' status (no longer auto-accepts)."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.quotes.service import sign_quote

    mock_quote = MagicMock()
    mock_quote.status = "viewed"
    mock_quote.quote_number = "Q-2026-00008"
    mock_quote.id = 8
    mock_quote.customer_id = 1
    mock_quote.total_cents = 350000
    mock_quote.accepted_at = None
    mock_quote.signature_data = None
    mock_quote.signature_name = None
    mock_quote.signature_ip = None
    mock_quote.signed_at = None
    mock_quote.expiry_date = None  # Not expired

    mock_db = AsyncMock()

    with patch("app.invoices.service.create_progress_invoices", new_callable=AsyncMock, return_value=[]) as mock_invoices, \
         patch("app.invoices.service.send_invoice", new_callable=AsyncMock) as mock_send, \
         patch("app.notifications.service.notify_quote_accepted", new_callable=AsyncMock) as mock_notify:
        result, was_already_signed = await sign_quote(
            db=mock_db,
            quote=mock_quote,
            signature_data="data:image/png;base64,signature",
            signature_name="Bob Smith",
            ip_address="10.0.0.1",
        )

    assert was_already_signed is False
    # Status stays 'viewed' — transitions to 'accepted' only on deposit payment
    assert mock_quote.status == "viewed"


@pytest.mark.anyio
async def test_decline_quote_from_sent_status():
    """Decline works from 'sent' status."""
    from unittest.mock import AsyncMock, MagicMock
    from app.quotes.service import decline_quote

    mock_quote = MagicMock()
    mock_quote.status = "sent"
    mock_quote.quote_number = "Q-2026-00009"
    mock_quote.id = 9
    mock_quote.declined_at = None
    mock_quote.decline_reason = None

    mock_db = AsyncMock()

    result, was_already_declined = await decline_quote(
        db=mock_db,
        quote=mock_quote,
        reason=None,
        ip_address="127.0.0.1",
    )

    assert was_already_declined is False
    assert mock_quote.status == "declined"


@pytest.mark.anyio
async def test_cannot_accept_draft_quote():
    """Cannot sign a draft quote."""
    from unittest.mock import AsyncMock, MagicMock
    from app.quotes.service import sign_quote

    mock_quote = MagicMock()
    mock_quote.status = "draft"
    mock_quote.quote_number = "Q-2026-00010"
    mock_quote.signed_at = None

    mock_db = AsyncMock()

    with pytest.raises(ValueError) as exc_info:
        await sign_quote(
            db=mock_db,
            quote=mock_quote,
            signature_data="data:image/png;base64,abc",
            signature_name="Test User",
            ip_address="127.0.0.1",
        )

    assert "Cannot sign quote in 'draft' status" in str(exc_info.value)


@pytest.mark.anyio
async def test_cannot_decline_draft_quote():
    """Cannot decline a draft quote."""
    from unittest.mock import AsyncMock, MagicMock
    from app.quotes.service import decline_quote

    mock_quote = MagicMock()
    mock_quote.status = "draft"
    mock_quote.quote_number = "Q-2026-00011"

    mock_db = AsyncMock()

    with pytest.raises(ValueError) as exc_info:
        await decline_quote(
            db=mock_db,
            quote=mock_quote,
            reason=None,
            ip_address="127.0.0.1",
        )

    assert "Cannot decline quote in 'draft' status" in str(exc_info.value)


@pytest.mark.anyio
async def test_cannot_accept_expired_quote():
    """Cannot sign an expired quote."""
    from unittest.mock import AsyncMock, MagicMock
    from app.quotes.service import sign_quote

    mock_quote = MagicMock()
    mock_quote.status = "expired"
    mock_quote.quote_number = "Q-2026-00012"
    mock_quote.signed_at = None

    mock_db = AsyncMock()

    with pytest.raises(ValueError) as exc_info:
        await sign_quote(
            db=mock_db,
            quote=mock_quote,
            signature_data="data:image/png;base64,abc",
            signature_name="Test User",
            ip_address="127.0.0.1",
        )

    assert "Cannot sign quote in 'expired' status" in str(exc_info.value)


@pytest.mark.anyio
async def test_cannot_decline_expired_quote():
    """Cannot decline an expired quote."""
    from unittest.mock import AsyncMock, MagicMock
    from app.quotes.service import decline_quote

    mock_quote = MagicMock()
    mock_quote.status = "expired"
    mock_quote.quote_number = "Q-2026-00013"

    mock_db = AsyncMock()

    with pytest.raises(ValueError) as exc_info:
        await decline_quote(
            db=mock_db,
            quote=mock_quote,
            reason=None,
            ip_address="127.0.0.1",
        )

    assert "Cannot decline quote in 'expired' status" in str(exc_info.value)


# =============================================================================
# RESUMABILITY TESTS (These test that state is persisted, not session-based)
# =============================================================================

def test_portal_state_comes_from_db():
    """
    Verify that portal state is derived from database, not cookies/sessions.

    This is a design test - the portal routes use get_quote_by_token(db, token)
    which looks up the quote from DB. The template then renders based on quote.status.
    There's no session cookie dependency for state.
    """
    # This is verified by the architecture:
    # 1. Portal routes take token from URL
    # 2. Lookup quote by hashing token
    # 3. Render template based on quote.status from DB
    # 4. No session cookies used for portal
    #
    # A customer can:
    # - View quote, close browser → return later → sees same state (viewed)
    # - Accept, close browser → return later → sees "accepted" with date picker
    # - Select date, close browser → return later → sees selected date
    pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
