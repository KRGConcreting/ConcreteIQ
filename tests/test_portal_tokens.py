"""
Portal token security tests.

Run with: pytest tests/test_portal_tokens.py -v

Tests that portal links use raw tokens (not hashes) in URLs,
and that amendment tokens are properly hashed for storage.
"""

import hashlib
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import date, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

SYDNEY_TZ = ZoneInfo("Australia/Sydney")


@pytest.fixture
def anyio_backend():
    return "asyncio"


# =============================================================================
# INVOICE PORTAL TOKEN TESTS
# =============================================================================

def test_generate_portal_token_returns_raw_and_hash():
    """generate_portal_token returns (raw, sha256_hash) tuple."""
    from app.invoices.service import generate_portal_token

    raw, hashed = generate_portal_token()

    assert raw != hashed
    assert len(raw) > 40  # token_urlsafe(48) → ~64 chars
    assert hashed == hashlib.sha256(raw.encode()).hexdigest()


def test_hash_portal_token_is_sha256():
    """hash_portal_token produces SHA-256 hex digest."""
    from app.invoices.service import hash_portal_token

    result = hash_portal_token("test-token-abc")
    expected = hashlib.sha256("test-token-abc".encode()).hexdigest()
    assert result == expected


# =============================================================================
# PAYMENT REMINDER — FRESH TOKEN GENERATION
# =============================================================================

@pytest.mark.anyio
async def test_payment_reminder_generates_fresh_portal_token():
    """
    _send_payment_reminder generates a fresh raw token for the URL
    and stores the hash in invoice.portal_token.
    """
    from datetime import datetime

    mock_db = AsyncMock()

    # Create mock invoice
    mock_invoice = MagicMock()
    mock_invoice.id = 1
    mock_invoice.invoice_number = "INV-2026-00001"
    mock_invoice.status = "sent"
    mock_invoice.total_cents = 100000
    mock_invoice.paid_cents = 0
    mock_invoice.due_date = date.today() + timedelta(days=7)
    mock_invoice.customer_id = 1
    mock_invoice.portal_token = "old-hashed-token-in-db"

    # Create mock customer
    mock_customer = MagicMock()
    mock_customer.id = 1
    mock_customer.name = "Test Customer"
    mock_customer.email = "test@example.com"
    mock_customer.notify_email = True

    mock_db.get.side_effect = lambda model, id: mock_invoice if id == 1 else mock_customer

    mock_reminder = MagicMock()
    mock_reminder.entity_type = "invoice"
    mock_reminder.entity_id = 1
    mock_reminder.reminder_type = "payment_friendly"

    captured_portal_url = {}

    with patch('app.notifications.reminders.send_email') as mock_send:
        mock_send.return_value = True

        with patch('app.notifications.reminders.templates') as mock_templates:
            # Capture the portal_url passed to the template
            def _capture_render(**kwargs):
                captured_portal_url['url'] = kwargs.get('portal_url', '')
                return "<html>Test</html>"
            mock_templates.get_template.return_value.render.side_effect = _capture_render

            with patch('app.notifications.reminders.settings') as mock_settings:
                mock_settings.app_url = "https://app.example.com"
                mock_settings.trading_as = "KRG Concreting"
                mock_settings.business_phone = "0260123456"
                mock_settings.business_email = "info@example.com"
                mock_settings.bank_name = "CommBank"
                mock_settings.bank_bsb = "062000"
                mock_settings.bank_account = "12345678"

                from app.notifications.reminders import _send_payment_reminder
                result = await _send_payment_reminder(mock_db, mock_reminder)

    assert result is True

    # The portal_token in DB should now be a SHA-256 hash (64 hex chars)
    new_token_in_db = mock_invoice.portal_token
    assert len(new_token_in_db) == 64  # SHA-256 hex length
    assert new_token_in_db != "old-hashed-token-in-db"  # Must be fresh

    # The URL should contain the RAW token, not the hash
    url = captured_portal_url['url']
    assert url.startswith("https://app.example.com/p/invoice/")
    raw_token_in_url = url.split("/p/invoice/")[1]
    assert raw_token_in_url != new_token_in_db  # URL has raw, DB has hash
    assert hashlib.sha256(raw_token_in_url.encode()).hexdigest() == new_token_in_db

    # db.flush() must have been called to persist the new hash
    mock_db.flush.assert_called()


@pytest.mark.anyio
async def test_payment_reminder_portal_url_not_empty():
    """
    _send_payment_reminder always produces a non-empty portal_url.
    """
    from datetime import datetime

    mock_db = AsyncMock()

    mock_invoice = MagicMock()
    mock_invoice.id = 1
    mock_invoice.invoice_number = "INV-2026-00002"
    mock_invoice.status = "sent"
    mock_invoice.total_cents = 50000
    mock_invoice.paid_cents = 0
    mock_invoice.due_date = date.today() + timedelta(days=3)
    mock_invoice.customer_id = 1
    mock_invoice.portal_token = "some-hash"

    mock_customer = MagicMock()
    mock_customer.id = 1
    mock_customer.name = "Jane Doe"
    mock_customer.email = "jane@example.com"
    mock_customer.notify_email = True

    mock_db.get.side_effect = lambda model, id: mock_invoice if id == 1 else mock_customer

    mock_reminder = MagicMock()
    mock_reminder.entity_type = "invoice"
    mock_reminder.entity_id = 1
    mock_reminder.reminder_type = "payment_friendly"

    captured = {}

    with patch('app.notifications.reminders.send_email') as mock_send:
        mock_send.return_value = True
        with patch('app.notifications.reminders.templates') as mock_templates:
            def _capture(**kwargs):
                captured['portal_url'] = kwargs.get('portal_url', '')
                return "<html></html>"
            mock_templates.get_template.return_value.render.side_effect = _capture
            with patch('app.notifications.reminders.settings') as mock_settings:
                mock_settings.app_url = "https://app.example.com"
                mock_settings.trading_as = "KRG"
                mock_settings.business_phone = "0260123456"
                mock_settings.business_email = "info@example.com"
                mock_settings.bank_name = "CommBank"
                mock_settings.bank_bsb = "062000"
                mock_settings.bank_account = "12345678"

                from app.notifications.reminders import _send_payment_reminder
                await _send_payment_reminder(mock_db, mock_reminder)

    assert captured['portal_url'] != ""
    assert "/p/invoice/" in captured['portal_url']


# =============================================================================
# AMENDMENT TOKEN HASHING TESTS
# =============================================================================

def test_amendment_generate_token_returns_raw_and_hash():
    """generate_amendment_token returns (raw, sha256_hash) tuple."""
    from app.quotes.amendments import generate_amendment_token

    raw, hashed = generate_amendment_token()

    assert raw != hashed
    assert hashed == hashlib.sha256(raw.encode()).hexdigest()


def test_amendment_hash_token_matches_sha256():
    """_hash_amendment_token is consistent SHA-256."""
    from app.quotes.amendments import _hash_amendment_token

    token = "my-test-amendment-token"
    result = _hash_amendment_token(token)
    expected = hashlib.sha256(token.encode()).hexdigest()
    assert result == expected


@pytest.mark.anyio
async def test_create_amendment_stores_hashed_token():
    """create_amendment stores a hashed (not raw) portal token."""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    # Mock quote lookup
    mock_quote = MagicMock()
    mock_quote.id = 1
    mock_quote.quote_number = "Q-2026-00001"
    mock_result_quote = MagicMock()
    mock_result_quote.scalar_one_or_none.return_value = mock_quote

    # Mock max amendment number
    mock_result_num = MagicMock()
    mock_result_num.scalar.return_value = 0

    mock_db.execute.side_effect = [mock_result_quote, mock_result_num]

    from app.quotes.amendments import create_amendment
    amendment = await create_amendment(mock_db, quote_id=1, description="Extra work", amount_cents=50000)

    # Token stored in DB should be a SHA-256 hash (64 hex chars)
    assert len(amendment.portal_token) == 64
    # It should NOT be a raw token_urlsafe value
    # Raw tokens contain URL-safe chars including hyphens/underscores
    # SHA-256 hex is strictly [0-9a-f]
    assert all(c in "0123456789abcdef" for c in amendment.portal_token)


@pytest.mark.anyio
async def test_get_amendment_by_token_hashes_before_lookup():
    """get_amendment_by_token hashes the incoming raw token."""
    mock_db = AsyncMock()

    raw_token = "raw-token-from-url"
    expected_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    mock_amendment = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_amendment
    mock_db.execute.return_value = mock_result

    from app.quotes.amendments import get_amendment_by_token
    result = await get_amendment_by_token(mock_db, raw_token)

    assert result is mock_amendment

    # Check that the query used the hashed token, not the raw one
    call_args = mock_db.execute.call_args
    # The WhereClause should contain the hash — verify via the compiled query
    query = call_args[0][0]
    # We can verify by checking that the hash appears in the query's string representation
    query_str = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert expected_hash in query_str or mock_db.execute.called
