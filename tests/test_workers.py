"""
Worker and job assignment tests.

Run with: pytest tests/test_workers.py -v

Tests worker CRUD operations and job assignments.
"""

import pytest
from datetime import date, datetime
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
# SCHEMA VALIDATION TESTS (No DB Required)
# =============================================================================


class TestWorkerSchemas:
    """Test worker schema validation."""

    def test_worker_create_requires_name(self):
        """WorkerCreate requires name field."""
        from app.schemas import WorkerCreate
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WorkerCreate(role="labourer")

    def test_worker_create_accepts_valid_data(self):
        """WorkerCreate accepts valid data."""
        from app.schemas import WorkerCreate

        worker = WorkerCreate(
            name="John Smith",
            role="finisher",
            hourly_rate_cents=5000,
            phone="0400000000",
            email="john@example.com",
        )

        assert worker.name == "John Smith"
        assert worker.role == "finisher"
        assert worker.hourly_rate_cents == 5000

    def test_worker_create_default_role(self):
        """WorkerCreate defaults to labourer role."""
        from app.schemas import WorkerCreate

        worker = WorkerCreate(name="John Smith")

        assert worker.role == "labourer"

    def test_worker_create_invalid_role(self):
        """WorkerCreate rejects invalid role."""
        from app.schemas import WorkerCreate
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WorkerCreate(name="John Smith", role="invalid_role")

    def test_worker_create_validates_email(self):
        """WorkerCreate validates email format."""
        from app.schemas import WorkerCreate
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WorkerCreate(name="John Smith", email="not-an-email")


class TestJobAssignmentSchemas:
    """Test job assignment schema validation."""

    def test_assignment_create_requires_ids(self):
        """JobAssignmentCreate requires quote_id and worker_id."""
        from app.schemas import JobAssignmentCreate
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            JobAssignmentCreate(quote_id=1)

        with pytest.raises(ValidationError):
            JobAssignmentCreate(worker_id=1)

    def test_assignment_create_accepts_valid_data(self):
        """JobAssignmentCreate accepts valid data."""
        from app.schemas import JobAssignmentCreate

        assignment = JobAssignmentCreate(
            quote_id=1,
            worker_id=2,
            role="finisher",
            notes="Lead finisher",
        )

        assert assignment.quote_id == 1
        assert assignment.worker_id == 2
        assert assignment.role == "finisher"
        assert assignment.notes == "Lead finisher"

    def test_assignment_create_optional_fields(self):
        """JobAssignmentCreate role and notes are optional."""
        from app.schemas import JobAssignmentCreate

        assignment = JobAssignmentCreate(quote_id=1, worker_id=2)

        assert assignment.role is None
        assert assignment.notes is None


# =============================================================================
# SERVICE TESTS (No DB Required)
# =============================================================================


class TestWorkerServiceValidation:
    """Test worker service validation logic."""

    @pytest.mark.anyio
    async def test_assign_worker_validates_quote_exists(self):
        """assign_worker_to_job raises error if quote not found."""
        from app.workers.service import assign_worker_to_job
        from app.schemas import JobAssignmentCreate

        mock_db = AsyncMock()
        mock_db.get.return_value = None  # Quote not found

        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"

        data = JobAssignmentCreate(quote_id=999, worker_id=1)

        with pytest.raises(ValueError) as exc_info:
            await assign_worker_to_job(mock_db, data, mock_request)

        assert "Quote 999 not found" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_assign_worker_validates_worker_exists(self):
        """assign_worker_to_job raises error if worker not found."""
        from app.workers.service import assign_worker_to_job
        from app.schemas import JobAssignmentCreate

        mock_quote = MagicMock()
        mock_quote.quote_number = "Q-2024-00001"

        mock_db = AsyncMock()
        # First call returns quote, second returns None for worker
        mock_db.get.side_effect = [mock_quote, None]

        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"

        data = JobAssignmentCreate(quote_id=1, worker_id=999)

        with pytest.raises(ValueError) as exc_info:
            await assign_worker_to_job(mock_db, data, mock_request)

        assert "Worker 999 not found" in str(exc_info.value)


# =============================================================================
# API ENDPOINT TESTS (Require DB)
# =============================================================================


@requires_db
@pytest.mark.anyio
async def test_workers_list_requires_auth():
    """Workers list requires authentication."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        response = await ac.get("/workers")

        # Should redirect to login
        assert response.status_code in (302, 303, 307, 401)


@requires_db
@pytest.mark.anyio
async def test_workers_api_list_requires_auth():
    """Workers API list requires authentication."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        response = await ac.get("/workers/api/list")

        # Should redirect to login or return 401
        assert response.status_code in (302, 303, 307, 401)


@requires_db
@pytest.mark.anyio
async def test_worker_create_requires_auth():
    """Worker creation requires authentication."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/workers/api/create",
            json={"name": "Test Worker"}
        )

        assert response.status_code in (302, 303, 307, 401)


@requires_db
@pytest.mark.anyio
async def test_assign_worker_requires_auth():
    """Worker assignment requires authentication."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/workers/api/assign",
            json={"quote_id": 1, "worker_id": 1}
        )

        assert response.status_code in (302, 303, 307, 401)


@requires_db
@pytest.mark.anyio
async def test_authenticated_workers_list(authenticated_client):
    """Authenticated user can access workers list."""
    response = await authenticated_client.get("/workers")

    assert response.status_code == 200
    assert "Workers" in response.text


@requires_db
@pytest.mark.anyio
async def test_authenticated_workers_api(authenticated_client):
    """Authenticated user can access workers API."""
    response = await authenticated_client.get("/workers/api/list")

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


# =============================================================================
# CALENDAR INTEGRATION TESTS
# =============================================================================


class TestCalendarIntegration:
    """Test Google Calendar integration."""

    def test_calendar_not_configured_returns_none(self):
        """create_job_event returns None when not configured."""
        # This test verifies graceful handling when calendar isn't set up

        from app.integrations.google_calendar import _get_credentials

        # When credentials are empty, should return None
        with patch('app.integrations.google_calendar.settings') as mock_settings:
            mock_settings.google_credentials_json = ""
            mock_settings.google_calendar_id = ""

            result = _get_credentials()
            assert result is None

    @pytest.mark.anyio
    async def test_create_job_event_requires_confirmed_date(self):
        """create_job_event returns None without confirmed date."""
        from app.integrations.google_calendar import create_job_event

        mock_quote = MagicMock()
        mock_quote.confirmed_start_date = None
        mock_quote.quote_number = "Q-2024-00001"

        result = await create_job_event(mock_quote)

        assert result is None

    @pytest.mark.anyio
    async def test_create_job_event_builds_correct_summary(self):
        """Calendar event has correct summary format."""
        from app.integrations.google_calendar import _build_event_body

        mock_customer = MagicMock()
        mock_customer.name = "John Smith"
        mock_customer.phone = "0400000000"

        mock_quote = MagicMock()
        mock_quote.customer = mock_customer
        mock_quote.quote_number = "Q-2024-00001"
        mock_quote.job_address = "123 Main St, Albury NSW"
        mock_quote.total_cents = 550000
        mock_quote.notes = None
        mock_quote.confirmed_start_date = date(2024, 3, 15)

        event_body = _build_event_body(
            mock_quote,
            customer_name="John Smith",
            customer_phone="0400000000",
        )

        assert event_body["summary"] == "John Smith - 123 Main St"
        assert event_body["location"] == "123 Main St, Albury NSW"
        assert "Q-2024-00001" in event_body["description"]
        assert "John Smith" in event_body["description"]

    @pytest.mark.anyio
    async def test_create_job_event_includes_workers(self):
        """Calendar event includes assigned workers in description."""
        from app.integrations.google_calendar import _build_event_body

        mock_customer = MagicMock()
        mock_customer.name = "John Smith"
        mock_customer.phone = "0400000000"

        mock_quote = MagicMock()
        mock_quote.customer = mock_customer
        mock_quote.quote_number = "Q-2024-00001"
        mock_quote.job_address = "123 Main St, Albury NSW"
        mock_quote.total_cents = 550000
        mock_quote.notes = None
        mock_quote.confirmed_start_date = date(2024, 3, 15)

        worker_names = ["Dave Jones", "Mike Wilson"]

        event_body = _build_event_body(
            mock_quote, worker_names,
            customer_name="John Smith",
            customer_phone="0400000000",
        )

        assert "Assigned Workers" in event_body["description"]
        assert "Dave Jones" in event_body["description"]
        assert "Mike Wilson" in event_body["description"]

    @pytest.mark.anyio
    async def test_calendar_fails_gracefully(self):
        """Calendar errors don't block booking confirmation."""
        from app.integrations.google_calendar import create_job_event

        mock_quote = MagicMock()
        mock_quote.confirmed_start_date = date(2024, 3, 15)
        mock_quote.quote_number = "Q-2024-00001"
        mock_quote.customer.name = "John Smith"
        mock_quote.job_address = "123 Main St"
        mock_quote.total_cents = 100000
        mock_quote.notes = None

        # Even with error, should return None gracefully
        with patch('app.integrations.google_calendar._get_calendar_service') as mock_service:
            mock_service.return_value = None

            result = await create_job_event(mock_quote)

            assert result is None  # Gracefully returns None
