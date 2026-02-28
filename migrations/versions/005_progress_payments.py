"""Add progress payment fields to Invoice and Quote models.

Revision ID: 005_progress_payments
Revises: 004_earnings_snapshot_enhancements
Create Date: 2026-02-01

This migration adds:
- Invoice.stage_percent: Integer for tracking payment stage percentage (30, 60, 10)
- Quote.payment_schedule: JSON for customizable payment schedule configuration
- Quote.total_invoiced_cents: Cached total of all invoices created for the quote
- Quote.total_paid_cents: Cached total of all payments received for the quote

These fields support the progress payment system where concrete jobs are billed
in stages: Deposit (30%), Pre-Pour (60%), and Final (10%).
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = '005_progress_payments'
down_revision = '004_earnings_snapshot_enhancements'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Get existing tables
    existing_tables = inspector.get_table_names()

    # Add fields to invoices table
    if 'invoices' in existing_tables:
        existing_columns = [col['name'] for col in inspector.get_columns('invoices')]

        if 'stage_percent' not in existing_columns:
            op.add_column('invoices',
                sa.Column('stage_percent', sa.Integer(), nullable=True))

    # Add fields to quotes table
    if 'quotes' in existing_tables:
        existing_columns = [col['name'] for col in inspector.get_columns('quotes')]

        if 'payment_schedule' not in existing_columns:
            op.add_column('quotes',
                sa.Column('payment_schedule', sa.JSON(), nullable=True))

        if 'total_invoiced_cents' not in existing_columns:
            op.add_column('quotes',
                sa.Column('total_invoiced_cents', sa.Integer(), server_default='0'))

        if 'total_paid_cents' not in existing_columns:
            op.add_column('quotes',
                sa.Column('total_paid_cents', sa.Integer(), server_default='0'))


def downgrade() -> None:
    # Remove the new columns
    op.drop_column('quotes', 'total_paid_cents')
    op.drop_column('quotes', 'total_invoiced_cents')
    op.drop_column('quotes', 'payment_schedule')
    op.drop_column('invoices', 'stage_percent')
