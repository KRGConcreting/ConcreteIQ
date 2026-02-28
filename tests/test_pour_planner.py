"""
Pour planner job integration tests.

Run with: pytest tests/test_pour_planner.py -v

Tests pour plan creation, weather integration, and calculations.
IMPORTANT: Does NOT modify the SACRED service.py - only tests that we call it correctly.
"""

import pytest
from datetime import date, time, datetime
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
# SACRED SERVICE TESTS (Verify we call it correctly - DO NOT MODIFY service.py)
# =============================================================================


class TestSacredServiceCalls:
    """Test that job_service correctly calls the SACRED service.py functions."""

    def test_calculate_pour_conditions_is_called(self):
        """Verify calculate_pour_conditions is importable and callable."""
        from app.pour_planner.service import calculate_pour_conditions

        # Test with sample data
        result = calculate_pour_conditions({
            "date": "2024-03-15",
            "time": "07:00",
            "air_temp": 25,
            "humidity": 50,
            "wind_speed": 10,
            "sun_exposure": "half",
            "concrete_grade": "N25",
            "is_exposed": False,
            "travel_time_min": 30,
        })

        # Verify we get expected structure
        assert "evaporation" in result
        assert "setting_time" in result
        assert "slump" in result
        assert "warnings" in result
        assert "tips" in result

    def test_evaporation_rate_calculation(self):
        """Verify evaporation rate calculation from SACRED service."""
        from app.pour_planner.service import calculate_evaporation_rate, get_evaporation_risk

        # Test known conditions
        evap_rate = calculate_evaporation_rate(
            air_temp=30,
            concrete_temp=38,  # Concrete temp is higher
            humidity=40,
            wind_speed=15,
        )

        # Rate should be positive
        assert evap_rate > 0

        # Check risk classification
        risk = get_evaporation_risk(evap_rate)
        assert "level" in risk
        assert "message" in risk
        assert "actions" in risk

    def test_setting_time_calculation(self):
        """Verify setting time calculation from SACRED service."""
        from app.pour_planner.service import calculate_setting_time

        result = calculate_setting_time(
            effective_temp=25,
            concrete_grade="N25",
        )

        assert "initial_set_hours" in result
        assert "final_set_hours" in result
        assert "time_to_trowel_hours" in result

        # Initial set should be positive
        assert result["initial_set_hours"] > 0
        # Final set should be longer than initial
        assert result["final_set_hours"] > result["initial_set_hours"]

    def test_slump_recommendation(self):
        """Verify slump recommendation from SACRED service."""
        from app.pour_planner.service import recommend_order_slump

        result = recommend_order_slump(
            target_arrival_slump=100,
            temp_celsius=30,
            travel_time_min=30,
            concrete_grade="N25",
        )

        assert "order_slump" in result
        assert "target_arrival_slump" in result
        assert result["order_slump"] >= result["target_arrival_slump"]


# =============================================================================
# JOB SERVICE TESTS (Mocked)
# =============================================================================


class TestPourPlanCreation:
    """Test pour plan creation logic."""

    @pytest.mark.anyio
    async def test_create_pour_plan_validates_quote_exists(self):
        """create_pour_plan raises error if quote not found."""
        from app.pour_planner.job_service import create_pour_plan

        mock_db = AsyncMock()
        mock_db.get.return_value = None  # Quote not found

        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"

        with pytest.raises(ValueError) as exc_info:
            await create_pour_plan(
                mock_db,
                quote_id=999,
                planned_date=date(2024, 3, 15),
                planned_time="07:00",
                request=mock_request,
            )

        assert "Quote not found" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_create_pour_plan_rejects_duplicate(self):
        """create_pour_plan raises error if plan already exists."""
        from app.pour_planner.job_service import create_pour_plan

        mock_quote = MagicMock()
        mock_existing_plan = MagicMock()

        mock_db = AsyncMock()
        mock_db.get.return_value = mock_quote

        # Mock get_pour_plan to return existing plan
        with patch('app.pour_planner.job_service.get_pour_plan') as mock_get_plan:
            mock_get_plan.return_value = mock_existing_plan

            mock_request = MagicMock()
            mock_request.client.host = "127.0.0.1"

            with pytest.raises(ValueError) as exc_info:
                await create_pour_plan(
                    mock_db,
                    quote_id=1,
                    planned_date=date(2024, 3, 15),
                    planned_time="07:00",
                    request=mock_request,
                )

            assert "already exists" in str(exc_info.value)


class TestPourConditions:
    """Test pour conditions retrieval."""

    @pytest.mark.anyio
    async def test_get_conditions_requires_date(self):
        """get_pour_conditions returns message when no date set."""
        from app.pour_planner.job_service import get_pour_conditions

        mock_quote = MagicMock()
        mock_quote.id = 1
        mock_quote.confirmed_start_date = None
        mock_quote.calculator_input = None

        mock_db = AsyncMock()

        # Mock get_pour_plan to return None
        with patch('app.pour_planner.job_service.get_pour_plan') as mock_get_plan:
            mock_get_plan.return_value = None

            result = await get_pour_conditions(mock_db, mock_quote)

            assert result["quote_id"] == 1
            assert result["planned_date"] is None
            assert "No pour date" in result.get("message", "")

    @pytest.mark.anyio
    async def test_get_conditions_uses_confirmed_date(self):
        """get_pour_conditions uses confirmed_start_date when available."""
        from app.pour_planner.job_service import get_pour_conditions

        mock_quote = MagicMock()
        mock_quote.id = 1
        mock_quote.confirmed_start_date = date(2024, 3, 15)
        mock_quote.calculator_input = {"concrete_grade": "N25"}

        mock_db = AsyncMock()

        # Mock get_pour_plan and weather
        with patch('app.pour_planner.job_service.get_pour_plan') as mock_get_plan, \
             patch('app.pour_planner.job_service.get_weather_forecast') as mock_weather:
            mock_get_plan.return_value = None
            mock_weather.return_value = []  # No weather data

            result = await get_pour_conditions(mock_db, mock_quote)

            assert result["quote_id"] == 1
            assert result["planned_date"] == "2024-03-15"

    @pytest.mark.anyio
    async def test_get_conditions_uses_pour_plan_date(self):
        """get_pour_conditions prefers pour plan date over confirmed date."""
        from app.pour_planner.job_service import get_pour_conditions

        mock_quote = MagicMock()
        mock_quote.id = 1
        mock_quote.confirmed_start_date = date(2024, 3, 15)
        mock_quote.calculator_input = {"concrete_grade": "N32"}

        mock_pour_plan = MagicMock()
        mock_pour_plan.planned_date = date(2024, 3, 20)  # Different date
        mock_pour_plan.planned_start_time = time(8, 0)

        mock_db = AsyncMock()

        with patch('app.pour_planner.job_service.get_pour_plan') as mock_get_plan, \
             patch('app.pour_planner.job_service.get_weather_forecast') as mock_weather:
            mock_get_plan.return_value = mock_pour_plan
            mock_weather.return_value = []

            result = await get_pour_conditions(mock_db, mock_quote)

            # Should use pour plan date
            assert result["planned_date"] == "2024-03-20"


# =============================================================================
# API ENDPOINT TESTS (Require DB)
# =============================================================================


@requires_db
@pytest.mark.anyio
async def test_pour_planner_page_requires_auth():
    """Pour planner page requires authentication."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        response = await ac.get("/pour-planner")

        assert response.status_code in (302, 303, 307, 401)


@requires_db
@pytest.mark.anyio
async def test_pour_planner_calculate_requires_auth():
    """Pour planner calculate requires authentication."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/pour-planner/api/calculate",
            json={"air_temp": 25, "humidity": 50}
        )

        assert response.status_code in (302, 303, 307, 401)


@requires_db
@pytest.mark.anyio
async def test_authenticated_pour_planner_page(authenticated_client):
    """Authenticated user can access pour planner page."""
    response = await authenticated_client.get("/pour-planner")

    assert response.status_code == 200
    assert "Pour Planner" in response.text or "pour" in response.text.lower()


@requires_db
@pytest.mark.anyio
async def test_authenticated_calculate(authenticated_client):
    """Authenticated user can calculate pour conditions."""
    response = await authenticated_client.post(
        "/pour-planner/api/calculate",
        json={
            "date": "2024-03-15",
            "time": "07:00",
            "air_temp": 25,
            "humidity": 50,
            "wind_speed": 10,
            "sun_exposure": "half",
            "concrete_grade": "N25",
        }
    )

    assert response.status_code == 200
    data = response.json()

    # Verify structure from SACRED service
    assert "evaporation" in data
    assert "setting_time" in data


@requires_db
@pytest.mark.anyio
async def test_evaporation_endpoint(authenticated_client):
    """Evaporation endpoint returns quick calculation."""
    response = await authenticated_client.post(
        "/pour-planner/api/evaporation",
        json={
            "air_temp": 30,
            "humidity": 40,
            "wind_speed": 15,
        }
    )

    assert response.status_code == 200
    data = response.json()

    assert "evap_rate" in data
    assert "risk" in data
    assert data["evap_rate"] > 0


@requires_db
@pytest.mark.anyio
async def test_job_conditions_requires_auth():
    """Job conditions endpoint requires auth."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        response = await ac.get("/pour-planner/api/job/1/conditions")

        assert response.status_code in (302, 303, 307, 401)


@requires_db
@pytest.mark.anyio
async def test_job_plan_create_requires_auth():
    """Creating pour plan requires auth."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/pour-planner/api/job/1/plan",
            json={"planned_date": "2024-03-15"}
        )

        assert response.status_code in (302, 303, 307, 401)


# =============================================================================
# WEATHER SNAPSHOT TESTS
# =============================================================================


class TestWeatherSnapshot:
    """Test weather snapshot capture."""

    @pytest.mark.anyio
    async def test_weather_snapshot_structure(self):
        """Weather snapshot includes expected fields."""
        from app.pour_planner.service import WeatherData

        # Create sample weather data
        weather = WeatherData(
            datetime=datetime(2024, 3, 15, 7, 0),
            temperature=25.0,
            humidity=50.0,
            wind_speed=10.0,
            rain_probability=20.0,
            rain_mm=0.0,
            uv_index=5.0,
            cloud_cover=30.0,
            conditions="Partly cloudy",
        )

        # Verify structure
        assert weather.temperature == 25.0
        assert weather.humidity == 50.0
        assert weather.wind_speed == 10.0
        assert weather.conditions == "Partly cloudy"

    def test_pour_plan_captures_weather(self):
        """PourPlan model has weather_snapshot field."""
        from app.models import PourPlan

        # Verify the model has the expected fields
        assert hasattr(PourPlan, 'weather_snapshot')
        assert hasattr(PourPlan, 'evaporation_rate')
        assert hasattr(PourPlan, 'risk_level')
        assert hasattr(PourPlan, 'recommendations')
