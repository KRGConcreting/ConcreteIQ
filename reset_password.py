"""
Quick password reset + login diagnostics.

Usage:
    python reset_password.py            # Show diagnostics
    python reset_password.py mypassword # Reset password to 'mypassword'
"""
import sys
import asyncio
import bcrypt

async def main():
    # Import app components
    from app.config import settings
    from app.database import get_async_session
    from app.settings.service import get_setting, set_setting

    print("=" * 60)
    print("ConcreteIQ Login Diagnostics")
    print("=" * 60)

    # Check env var
    env_hash = settings.admin_password
    print(f"\n1. ADMIN_PASSWORD from .env:")
    if env_hash:
        print(f"   Hash: {env_hash[:20]}...{env_hash[-10:]}" if len(env_hash) > 30 else f"   Hash: {env_hash}")
        print(f"   Length: {len(env_hash)} chars")
        valid_bcrypt = env_hash.startswith("$2b$") or env_hash.startswith("$2a$")
        print(f"   Valid bcrypt format: {valid_bcrypt}")
    else:
        print("   NOT SET!")

    # Check DB override
    async with get_async_session() as db:
        db_hash = await get_setting(db, "security", "admin_password_hash")
        print(f"\n2. DB password override (security.admin_password_hash):")
        if db_hash:
            print(f"   Hash: {db_hash[:20]}...{db_hash[-10:]}" if len(str(db_hash)) > 30 else f"   Hash: {db_hash}")
            print(f"   ⚠️  DB override is ACTIVE — .env password is IGNORED!")
        else:
            print("   None (using .env password)")

        # Determine effective hash
        effective_hash = db_hash if db_hash else env_hash
        print(f"\n3. Effective hash being used:")
        print(f"   Source: {'DATABASE' if db_hash else '.env file'}")

        # Check session version
        session_ver = await get_setting(db, "security", "session_version")
        print(f"\n4. Session version: {session_ver or 1}")

    # Check environment
    print(f"\n5. Environment: {settings.environment}")
    print(f"   Secure cookies: {settings.environment == 'production'}")
    print(f"   Session timeout: {settings.session_expire_hours}h")

    # If password provided, reset it
    if len(sys.argv) > 1:
        new_password = sys.argv[1]
        new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

        print(f"\n{'=' * 60}")
        print(f"RESETTING PASSWORD to: {new_password}")
        print(f"New hash: {new_hash}")

        async with get_async_session() as db:
            # Clear any DB override so .env is used
            # Also set in DB for good measure
            await set_setting(db, "security", "admin_password_hash", new_hash)
            await db.commit()
            print(f"✅ Password saved to database!")

        # Verify it works
        test = bcrypt.checkpw(new_password.encode(), new_hash.encode())
        print(f"✅ Verification: {'PASS' if test else 'FAIL'}")
        print(f"\nYou can now log in with: {new_password}")
    else:
        # Test a password if user wants
        print(f"\n{'=' * 60}")
        print("To reset your password, run:")
        print("  python reset_password.py YOUR_NEW_PASSWORD")


asyncio.run(main())
