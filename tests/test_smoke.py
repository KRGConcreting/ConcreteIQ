"""
Smoke tests — verify basic app functionality.

Run with: pytest tests/test_smoke.py -v
"""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


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


@pytest.mark.anyio
async def test_health_endpoint(client):
    """Health check returns 200."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.anyio
async def test_root_redirects_to_login(client):
    """Root redirects unauthenticated users to login."""
    response = await client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers.get("location", "")


@pytest.mark.anyio
async def test_login_page_renders(client):
    """Login page returns 200."""
    response = await client.get("/login")
    assert response.status_code == 200
    assert b"Login" in response.content or b"login" in response.content


@pytest.mark.anyio
async def test_calculator_import():
    """Calculator module imports without error."""
    from app.quotes.calculator import calculate, CalculatorInput
    
    # Basic calculation doesn't crash
    inputs = CalculatorInput(slab_area=25.0)
    result = calculate(inputs)
    
    assert result.volume_m3 > 0
    assert result.total_cents > 0


@pytest.mark.anyio
async def test_pour_planner_import():
    """Pour planner module imports without error."""
    from app.pour_planner.service import (
        calculate_evaporation_rate,
        get_evaporation_risk,
        calculate_setting_time,
    )
    
    # Basic calculation doesn't crash
    evap_rate = calculate_evaporation_rate(
        air_temp=25.0,
        concrete_temp=28.0,
        humidity=50,
        wind_speed=10.0,
    )
    
    assert evap_rate >= 0
    risk = get_evaporation_risk(evap_rate)
    assert risk["level"] in ["low", "moderate", "high", "very_high", "critical"]


@pytest.mark.anyio
async def test_api_config_unauthenticated(client):
    """API config returns authenticated=False when not logged in."""
    response = await client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert data["authenticated"] == False
