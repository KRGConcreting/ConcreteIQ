"""Add quote_type column to quotes table.

Supports three quote types:
- 'calculator' (default) - full calculator-based quotes
- 'labour' - labour invoice quotes for subcontractors
- 'custom' - freeform quotes with manual line items

Revision ID: 014_quote_type
Revises: 013_safety_forms_and_sms_inbox
"""
from alembic import op
import sqlalchemy as sa

revision = '014_quote_type'
down_revision = '013_safety_forms_and_sms_inbox'
branch_labels = None
depends_on = None


def column_exists(table_name, column_name):
    """Check if a column already exists in the table."""
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade():
    if not column_exists('quotes', 'quote_type'):
        op.add_column('quotes', sa.Column(
            'quote_type', sa.String(20), nullable=True, server_default='calculator'
        ))
        # Backfill existing quotes
        op.execute("UPDATE quotes SET quote_type = 'calculator' WHERE quote_type IS NULL")


def downgrade():
    if column_exists('quotes', 'quote_type'):
        op.drop_column('quotes', 'quote_type')
