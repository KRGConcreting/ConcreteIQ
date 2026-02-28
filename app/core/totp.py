"""
TOTP (Time-based One-Time Password) two-factor authentication.

Stores TOTP secret in the settings table (category='security').
Uses pyotp for TOTP generation/verification and qrcode for QR setup.
Includes recovery code support for account recovery.
"""

import io
import json
import base64
import hashlib
import secrets
from typing import Optional

import pyotp
import qrcode

from sqlalchemy.ext.asyncio import AsyncSession
from app.settings import service as settings_service


TOTP_CATEGORY = "security"
TOTP_SECRET_KEY = "totp_secret"
TOTP_ENABLED_KEY = "totp_enabled"
TOTP_ISSUER = "ConcreteIQ"


async def is_totp_enabled(db: AsyncSession) -> bool:
    """Check if TOTP 2FA is enabled."""
    security = await settings_service.get_settings_by_category(db, TOTP_CATEGORY)
    return security.get(TOTP_ENABLED_KEY) == "true"


async def get_totp_secret(db: AsyncSession) -> Optional[str]:
    """Get the stored TOTP secret."""
    security = await settings_service.get_settings_by_category(db, TOTP_CATEGORY)
    return security.get(TOTP_SECRET_KEY)


def generate_totp_secret() -> str:
    """Generate a new TOTP secret key."""
    return pyotp.random_base32()


def get_totp_uri(secret: str, account_name: str = "admin") -> str:
    """Get the otpauth:// URI for QR code generation."""
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=account_name, issuer_name=TOTP_ISSUER)


def generate_qr_code_base64(uri: str) -> str:
    """Generate QR code as base64-encoded PNG for embedding in HTML."""
    qr = qrcode.QRCode(version=1, box_size=6, border=2)
    qr.add_data(uri)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return base64.b64encode(buffer.getvalue()).decode()


def verify_totp_code(secret: str, code: str) -> bool:
    """
    Verify a 6-digit TOTP code.

    Allows 1 window of clock drift (30 seconds each way).
    """
    if not secret or not code:
        return False

    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


async def setup_totp(db: AsyncSession) -> tuple[str, str, str]:
    """
    Start TOTP setup — generate secret and QR code.

    Returns:
        tuple of (secret, qr_code_base64, otpauth_uri)
    """
    secret = generate_totp_secret()
    uri = get_totp_uri(secret)
    qr_base64 = generate_qr_code_base64(uri)
    return secret, qr_base64, uri


async def enable_totp(db: AsyncSession, secret: str, code: str) -> bool:
    """
    Enable TOTP after verifying a code from the authenticator app.

    Returns True if enabled, False if code was wrong.
    """
    if not verify_totp_code(secret, code):
        return False

    # Store the secret and enable flag
    await settings_service.set_setting(db, TOTP_CATEGORY, TOTP_SECRET_KEY, secret)
    await settings_service.set_setting(db, TOTP_CATEGORY, TOTP_ENABLED_KEY, "true")
    return True


async def disable_totp(db: AsyncSession, code: str) -> bool:
    """
    Disable TOTP after verifying a current code.

    Returns True if disabled, False if code was wrong.
    """
    secret = await get_totp_secret(db)
    if not secret:
        return False

    if not verify_totp_code(secret, code):
        return False

    # Clear secret and disable
    await settings_service.set_setting(db, TOTP_CATEGORY, TOTP_SECRET_KEY, "")
    await settings_service.set_setting(db, TOTP_CATEGORY, TOTP_ENABLED_KEY, "false")
    return True


async def verify_login_totp(db: AsyncSession, code: str) -> bool:
    """
    Verify TOTP code during login.

    Returns True if valid.
    """
    secret = await get_totp_secret(db)
    if not secret:
        return False
    return verify_totp_code(secret, code)


# =============================================================================
# RECOVERY CODES
# =============================================================================

RECOVERY_CODES_KEY = "recovery_codes"
RECOVERY_CODE_COUNT = 10


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """
    Generate recovery codes in XXXX-XXXX format.

    Returns a list of plain-text codes.
    """
    codes = []
    for _ in range(count):
        raw = secrets.token_hex(4).upper()  # 8 hex chars
        code = f"{raw[:4]}-{raw[4:]}"
        codes.append(code)
    return codes


def _hash_recovery_code(code: str) -> str:
    """Hash a recovery code with SHA256. Normalizes by stripping dashes and uppercasing."""
    normalized = code.replace("-", "").upper().strip()
    return hashlib.sha256(normalized.encode()).hexdigest()


async def store_recovery_codes(db: AsyncSession, plain_codes: list[str]) -> None:
    """Hash and store recovery codes in the settings table."""
    hashed = [_hash_recovery_code(c) for c in plain_codes]
    await settings_service.set_setting(
        db, TOTP_CATEGORY, RECOVERY_CODES_KEY, json.dumps(hashed)
    )


async def get_recovery_code_count(db: AsyncSession) -> int:
    """Get the number of remaining unused recovery codes."""
    security = await settings_service.get_settings_by_category(db, TOTP_CATEGORY)
    codes_raw = security.get(RECOVERY_CODES_KEY)
    if not codes_raw:
        return 0
    try:
        codes = json.loads(codes_raw) if isinstance(codes_raw, str) else codes_raw
        return len(codes)
    except (json.JSONDecodeError, TypeError):
        return 0


async def verify_recovery_code(db: AsyncSession, code: str) -> bool:
    """
    Verify and consume a recovery code.

    Returns True if valid (and marks the code as used by removing it).
    """
    if not code:
        return False

    security = await settings_service.get_settings_by_category(db, TOTP_CATEGORY)
    codes_raw = security.get(RECOVERY_CODES_KEY)
    if not codes_raw:
        return False

    try:
        hashed_codes = json.loads(codes_raw) if isinstance(codes_raw, str) else codes_raw
    except (json.JSONDecodeError, TypeError):
        return False

    if not isinstance(hashed_codes, list) or not hashed_codes:
        return False

    code_hash = _hash_recovery_code(code)

    if code_hash in hashed_codes:
        # Remove used code
        hashed_codes.remove(code_hash)
        await settings_service.set_setting(
            db, TOTP_CATEGORY, RECOVERY_CODES_KEY, json.dumps(hashed_codes)
        )
        await db.commit()
        return True

    return False


async def clear_recovery_codes(db: AsyncSession) -> None:
    """Remove all recovery codes (used when disabling 2FA)."""
    await settings_service.delete_setting(db, TOTP_CATEGORY, RECOVERY_CODES_KEY)
