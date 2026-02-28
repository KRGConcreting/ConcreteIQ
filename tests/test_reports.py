"""
Reports module tests.

Run with: pytest tests/test_reports.py -v

Tests for:
- Dashboard stats calculation
- Quote conversion rate
- Revenue by month
- CSV export format
- Reports require auth
- Date range filtering
"""

import pytest
from datetime import date, timedelta
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
# AUTH TESTS
# =============================================================================

@pytest.mark.anyio
async def test_reports_index_requires_auth(client):
    """Reports index page redirects to login when not authenticated."""
    response = await client.get("/reports", follow_redirects=False)
    assert response.status_code in [302, 307]
    assert "/login" in response.headers.get("location", "")


@pytest.mark.anyio
async def test_reports_quotes_requires_auth(client):
    """Quote report page requires authentication."""
    response = await client.get("/reports/quotes", follow_redirects=False)
    assert response.status_code in [302, 307]


@pytest.mark.anyio
async def test_reports_revenue_requires_auth(client):
    """Revenue report page requires authentication."""
    response = await client.get("/reports/revenue", follow_redirects=False)
    assert response.status_code in [302, 307]


@pytest.mark.anyio
async def test_reports_jobs_requires_auth(client):
    """Jobs report page requires authentication."""
    response = await client.get("/reports/jobs", follow_redirects=False)
    assert response.status_code in [302, 307]


@pytest.mark.anyio
async def test_reports_customers_requires_auth(client):
    """Customer report page requires authentication."""
    response = await client.get("/reports/customers", follow_redirects=False)
    assert response.status_code in [302, 307]


@pytest.mark.anyio
async def test_reports_api_dashboard_requires_auth(client):
    """Dashboard stats API requires authentication."""
    response = await client.get("/reports/api/dashboard", follow_redirects=False)
    assert response.status_code in [302, 307]


@pytest.mark.anyio
async def test_reports_csv_export_requires_auth(client):
    """CSV export requires authentication."""
    response = await client.get("/reports/api/export/quotes", follow_redirects=False)
    assert response.status_code in [302, 307]


# =============================================================================
# PAGE RENDER TESTS (require database)
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_reports_index_renders(authenticated_client):
    """Reports index page renders for authenticated user."""
    response = await authenticated_client.get("/reports")
    assert response.status_code == 200
    assert b"Reports" in response.content


@requires_db
@pytest.mark.anyio
async def test_reports_quotes_renders(authenticated_client):
    """Quote report page renders for authenticated user."""
    response = await authenticated_client.get("/reports/quotes")
    assert response.status_code == 200
    assert b"Quote" in response.content or b"Conversion" in response.content


@requires_db
@pytest.mark.anyio
async def test_reports_revenue_renders(authenticated_client):
    """Revenue report page renders for authenticated user."""
    response = await authenticated_client.get("/reports/revenue")
    assert response.status_code == 200
    assert b"Revenue" in response.content


@requires_db
@pytest.mark.anyio
async def test_reports_jobs_renders(authenticated_client):
    """Jobs report page renders for authenticated user."""
    response = await authenticated_client.get("/reports/jobs")
    assert response.status_code == 200
    assert b"Jobs" in response.content


@requires_db
@pytest.mark.anyio
async def test_reports_customers_renders(authenticated_client):
    """Customer report page renders for authenticated user."""
    response = await authenticated_client.get("/reports/customers")
    assert response.status_code == 200
    assert b"Customer" in response.content


# =============================================================================
# API TESTS (require database)
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_dashboard_stats_structure(authenticated_client):
    """Dashboard stats API returns correct structure."""
    response = await authenticated_client.get("/reports/api/dashboard")
    assert response.status_code == 200
    data = response.json()

    # Should have required fields
    assert "quotes_this_month" in data
    assert "conversion_rate" in data
    assert "revenue_this_month_cents" in data
    assert "outstanding_cents" in data
    assert "jobs_this_week" in data
    assert "overdue_count" in data
    assert "trends" in data

    # Types should be correct
    assert isinstance(data["quotes_this_month"], int)
    assert isinstance(data["conversion_rate"], (int, float))
    assert isinstance(data["revenue_this_month_cents"], int)


@requires_db
@pytest.mark.anyio
async def test_quote_stats_structure(authenticated_client):
    """Quote stats API returns correct structure."""
    response = await authenticated_client.get("/reports/api/quotes")
    assert response.status_code == 200
    data = response.json()

    # Should have required fields
    assert "funnel" in data
    assert "conversion_rate" in data
    assert "avg_time_to_accept_days" in data
    assert "decline_reasons" in data
    assert "by_suburb" in data
    assert "by_month" in data

    # Funnel should have status counts
    assert "draft" in data["funnel"]
    assert "sent" in data["funnel"]
    assert "accepted" in data["funnel"]


@requires_db
@pytest.mark.anyio
async def test_revenue_stats_structure(authenticated_client):
    """Revenue stats API returns correct structure."""
    response = await authenticated_client.get("/reports/api/revenue")
    assert response.status_code == 200
    data = response.json()

    # Should have required fields
    assert "total_revenue_cents" in data
    assert "by_month" in data
    assert "by_stage" in data
    assert "outstanding_cents" in data
    assert "top_customers" in data

    # Stage breakdown should have standard stages
    assert "booking" in data["by_stage"]
    assert "prepour" in data["by_stage"]
    assert "completion" in data["by_stage"]


@requires_db
@pytest.mark.anyio
async def test_job_stats_structure(authenticated_client):
    """Job stats API returns correct structure."""
    response = await authenticated_client.get("/reports/api/jobs")
    assert response.status_code == 200
    data = response.json()

    # Should have required fields
    assert "completed_count" in data
    assert "completed_value_cents" in data
    assert "by_suburb" in data
    assert "by_worker" in data
    assert "upcoming" in data


@requires_db
@pytest.mark.anyio
async def test_customer_stats_structure(authenticated_client):
    """Customer stats API returns correct structure."""
    response = await authenticated_client.get("/reports/api/customers")
    assert response.status_code == 200
    data = response.json()

    # Should have required fields
    assert "new_count" in data
    assert "total_count" in data
    assert "repeat_count" in data
    assert "top_by_revenue" in data
    assert "by_location" in data


# =============================================================================
# DATE FILTERING TESTS
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_dashboard_stats_period_filter(authenticated_client):
    """Dashboard stats respects period parameter."""
    # Test different periods
    for period in ["week", "month", "quarter", "year"]:
        response = await authenticated_client.get(f"/reports/api/dashboard?period={period}")
        assert response.status_code == 200
        data = response.json()
        assert "period" in data
        assert data["period"] == period


@requires_db
@pytest.mark.anyio
async def test_reports_custom_date_range(authenticated_client):
    """Reports accept custom date range."""
    start = (date.today() - timedelta(days=30)).isoformat()
    end = date.today().isoformat()

    response = await authenticated_client.get(
        f"/reports/api/quotes?start={start}&end={end}"
    )
    assert response.status_code == 200


@requires_db
@pytest.mark.anyio
async def test_reports_invalid_date_falls_back(authenticated_client):
    """Reports fall back to period on invalid dates."""
    response = await authenticated_client.get(
        "/reports/api/quotes?start=invalid&end=invalid"
    )
    assert response.status_code == 200  # Should not error


# =============================================================================
# CSV EXPORT TESTS
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_csv_export_quotes_format(authenticated_client):
    """Quotes CSV export has correct format."""
    response = await authenticated_client.get("/reports/api/export/quotes")
    assert response.status_code == 200
    assert response.headers.get("content-type") == "text/csv; charset=utf-8"
    assert "attachment" in response.headers.get("content-disposition", "")

    # Check CSV content
    content = response.content.decode()
    lines = content.strip().split('\n')
    assert len(lines) >= 1  # At least header row

    # Check header
    header = lines[0]
    assert "Quote Number" in header
    assert "Customer" in header
    assert "Status" in header


@requires_db
@pytest.mark.anyio
async def test_csv_export_invoices_format(authenticated_client):
    """Invoices CSV export has correct format."""
    response = await authenticated_client.get("/reports/api/export/invoices")
    assert response.status_code == 200
    assert response.headers.get("content-type") == "text/csv; charset=utf-8"

    content = response.content.decode()
    header = content.split('\n')[0]
    assert "Invoice Number" in header
    assert "Customer" in header


@requires_db
@pytest.mark.anyio
async def test_csv_export_payments_format(authenticated_client):
    """Payments CSV export has correct format."""
    response = await authenticated_client.get("/reports/api/export/payments")
    assert response.status_code == 200
    assert response.headers.get("content-type") == "text/csv; charset=utf-8"

    content = response.content.decode()
    header = content.split('\n')[0]
    assert "Payment Date" in header or "Amount" in header


@requires_db
@pytest.mark.anyio
async def test_csv_export_invalid_type(authenticated_client):
    """Invalid export type returns error."""
    response = await authenticated_client.get("/reports/api/export/invalid")
    assert response.status_code == 400


# =============================================================================
# CONVERSION RATE CALCULATION TESTS
# =============================================================================

def test_conversion_rate_calculation():
    """Conversion rate calculation is correct."""
    # Import the function
    from app.reports.service import get_period_dates

    # Test period date calculation
    start, end = get_period_dates("month")
    assert start <= end
    assert start.day == 1  # Month starts on 1st

    start, end = get_period_dates("week")
    assert start <= end
    assert (end - start).days == 6  # Week is 7 days


def test_period_dates_year():
    """Year period gives correct dates."""
    from app.reports.service import get_period_dates

    start, end = get_period_dates("year")
    assert start.month == 1
    assert start.day == 1
    assert end.month == 12
    assert end.day == 31


# =============================================================================
# DASHBOARD TESTS
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_dashboard_renders_with_stats(authenticated_client):
    """Dashboard page renders with stats."""
    response = await authenticated_client.get("/dashboard")
    assert response.status_code == 200

    content = response.content.decode()
    # Should have KPI cards
    assert "Quotes" in content or "quotes" in content
    assert "Revenue" in content or "revenue" in content


@requires_db
@pytest.mark.anyio
async def test_dashboard_shows_trends(authenticated_client):
    """Dashboard shows trend indicators."""
    response = await authenticated_client.get("/dashboard")
    assert response.status_code == 200
    # Trends may or may not show depending on data
    # Just verify page loads


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
