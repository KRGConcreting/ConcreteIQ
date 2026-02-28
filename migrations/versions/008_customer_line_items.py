"""Add customer_line_items JSON column to quotes table.

Revision ID: 008_customer_line_items
Revises: 007_pour_results
Create Date: 2026-02-08

Adds a separate customer-facing line items field for the Quote Preview page.
This is distinct from the internal calculator line_items — it stores editable
grouped categories that the customer sees on the quote.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = '008_customer_line_items'
down_revision = '007_pour_results'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('quotes', sa.Column('customer_line_items', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('quotes', 'customer_line_items')
