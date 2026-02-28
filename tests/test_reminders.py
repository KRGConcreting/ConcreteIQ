"""
Reminders system tests.

Run with: pytest tests/test_reminders.py -v

Tests reminder scheduling, processing, and cancellation.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timedelta, date

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

SYDNEY_TZ = ZoneInfo("Australia/Sydney")


@pytest.fixture
def anyio_backend():
    return "asyncio"


# =============================================================================
# PAYMENT REMINDER SCHEDULING TESTS
# =============================================================================

@pytest.mark.anyio
async def test_schedule_payment_reminders_creates_four_reminders():
    """Payment reminders are scheduled at correct intervals."""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    # Create mock invoice with due date in the future
    future_date = date.today() + timedelta(days=14)
    mock_invoice = MagicMock()
    mock_invoice.id = 1
    mock_invoice.invoice_number = "INV-2026-00001"
    mock_invoice.due_date = future_date

    with patch('app.notifications.reminders.sydney_now') as mock_now:
        mock_now.return_value = datetime.now(tz=SYDNEY_TZ)

        from app.notifications.reminders import schedule_payment_reminders
        reminders = await schedule_payment_reminders(mock_db, mock_invoice)

        # Should create 5 reminders: -3 days, 0 days, +3 days, +7 days, +14 days
        assert len(reminders) == 5

        # Check 3-tier reminder types
        reminder_types = [r.reminder_type for r in reminders]
        assert reminder_types.count("payment_friendly") == 2  # -3 days and on due date
        assert reminder_types.count("payment_firm") == 1      # +3 days
        assert reminder_types.count("payment_final") == 2     # +7 days and +14 days


@pytest.mark.anyio
async def test_schedule_payment_reminders_skips_past_dates():
    """Payment reminders skip dates that are already in the past."""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    # Create mock invoice with due date tomorrow
    tomorrow = date.today() + timedelta(days=1)
    mock_invoice = MagicMock()
    mock_invoice.id = 1
    mock_invoice.invoice_number = "INV-2026-00001"
    mock_invoice.due_date = tomorrow

    with patch('app.notifications.reminders.sydney_now') as mock_now:
        now = datetime.now(tz=SYDNEY_TZ)
        mock_now.return_value = now

        from app.notifications.reminders import schedule_payment_reminders
        reminders = await schedule_payment_reminders(mock_db, mock_invoice)

        # Should skip the -3 days reminder (already past), leaving < 5
        assert len(reminders) < 5

        # All remaining should be future dates
        for reminder in reminders:
            assert reminder.scheduled_for > now


@pytest.mark.anyio
async def test_schedule_payment_reminders_without_due_date():
    """Payment reminders returns empty list if no due date."""
    mock_db = AsyncMock()

    mock_invoice = MagicMock()
    mock_invoice.id = 1
    mock_invoice.invoice_number = "INV-2026-00001"
    mock_invoice.due_date = None

    from app.notifications.reminders import schedule_payment_reminders
    reminders = await schedule_payment_reminders(mock_db, mock_invoice)

    assert reminders == []


# =============================================================================
# JOB REMINDER SCHEDULING TESTS
# =============================================================================

@pytest.mark.anyio
async def test_schedule_job_reminders_creates_two_reminders():
    """Job reminders are scheduled at correct intervals."""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    # Create mock quote with confirmed date in the future
    future_date = date.today() + timedelta(days=14)
    mock_quote = MagicMock()
    mock_quote.id = 1
    mock_quote.quote_number = "Q-2026-00001"
    mock_quote.confirmed_start_date = future_date

    with patch('app.notifications.reminders.sydney_now') as mock_now:
        mock_now.return_value = datetime.now(tz=SYDNEY_TZ)

        from app.notifications.reminders import schedule_job_reminders
        reminders = await schedule_job_reminders(mock_db, mock_quote)

        # Should create 2 reminders: 1 week before and 1 day before
        assert len(reminders) == 2

        reminder_types = [r.reminder_type for r in reminders]
        assert "job_week" in reminder_types
        assert "job_tomorrow" in reminder_types


@pytest.mark.anyio
async def test_schedule_job_reminders_skips_past_dates():
    """Job reminders skip dates that are already in the past."""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    # Create mock quote with job in 2 days (1 week before is past)
    near_date = date.today() + timedelta(days=2)
    mock_quote = MagicMock()
    mock_quote.id = 1
    mock_quote.quote_number = "Q-2026-00001"
    mock_quote.confirmed_start_date = near_date

    with patch('app.notifications.reminders.sydney_now') as mock_now:
        mock_now.return_value = datetime.now(tz=SYDNEY_TZ)

        from app.notifications.reminders import schedule_job_reminders
        reminders = await schedule_job_reminders(mock_db, mock_quote)

        # Should only have the "tomorrow" reminder (1 week before is past)
        assert len(reminders) == 1
        assert reminders[0].reminder_type == "job_tomorrow"


@pytest.mark.anyio
async def test_schedule_job_reminders_without_confirmed_date():
    """Job reminders returns empty list if no confirmed date."""
    mock_db = AsyncMock()

    mock_quote = MagicMock()
    mock_quote.id = 1
    mock_quote.quote_number = "Q-2026-00001"
    mock_quote.confirmed_start_date = None

    from app.notifications.reminders import schedule_job_reminders
    reminders = await schedule_job_reminders(mock_db, mock_quote)

    assert reminders == []


# =============================================================================
# REMINDER CANCELLATION TESTS
# =============================================================================

@pytest.mark.anyio
async def test_cancel_reminders_marks_as_cancelled():
    """Cancel reminders sets cancelled_at on pending reminders."""
    mock_db = AsyncMock()

    # Create mock pending reminders
    mock_reminder1 = MagicMock()
    mock_reminder1.sent_at = None
    mock_reminder1.cancelled_at = None

    mock_reminder2 = MagicMock()
    mock_reminder2.sent_at = None
    mock_reminder2.cancelled_at = None

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_reminder1, mock_reminder2]
    mock_db.execute.return_value = mock_result

    with patch('app.notifications.reminders.sydney_now') as mock_now:
        now = datetime.now()
        mock_now.return_value = now

        from app.notifications.reminders import cancel_reminders
        count = await cancel_reminders(mock_db, "invoice", 1)

        assert count == 2
        assert mock_reminder1.cancelled_at == now
        assert mock_reminder2.cancelled_at == now


@pytest.mark.anyio
async def test_cancel_reminders_returns_zero_when_none_found():
    """Cancel reminders returns 0 when no pending reminders found."""
    mock_db = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    from app.notifications.reminders import cancel_reminders
    count = await cancel_reminders(mock_db, "invoice", 1)

    assert count == 0


# =============================================================================
# REMINDER PROCESSING TESTS
# =============================================================================

@pytest.mark.anyio
async def test_process_due_reminders_sends_emails():
    """Process due reminders sends emails and marks as sent."""
    mock_db = AsyncMock()

    # Create mock due reminder
    mock_reminder = MagicMock()
    mock_reminder.id = 1
    mock_reminder.reminder_type = "payment_due"
    mock_reminder.entity_type = "invoice"
    mock_reminder.entity_id = 1
    mock_reminder.sent_at = None
    mock_reminder.cancelled_at = None
    mock_reminder.scheduled_for = datetime.now() - timedelta(hours=1)  # Due

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_reminder]
    mock_db.execute.return_value = mock_result

    # Mock the internal processing function
    with patch('app.notifications.reminders._process_reminder') as mock_process:
        mock_process.return_value = True

        with patch('app.notifications.reminders.sydney_now') as mock_now:
            now = datetime.now()
            mock_now.return_value = now

            from app.notifications.reminders import process_due_reminders
            count = await process_due_reminders(mock_db)

            assert count == 1
            assert mock_reminder.sent_at == now
            mock_process.assert_called_once()


@pytest.mark.anyio
async def test_process_due_reminders_idempotent():
    """Process due reminders checks sent_at before processing."""
    mock_db = AsyncMock()

    # Already sent reminder should not be in query results
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    from app.notifications.reminders import process_due_reminders
    count = await process_due_reminders(mock_db)

    assert count == 0


@pytest.mark.anyio
async def test_payment_reminder_skips_paid_invoice():
    """Payment reminder marks as processed but doesn't send for paid invoice."""
    mock_db = AsyncMock()

    # Create mock paid invoice
    mock_invoice = MagicMock()
    mock_invoice.status = "paid"
    mock_invoice.invoice_number = "INV-2026-00001"
    mock_db.get.return_value = mock_invoice

    mock_reminder = MagicMock()
    mock_reminder.entity_type = "invoice"
    mock_reminder.entity_id = 1
    mock_reminder.reminder_type = "payment_due"

    from app.notifications.reminders import _send_payment_reminder
    result = await _send_payment_reminder(mock_db, mock_reminder)

    # Should return True (mark as processed) but not actually send
    assert result is True


@pytest.mark.anyio
async def test_payment_reminder_skips_voided_invoice():
    """Payment reminder marks as processed but doesn't send for voided invoice."""
    mock_db = AsyncMock()

    mock_invoice = MagicMock()
    mock_invoice.status = "voided"
    mock_invoice.invoice_number = "INV-2026-00001"
    mock_db.get.return_value = mock_invoice

    mock_reminder = MagicMock()
    mock_reminder.entity_type = "invoice"
    mock_reminder.entity_id = 1

    from app.notifications.reminders import _send_payment_reminder
    result = await _send_payment_reminder(mock_db, mock_reminder)

    assert result is True


@pytest.mark.anyio
async def test_job_reminder_skips_non_confirmed_quote():
    """Job reminder marks as processed but doesn't send for non-confirmed quote."""
    mock_db = AsyncMock()

    mock_quote = MagicMock()
    mock_quote.status = "accepted"  # Not confirmed
    mock_quote.quote_number = "Q-2026-00001"
    mock_db.get.return_value = mock_quote

    mock_reminder = MagicMock()
    mock_reminder.entity_type = "quote"
    mock_reminder.entity_id = 1

    from app.notifications.reminders import _send_job_reminder
    result = await _send_job_reminder(mock_db, mock_reminder)

    assert result is True


# =============================================================================
# REMINDER EMAIL SENDING TESTS
# =============================================================================

@pytest.mark.anyio
async def test_payment_reminder_sends_email():
    """Payment reminder sends email via send_email function."""
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
    mock_invoice.portal_token = "test-token-123"

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
    mock_reminder.reminder_type = "payment_due"

    with patch('app.notifications.reminders.send_email') as mock_send:
        mock_send.return_value = True

        with patch('app.notifications.reminders.templates') as mock_templates:
            mock_templates.get_template.return_value.render.return_value = "<html>Test</html>"

            from app.notifications.reminders import _send_payment_reminder
            result = await _send_payment_reminder(mock_db, mock_reminder)

            assert result is True
            mock_send.assert_called_once()


@pytest.mark.anyio
async def test_reminder_skips_customer_without_email():
    """Reminder marks as processed when customer has no email."""
    mock_db = AsyncMock()

    mock_invoice = MagicMock()
    mock_invoice.id = 1
    mock_invoice.invoice_number = "INV-2026-00001"
    mock_invoice.status = "sent"
    mock_invoice.customer_id = 2

    mock_customer = MagicMock()
    mock_customer.id = 2
    mock_customer.email = None  # No email

    # Use model class to distinguish Invoice vs Customer lookups
    from app.models import Invoice as InvoiceModel, Customer as CustomerModel
    async def _get_side_effect(model, id):
        if model is InvoiceModel:
            return mock_invoice
        if model is CustomerModel:
            return mock_customer
        return None
    mock_db.get.side_effect = _get_side_effect

    mock_reminder = MagicMock()
    mock_reminder.entity_type = "invoice"
    mock_reminder.entity_id = 1

    from app.notifications.reminders import _send_payment_reminder
    result = await _send_payment_reminder(mock_db, mock_reminder)

    # Should return True to mark as processed (avoid retrying forever)
    assert result is True


@pytest.mark.anyio
async def test_reminder_respects_notify_email_preference():
    """Reminder marks as processed when customer disabled email notifications."""
    mock_db = AsyncMock()

    mock_invoice = MagicMock()
    mock_invoice.id = 1
    mock_invoice.invoice_number = "INV-2026-00001"
    mock_invoice.status = "sent"
    mock_invoice.customer_id = 2

    mock_customer = MagicMock()
    mock_customer.id = 2
    mock_customer.email = "test@example.com"
    mock_customer.notify_email = False  # Disabled

    # Use model class to distinguish Invoice vs Customer lookups
    from app.models import Invoice as InvoiceModel, Customer as CustomerModel
    async def _get_side_effect(model, id):
        if model is InvoiceModel:
            return mock_invoice
        if model is CustomerModel:
            return mock_customer
        return None
    mock_db.get.side_effect = _get_side_effect

    mock_reminder = MagicMock()
    mock_reminder.entity_type = "invoice"
    mock_reminder.entity_id = 1

    from app.notifications.reminders import _send_payment_reminder
    result = await _send_payment_reminder(mock_db, mock_reminder)

    # Should return True to mark as processed
    assert result is True


# =============================================================================
# GET PENDING REMINDERS TESTS
# =============================================================================

@pytest.mark.anyio
async def test_get_pending_reminders_filters_correctly():
    """Get pending reminders returns only unsent, uncancelled reminders."""
    mock_db = AsyncMock()

    mock_reminder = MagicMock()
    mock_reminder.id = 1
    mock_reminder.reminder_type = "payment_due"
    mock_reminder.entity_type = "invoice"
    mock_reminder.entity_id = 1
    mock_reminder.scheduled_for = datetime.now() + timedelta(days=1)
    mock_reminder.sent_at = None
    mock_reminder.cancelled_at = None

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_reminder]
    mock_db.execute.return_value = mock_result

    from app.notifications.reminders import get_pending_reminders
    reminders = await get_pending_reminders(mock_db)

    assert len(reminders) == 1
    assert reminders[0].id == 1


@pytest.mark.anyio
async def test_get_pending_reminders_with_filters():
    """Get pending reminders can filter by entity type and ID."""
    mock_db = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    from app.notifications.reminders import get_pending_reminders
    reminders = await get_pending_reminders(mock_db, entity_type="invoice", entity_id=123)

    assert reminders == []
    # Check that execute was called (query was built)
    mock_db.execute.assert_called_once()
