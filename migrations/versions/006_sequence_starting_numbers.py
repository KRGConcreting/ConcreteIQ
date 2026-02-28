"""Set invoice and quote sequence starting numbers.

Revision ID: 006_sequence_starting_numbers
Revises: 005_progress_payments
Create Date: 2026-02-01

This migration sets the starting numbers for invoice and quote sequences
so that new documents start at 1126 (e.g., Q-2026-01126, INV-2026-01126).
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = '006_sequence_starting_numbers'
down_revision = '005_progress_payments'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    existing_tables = inspector.get_table_names()

    if 'sequences' in existing_tables:
        # Set starting values for 2026 sequences
        # Value is set to 1125 so the NEXT number generated will be 1126
        year = 2026
        starting_value = 1125

        # Check if sequences exist and update/insert accordingly
        # Using raw SQL for upsert logic
        conn.execute(sa.text("""
            INSERT INTO sequences (name, current_value)
            VALUES (:quote_name, :value)
            ON CONFLICT (name) DO UPDATE SET current_value =
                CASE WHEN sequences.current_value < :value
                     THEN :value
                     ELSE sequences.current_value
                END
        """), {"quote_name": f"quote_{year}", "value": starting_value})

        conn.execute(sa.text("""
            INSERT INTO sequences (name, current_value)
            VALUES (:invoice_name, :value)
            ON CONFLICT (name) DO UPDATE SET current_value =
                CASE WHEN sequences.current_value < :value
                     THEN :value
                     ELSE sequences.current_value
                END
        """), {"invoice_name": f"invoice_{year}", "value": starting_value})

        conn.execute(sa.text("""
            INSERT INTO sequences (name, current_value)
            VALUES (:expense_name, :value)
            ON CONFLICT (name) DO UPDATE SET current_value =
                CASE WHEN sequences.current_value < :value
                     THEN :value
                     ELSE sequences.current_value
                END
        """), {"expense_name": f"expense_{year}", "value": starting_value})


def downgrade() -> None:
    # Don't modify sequences on downgrade - they may have been used
    pass
