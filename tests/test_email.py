"""
Email service tests.

Run with: pytest tests/test_email.py -v

Tests the Postmark email integration with mocked HTTP calls.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import date

# =============================================================================
# EMAIL SERVICE UNIT TESTS (No DB or Network Required)
# =============================================================================


class TestEmailServiceCore:
    """Test email service core functionality."""

    def test_postmark_api_url_configured(self):
        """Postmark API URL is correct."""
        from app.notifications.email import POSTMARK_API_URL

        assert POSTMARK_API_URL == "https://api.postmarkapp.com/email"

    @pytest.mark.anyio
    async def test_send_email_returns_false_without_api_key(self):
        """Email returns False when API key not configured."""
        from app.notifications.email import send_email

        with patch("app.notifications.email.settings") as mock_settings:
            mock_settings.postmark_api_key = None

            result = await send_email(
                to="test@example.com",
                subject="Test",
                html_body="<p>Test</p>"
            )

            assert result is False

    @pytest.mark.anyio
    async def test_send_email_returns_false_without_recipient(self):
        """Email returns False when no recipient provided."""
        from app.notifications.email import send_email

        with patch("app.notifications.email.settings") as mock_settings:
            mock_settings.postmark_api_key = "test-key"

            result = await send_email(
                to="",
                subject="Test",
                html_body="<p>Test</p>"
            )

            assert result is False

    @pytest.mark.anyio
    async def test_send_email_success(self):
        """Email returns True on successful send."""
        from app.notifications.email import send_email

        with patch("app.notifications.email.settings") as mock_settings:
            mock_settings.postmark_api_key = "test-key"
            mock_settings.postmark_from_email = "test@example.com"

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"MessageID": "test-123"}
                mock_client.post.return_value = mock_response
                mock_client.__aenter__.return_value = mock_client
                mock_client_class.return_value = mock_client

                result = await send_email(
                    to="recipient@example.com",
                    subject="Test Subject",
                    html_body="<p>Test Body</p>"
                )

                assert result is True

    @pytest.mark.anyio
    async def test_send_email_failure_returns_false(self):
        """Email returns False on API error."""
        from app.notifications.email import send_email

        with patch("app.notifications.email.settings") as mock_settings:
            mock_settings.postmark_api_key = "test-key"
            mock_settings.postmark_from_email = "test@example.com"

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_response = MagicMock()
                mock_response.status_code = 422  # Postmark error
                mock_response.text = "Invalid email"
                mock_client.post.return_value = mock_response
                mock_client.__aenter__.return_value = mock_client
                mock_client_class.return_value = mock_client

                result = await send_email(
                    to="recipient@example.com",
                    subject="Test Subject",
                    html_body="<p>Test Body</p>"
                )

                assert result is False

    @pytest.mark.anyio
    async def test_send_email_timeout_returns_false(self):
        """Email returns False on timeout."""
        import httpx
        from app.notifications.email import send_email

        with patch("app.notifications.email.settings") as mock_settings:
            mock_settings.postmark_api_key = "test-key"
            mock_settings.postmark_from_email = "test@example.com"

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.post.side_effect = httpx.TimeoutException("Timeout")
                mock_client.__aenter__.return_value = mock_client
                mock_client_class.return_value = mock_client

                result = await send_email(
                    to="recipient@example.com",
                    subject="Test Subject",
                    html_body="<p>Test Body</p>"
                )

                assert result is False


class TestQuoteEmail:
    """Test quote email functionality."""

    @pytest.mark.anyio
    async def test_quote_email_requires_customer_email(self):
        """Quote email returns False if customer has no email."""
        from app.notifications.email import send_quote_email

        # Mock quote
        mock_quote = MagicMock()
        mock_quote.quote_number = "Q-2024-00001"
        mock_quote.total_cents = 100000

        # Mock customer without email
        mock_customer = MagicMock()
        mock_customer.id = 1
        mock_customer.email = None
        mock_customer.notify_email = True

        result = await send_quote_email(
            db=None,
            quote=mock_quote,
            customer=mock_customer,
            portal_url="http://example.com/portal/quote/abc123"
        )

        assert result is False

    @pytest.mark.anyio
    async def test_quote_email_respects_notification_preference(self):
        """Quote email returns False if customer disabled email notifications."""
        from app.notifications.email import send_quote_email

        mock_quote = MagicMock()
        mock_quote.quote_number = "Q-2024-00001"

        mock_customer = MagicMock()
        mock_customer.id = 1
        mock_customer.email = "test@example.com"
        mock_customer.notify_email = False

        result = await send_quote_email(
            db=None,
            quote=mock_quote,
            customer=mock_customer,
            portal_url="http://example.com/portal/quote/abc123"
        )

        assert result is False


class TestInvoiceEmail:
    """Test invoice email functionality."""

    @pytest.mark.anyio
    async def test_invoice_email_requires_customer_email(self):
        """Invoice email returns False if customer has no email."""
        from app.notifications.email import send_invoice_email

        mock_invoice = MagicMock()
        mock_invoice.invoice_number = "INV-2024-00001"

        mock_customer = MagicMock()
        mock_customer.id = 1
        mock_customer.email = None
        mock_customer.notify_email = True

        result = await send_invoice_email(
            db=None,
            invoice=mock_invoice,
            customer=mock_customer,
            portal_url="http://example.com/portal/invoice/abc123"
        )

        assert result is False


class TestPaymentReceiptEmail:
    """Test payment receipt email functionality."""

    @pytest.mark.anyio
    async def test_receipt_email_requires_customer_email(self):
        """Receipt email returns False if customer has no email."""
        from app.notifications.email import send_payment_receipt_email

        mock_payment = MagicMock()
        mock_payment.amount_cents = 30000
        mock_payment.method = "stripe"
        mock_payment.payment_date = date(2024, 3, 15)

        mock_invoice = MagicMock()
        mock_invoice.invoice_number = "INV-2024-00001"
        mock_invoice.total_cents = 100000
        mock_invoice.paid_cents = 30000

        mock_customer = MagicMock()
        mock_customer.id = 1
        mock_customer.email = None
        mock_customer.notify_email = True

        result = await send_payment_receipt_email(
            db=None,
            payment=mock_payment,
            invoice=mock_invoice,
            customer=mock_customer
        )

        assert result is False
