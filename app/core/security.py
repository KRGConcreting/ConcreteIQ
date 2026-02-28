"""
PII Encryption — Fernet symmetric encryption for customer data at rest.

Uses Fernet (AES-128-CBC with HMAC-SHA256) for field-level encryption.
Deterministic hashing (SHA256) for searchable lookups.

When ENCRYPTION_KEY is not configured, all functions are no-ops — plaintext
columns are used as-is. This lets dev environments run without encryption.

Generate a key:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import hashlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _get_fernet():
    """Get Fernet instance from configured key. Returns None if not configured."""
    from app.config import settings
    if not settings.encryption_key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(settings.encryption_key.encode())
    except Exception as e:
        logger.error(f"Failed to initialize Fernet: {e}")
        return None


def encrypt_value(value: Optional[str]) -> Optional[str]:
    """Encrypt a string value. Returns base64-encoded ciphertext, or None."""
    if not value:
        return None
    f = _get_fernet()
    if not f:
        return None
    try:
        return f.encrypt(value.encode()).decode()
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        return None


def decrypt_value(encrypted: Optional[str]) -> Optional[str]:
    """Decrypt a Fernet-encrypted value. Returns plaintext, or None."""
    if not encrypted:
        return None
    f = _get_fernet()
    if not f:
        return None
    try:
        return f.decrypt(encrypted.encode()).decode()
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        return None


def hash_value(value: Optional[str]) -> Optional[str]:
    """SHA256 hash for exact-match searching. Normalised (lowercase, stripped)."""
    if not value:
        return None
    normalised = value.strip().lower()
    return hashlib.sha256(normalised.encode()).hexdigest()


def is_encryption_active() -> bool:
    """Check if encryption is configured."""
    from app.config import settings
    return bool(settings.encryption_key)


def encrypt_customer_pii(customer) -> None:
    """
    Encrypt PII fields on a Customer object before saving.

    Mutates in place: sets encrypted columns + hash columns, clears plaintext.
    No-op if encryption is not configured.
    """
    if not is_encryption_active():
        return

    if customer.email:
        customer.email_encrypted = encrypt_value(customer.email)
        customer.email_hash = hash_value(customer.email)
        customer.email = None

    if customer.phone:
        customer.phone_encrypted = encrypt_value(customer.phone)
        customer.phone_hash = hash_value(customer.phone)
        customer.phone = None

    if customer.phone2:
        customer.phone2_encrypted = encrypt_value(customer.phone2)
        customer.phone2_hash = hash_value(customer.phone2)
        customer.phone2 = None


def decrypt_customer_pii(customer) -> None:
    """
    Decrypt PII fields on a Customer object for display/use.

    Mutates in place: populates plaintext columns from encrypted columns.
    No-op if encryption is not configured or no encrypted data exists.
    """
    if not is_encryption_active():
        return

    if customer.email_encrypted and not customer.email:
        customer.email = decrypt_value(customer.email_encrypted)

    if customer.phone_encrypted and not customer.phone:
        customer.phone = decrypt_value(customer.phone_encrypted)

    if customer.phone2_encrypted and not customer.phone2:
        customer.phone2 = decrypt_value(customer.phone2_encrypted)
