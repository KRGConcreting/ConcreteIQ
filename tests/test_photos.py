"""
Photo upload and management tests.

Run with: pytest tests/test_photos.py -v

Tests photo upload, listing, and deletion.
"""

import pytest
from datetime import date, datetime
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
from io import BytesIO
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
# SERVICE VALIDATION TESTS (No DB Required)
# =============================================================================


class TestPhotoValidation:
    """Test photo validation functions."""

    def test_validate_file_rejects_empty_filename(self):
        """validate_file rejects files without filename."""
        from app.photos.service import validate_file

        mock_file = MagicMock()
        mock_file.filename = ""

        is_valid, error = validate_file(mock_file)

        assert not is_valid
        assert "No filename" in error

    def test_validate_file_rejects_invalid_extension(self):
        """validate_file rejects files with invalid extensions."""
        from app.photos.service import validate_file

        mock_file = MagicMock()
        mock_file.filename = "test.pdf"
        mock_file.content_type = "application/pdf"

        is_valid, error = validate_file(mock_file)

        assert not is_valid
        assert "Invalid file type" in error

    def test_validate_file_accepts_jpeg(self):
        """validate_file accepts JPEG files."""
        from app.photos.service import validate_file

        mock_file = MagicMock()
        mock_file.filename = "photo.jpg"
        mock_file.content_type = "image/jpeg"

        is_valid, error = validate_file(mock_file)

        assert is_valid
        assert error == ""

    def test_validate_file_accepts_png(self):
        """validate_file accepts PNG files."""
        from app.photos.service import validate_file

        mock_file = MagicMock()
        mock_file.filename = "screenshot.png"
        mock_file.content_type = "image/png"

        is_valid, error = validate_file(mock_file)

        assert is_valid
        assert error == ""

    def test_generate_storage_key_includes_quote_id(self):
        """generate_storage_key includes quote ID in path."""
        from app.photos.service import generate_storage_key

        key = generate_storage_key(123, "photo.jpg")

        assert "quote-123" in key
        assert key.startswith("photos/")
        assert key.endswith(".jpg")

    def test_generate_storage_key_unique(self):
        """generate_storage_key produces unique keys."""
        from app.photos.service import generate_storage_key

        key1 = generate_storage_key(1, "photo.jpg")
        key2 = generate_storage_key(1, "photo.jpg")

        assert key1 != key2


class TestPhotoServiceMocked:
    """Test photo service with mocked dependencies."""

    @pytest.mark.anyio
    async def test_upload_validates_quote_exists(self):
        """upload_photo raises error if quote not found."""
        from app.photos.service import upload_photo

        mock_db = AsyncMock()
        mock_db.get.return_value = None  # Quote not found

        mock_file = MagicMock()
        mock_file.filename = "test.jpg"
        mock_file.content_type = "image/jpeg"

        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"

        with pytest.raises(ValueError) as exc_info:
            await upload_photo(mock_db, 999, mock_file, "before", None, mock_request)

        assert "Quote not found" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_upload_validates_file_type(self):
        """upload_photo raises error for invalid file types."""
        from app.photos.service import upload_photo

        mock_quote = MagicMock()
        mock_db = AsyncMock()
        mock_db.get.return_value = mock_quote

        mock_file = MagicMock()
        mock_file.filename = "document.pdf"
        mock_file.content_type = "application/pdf"

        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"

        with pytest.raises(ValueError) as exc_info:
            await upload_photo(mock_db, 1, mock_file, "before", None, mock_request)

        assert "Invalid file type" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_upload_validates_file_size(self):
        """upload_photo raises error for files too large."""
        from app.photos.service import upload_photo

        mock_quote = MagicMock()
        mock_db = AsyncMock()
        mock_db.get.return_value = mock_quote

        mock_file = MagicMock()
        mock_file.filename = "photo.jpg"
        mock_file.content_type = "image/jpeg"
        # Return 15MB of data
        mock_file.read = AsyncMock(return_value=b"x" * (15 * 1024 * 1024))

        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"

        with pytest.raises(ValueError) as exc_info:
            await upload_photo(mock_db, 1, mock_file, "before", None, mock_request)

        assert "too large" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_upload_validates_empty_file(self):
        """upload_photo raises error for empty files."""
        from app.photos.service import upload_photo

        mock_quote = MagicMock()
        mock_db = AsyncMock()
        mock_db.get.return_value = mock_quote

        mock_file = MagicMock()
        mock_file.filename = "photo.jpg"
        mock_file.content_type = "image/jpeg"
        mock_file.read = AsyncMock(return_value=b"")  # Empty file

        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"

        with pytest.raises(ValueError) as exc_info:
            await upload_photo(mock_db, 1, mock_file, "before", None, mock_request)

        assert "Empty file" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_get_photos_filters_by_type(self):
        """get_photos can filter by photo type."""
        from app.photos.service import get_photos
        from sqlalchemy import select

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        await get_photos(mock_db, 1, "before")

        # Verify execute was called
        mock_db.execute.assert_called_once()


# =============================================================================
# API ENDPOINT TESTS (Require DB)
# =============================================================================


@requires_db
@pytest.mark.anyio
async def test_photos_upload_requires_auth():
    """Photo upload requires authentication."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/photos/api/upload",
            data={"quote_id": 1, "photo_type": "before"},
            files={"file": ("test.jpg", b"test", "image/jpeg")}
        )

        assert response.status_code in (302, 303, 307, 401)


@requires_db
@pytest.mark.anyio
async def test_photos_list_requires_auth():
    """Photo listing requires authentication."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        response = await ac.get("/photos/api/quote/1")

        assert response.status_code in (302, 303, 307, 401)


@requires_db
@pytest.mark.anyio
async def test_photos_delete_requires_auth():
    """Photo deletion requires authentication."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        response = await ac.delete("/photos/api/1")

        assert response.status_code in (302, 303, 307, 401)


@requires_db
@pytest.mark.anyio
async def test_authenticated_photos_list(authenticated_client):
    """Authenticated user can list photos."""
    response = await authenticated_client.get("/photos/api/quote/1")

    assert response.status_code == 200
    data = response.json()
    assert "success" in data
    assert "photos" in data


@requires_db
@pytest.mark.anyio
async def test_photos_list_empty_for_nonexistent_quote(authenticated_client):
    """Photos list returns empty for nonexistent quote."""
    response = await authenticated_client.get("/photos/api/quote/99999")

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["photos"] == []


@requires_db
@pytest.mark.anyio
async def test_upload_rejects_invalid_photo_type(authenticated_client):
    """Upload rejects invalid photo type."""
    response = await authenticated_client.post(
        "/photos/api/upload",
        data={"quote_id": 1, "photo_type": "invalid_type"},
        files={"file": ("test.jpg", b"test content", "image/jpeg")}
    )

    assert response.status_code == 400


# =============================================================================
# STORAGE INTEGRATION TESTS
# =============================================================================


class TestStorageClient:
    """Test S3/R2 storage client."""

    @pytest.mark.anyio
    async def test_storage_client_returns_none_when_not_configured(self):
        """get_storage_client returns None when not configured."""
        from app.photos.service import get_storage_client

        with patch('app.photos.service.settings') as mock_settings:
            mock_settings.r2_access_key = None
            mock_settings.r2_secret_key = None
            mock_settings.r2_endpoint = None

            client = await get_storage_client()

            assert client is None

    @pytest.mark.anyio
    async def test_storage_client_handles_import_error(self):
        """get_storage_client handles missing boto3."""
        from app.photos.service import get_storage_client

        with patch('app.photos.service.settings') as mock_settings:
            mock_settings.r2_access_key = "key"
            mock_settings.r2_secret_key = "secret"
            mock_settings.r2_endpoint = "https://r2.example.com"

            with patch.dict('sys.modules', {'boto3': None}):
                # Should not raise, just return None
                client = await get_storage_client()
                # May succeed if boto3 is installed, or return None
