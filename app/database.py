"""Database configuration and session management."""

from contextlib import contextmanager, asynccontextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from app.config import settings


# Determine if using SQLite (for local dev)
is_sqlite = settings.database_url.startswith("sqlite")

# Only echo SQL in development + debug mode (never in production to avoid leaking data)
_sql_echo = settings.debug and settings.environment == "development"

# Create async engine
# Note: SQLite doesn't support pool_pre_ping or pool settings with async
engine_kwargs = {
    "echo": _sql_echo,
}
if not is_sqlite:
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_size"] = 10
    engine_kwargs["max_overflow"] = 20
    engine_kwargs["pool_recycle"] = 1800  # Recycle connections every 30 min (prevents stale connections behind cloud proxies)
    engine_kwargs["pool_timeout"] = 30

engine = create_async_engine(
    settings.database_url,
    **engine_kwargs,
)

# Enable SQLite foreign keys and WAL mode on every new connection
if is_sqlite:
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragma_on_connect(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.close()

# Session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


async def get_db() -> AsyncSession:
    """Dependency that provides a database session."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_async_session():
    """Async context manager for getting a database session outside of FastAPI dependencies."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Initialize database (create tables)."""
    # Import models to ensure they're registered with Base
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Safe migrations for existing databases (new columns on existing tables)
    await _run_safe_migrations()


async def _run_safe_migrations():
    """Add columns that may be missing from existing databases."""
    import logging
    from sqlalchemy import text, inspect as sa_inspect

    logger = logging.getLogger(__name__)

    migrations = [
        ("customers", "portal_access_token", "VARCHAR(64)"),
        # Part B-C: Expense model upgrades for Xero/BAS
        ("expenses", "gst_free", "BOOLEAN DEFAULT FALSE"),
        ("expenses", "xero_sync_error", "TEXT"),
        # PAYG withholding fields for workers
        ("workers", "claims_tax_free_threshold", "BOOLEAN DEFAULT TRUE"),
        ("workers", "pay_frequency", "VARCHAR(20) DEFAULT 'weekly'"),
        # Single-invoice model: payment schedule milestones on invoice
        ("invoices", "payment_schedule", "JSON"),
    ]

    async with engine.begin() as conn:
        for table, column, col_type in migrations:
            # Check if column exists
            def _has_column(sync_conn, tbl=table, col=column):
                inspector = sa_inspect(sync_conn)
                columns = [c["name"] for c in inspector.get_columns(tbl)]
                return col in columns

            exists = await conn.run_sync(_has_column)
            if not exists:
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                )
                logger.info(f"Migration: added {table}.{column}")


# =============================================================================
# SYNCHRONOUS SESSION (for Celery tasks)
# =============================================================================

def _get_sync_database_url() -> str:
    """Convert async database URL to sync format."""
    url = settings.database_url
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://")
    if url.startswith("sqlite+aiosqlite://"):
        return url.replace("sqlite+aiosqlite://", "sqlite://")
    return url


# Sync engine for Celery tasks
sync_engine = create_engine(
    _get_sync_database_url(),
    echo=_sql_echo,
    pool_pre_ping=True if not is_sqlite else False,
)

# Enable SQLite foreign keys for sync engine too
if is_sqlite:
    @event.listens_for(sync_engine, "connect")
    def _sqlite_pragma_on_connect_sync(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.close()

# Sync session factory
sync_session_maker = sessionmaker(
    sync_engine,
    class_=Session,
    expire_on_commit=False,
)


@contextmanager
def get_sync_session():
    """Get a synchronous database session for Celery tasks."""
    session = sync_session_maker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
