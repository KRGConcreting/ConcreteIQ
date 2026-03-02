"""Application configuration loaded from environment variables."""

import logging
from pydantic_settings import BaseSettings
from pydantic import model_validator
from functools import lru_cache


logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings."""
    
    # Environment
    environment: str = "development"
    debug: bool = False  # Must be explicitly enabled; never default True in prod
    
    # Database (defaults to SQLite for local dev, use PostgreSQL in production)
    database_url: str = "sqlite+aiosqlite:///./concreteiq.db"
    
    # Security
    secret_key: str = "change-me-in-production"
    # Dev default is bcrypt hash of "admin". Production MUST use a bcrypt hash
    # generated via: python -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_PASSWORD', bcrypt.gensalt()).decode())"
    admin_password: str = "$2b$12$47nrlise.haCQ8a2EuJiDOvgmq2rmYuJ43CDc7BVJLehUwzN.GZTi"
    session_expire_hours: int = 24  # 24 hours (was 168/7 days — reduced per security audit)

    # PII Encryption (Fernet key — generate via: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    encryption_key: str | None = None

    # Business Details (KRG)
    business_name: str = "Kyle R Gyoles"
    trading_as: str = "KRG Concreting"
    abn: str = "76 993 685 401"
    licence_number: str = "374931C"
    business_phone: str = "0423 005 129"
    business_email: str = "kyle@krgconcreting.au"
    business_address: str = "1/32 Whitton Drive, Thurgoona NSW 2640"
    
    # Integrations (optional)
    stripe_secret_key: str | None = None
    stripe_publishable_key: str | None = None
    stripe_webhook_secret: str | None = None
    postmark_api_key: str | None = None
    postmark_from_email: str = "quotes@krgconcreting.au"
    xero_client_id: str | None = None
    xero_client_secret: str | None = None
    xero_redirect_uri: str = ""  # e.g., https://app.krgconcreting.au/integrations/xero/callback
    xero_scopes: str = "offline_access openid profile email accounting.transactions accounting.contacts accounting.settings"
    xero_bank_account_code: str = "090"  # Xero bank account code for Spend Money transactions
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_places_api_key: str | None = None  # Google Maps Platform key (Places + Distance Matrix)

    # Google Calendar (service account for job scheduling)
    google_calendar_id: str = ""  # Calendar ID to create events in
    google_credentials_json: str = ""  # Service account JSON (base64 encoded)
    google_review_url: str = "https://g.page/r/Cd-0sbGF_o_nEAE/review"  # Google reviews link

    # Webhook authentication (shared secret for Postmark/ClickSend/Vonage callbacks)
    webhook_secret: str | None = None

    # Proxy trust (set True when behind a reverse proxy like Railway/nginx)
    trust_proxy_headers: bool = False

    # Redis (for Celery background tasks)
    redis_url: str = "redis://localhost:6379/0"

    # Storage (Cloudflare R2 / S3 compatible)
    r2_access_key: str | None = None
    r2_secret_key: str | None = None
    r2_bucket: str = "concreteiq-photos"
    r2_endpoint: str | None = None
    r2_public_url: str = ""  # CDN URL for serving photos
    
    # Bank details for invoices (user must set via env vars or settings UI)
    bank_name: str = "Great Southern Bank"
    bank_bsb: str = ""
    bank_account: str = ""
    bank_account_name: str = ""

    # App URL (override in production, e.g. https://app.krgconcreting.au)
    app_url: str = "http://localhost:8010"
    
    @model_validator(mode='after')
    def normalise_database_url(self):
        """Auto-convert Railway/Heroku DATABASE_URL to async driver format.

        Railway provides: postgresql://user:pass@host:port/db
        We need:          postgresql+asyncpg://user:pass@host:port/db
        """
        url = self.database_url
        if url.startswith("postgresql://"):
            self.database_url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgres://"):
            # Heroku-style URL
            self.database_url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        return self

    @model_validator(mode='after')
    def validate_production_settings(self):
        """Warn about insecure settings in production."""
        if self.environment == "production":
            # Check for default/weak secret key
            if self.secret_key in ("change-me-in-production", "local-dev-secret-key-change-in-production"):
                raise ValueError("SECURITY: Using default secret key in production! Set SECRET_KEY environment variable.")
            elif len(self.secret_key) < 32:
                logger.warning("SECURITY: Secret key is short. Consider using a longer key (32+ chars).")

            # Check for default admin password (dev default is bcrypt hash of "admin")
            if self.admin_password == "$2b$12$47nrlise.haCQ8a2EuJiDOvgmq2rmYuJ43CDc7BVJLehUwzN.GZTi":
                raise ValueError("SECURITY: Using default admin password in production! Set ADMIN_PASSWORD to a bcrypt hash.")

            # Check for SQLite in production
            if "sqlite" in self.database_url.lower():
                raise ValueError("DATABASE: Using SQLite in production. Set DATABASE_URL to a PostgreSQL connection string.")
            
            # Check for localhost app URL
            if "localhost" in self.app_url or "127.0.0.1" in self.app_url:
                logger.warning("CONFIG: APP_URL contains localhost. Update for production domain.")

            # Check encryption key is set
            if not self.encryption_key:
                logger.warning("SECURITY: ENCRYPTION_KEY not set. PII encryption disabled.")

            # Verify HTTPS in app_url
            if self.app_url and not self.app_url.startswith("https://"):
                logger.warning("CONFIG: APP_URL should use HTTPS in production.")

        return self
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
