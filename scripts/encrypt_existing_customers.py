"""
One-time script to encrypt existing customer PII data.

Run this after:
1. Setting ENCRYPTION_KEY in .env
2. Running the 009 migration (alembic upgrade head)

Usage:
    python -m scripts.encrypt_existing_customers

Or from project root:
    python scripts/encrypt_existing_customers.py
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings


async def migrate():
    if not settings.encryption_key:
        print("ERROR: ENCRYPTION_KEY not set in environment. Nothing to do.")
        return

    from app.database import async_session_maker
    from app.models import Customer
    from app.core.security import encrypt_value, hash_value
    from sqlalchemy import select

    async with async_session_maker() as db:
        result = await db.execute(select(Customer))
        customers = result.scalars().all()

        encrypted_count = 0
        skipped_count = 0

        for customer in customers:
            changed = False

            if customer.email and not customer.email_encrypted:
                customer.email_encrypted = encrypt_value(customer.email)
                customer.email_hash = hash_value(customer.email)
                customer.email = None
                changed = True

            if customer.phone and not customer.phone_encrypted:
                customer.phone_encrypted = encrypt_value(customer.phone)
                customer.phone_hash = hash_value(customer.phone)
                customer.phone = None
                changed = True

            if customer.phone2 and not customer.phone2_encrypted:
                customer.phone2_encrypted = encrypt_value(customer.phone2)
                customer.phone2_hash = hash_value(customer.phone2)
                customer.phone2 = None
                changed = True

            if changed:
                encrypted_count += 1
            else:
                skipped_count += 1

        await db.commit()
        print(f"Done. Encrypted: {encrypted_count}, Skipped (already encrypted): {skipped_count}")


if __name__ == "__main__":
    asyncio.run(migrate())
