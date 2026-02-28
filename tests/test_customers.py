"""
Customer module tests.

Run with: pytest tests/test_customers.py -v

Note: Some tests require a PostgreSQL database connection.
Tests that require the database are marked and will be skipped
if the database is not available.
"""

import pytest
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
    """Async test client with authentication and CSRF token."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        # Login to get session cookie
        response = await ac.post("/login", data={
            "password": "admin",  # Default password from settings
            "next": "/",
        }, follow_redirects=False)

        # CSRF token is set during login via middleware — read from client cookie jar
        csrf_token = ac.cookies.get("csrf_token", "")
        if csrf_token:
            ac.headers["X-CSRF-Token"] = csrf_token

        yield ac


# =============================================================================
# AUTH TESTS
# =============================================================================

@pytest.mark.anyio
async def test_customers_list_requires_auth(client):
    """Customer list page redirects to login when not authenticated."""
    response = await client.get("/customers", follow_redirects=False)
    assert response.status_code in [302, 307]  # Either redirect type
    assert "/login" in response.headers.get("location", "")


@pytest.mark.anyio
async def test_customers_api_requires_auth(client):
    """Customer API redirects when not authenticated."""
    response = await client.get("/customers/api/list", follow_redirects=False)
    assert response.status_code in [302, 307]  # Either redirect type


@pytest.mark.anyio
async def test_customers_new_requires_auth(client):
    """New customer page redirects to login when not authenticated."""
    response = await client.get("/customers/new", follow_redirects=False)
    assert response.status_code in [302, 307]  # Either redirect type


# =============================================================================
# PAGE RENDER TESTS (require database)
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_customers_list_page_renders(authenticated_client):
    """Customer list page renders for authenticated user."""
    response = await authenticated_client.get("/customers")
    assert response.status_code == 200
    assert b"Customers" in response.content or b"customers" in response.content


@pytest.mark.anyio
async def test_customers_new_page_renders(authenticated_client):
    """New customer page renders for authenticated user."""
    response = await authenticated_client.get("/customers/new")
    assert response.status_code == 200
    assert b"New Customer" in response.content or b"customer" in response.content


@requires_db
@pytest.mark.anyio
async def test_customers_list_with_search(authenticated_client):
    """Customer list accepts search parameter."""
    response = await authenticated_client.get("/customers?q=test")
    assert response.status_code == 200


# =============================================================================
# API TESTS (require database)
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_customers_api_list_structure(authenticated_client):
    """Customer API list endpoint returns correct structure."""
    response = await authenticated_client.get("/customers/api/list")
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
async def test_customers_api_list_pagination(authenticated_client):
    """Customer API respects pagination parameters."""
    response = await authenticated_client.get("/customers/api/list?page=1&page_size=10")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 10


@pytest.mark.anyio
async def test_customers_api_create_requires_name(authenticated_client):
    """Customer create API requires name field."""
    response = await authenticated_client.post(
        "/customers/api/create",
        json={"phone": "0400000000"}  # Missing name
    )
    assert response.status_code == 422  # Validation error


@requires_db
@pytest.mark.anyio
async def test_customers_api_get_not_found(authenticated_client):
    """Customer get API returns 404 for non-existent customer."""
    response = await authenticated_client.get("/customers/api/99999")
    assert response.status_code == 404


# =============================================================================
# TEMPLATE CONTENT TESTS
# =============================================================================

@requires_db
@pytest.mark.anyio
async def test_customers_list_has_add_button(authenticated_client):
    """Customer list page has 'Add Customer' button."""
    response = await authenticated_client.get("/customers")
    assert response.status_code == 200
    assert b"Add Customer" in response.content or b"New Customer" in response.content


@pytest.mark.anyio
async def test_customers_new_has_form_fields(authenticated_client):
    """New customer page has required form fields."""
    response = await authenticated_client.get("/customers/new")
    assert response.status_code == 200
    content = response.content.decode()

    # Check for key form fields
    assert "name" in content.lower()
    assert "phone" in content.lower()
    assert "email" in content.lower()


@requires_db
@pytest.mark.anyio
async def test_customers_list_empty_state(authenticated_client):
    """Customer list shows empty state when no customers."""
    response = await authenticated_client.get("/customers")
    assert response.status_code == 200
    # Should have some indication of empty state or customer list
    content = response.content.decode()
    # Either has customers or shows empty state
    assert "customer" in content.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
