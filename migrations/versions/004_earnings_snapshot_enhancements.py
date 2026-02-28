"""Add enhanced fields to earnings_snapshots for GST and labour breakdown.

Revision ID: 004_earnings_snapshot_enhancements
Revises: 003_earnings_snapshots
Create Date: 2026-02-01

This migration adds:
- revenue_inc_gst_cents: Total revenue including GST
- gst_collected_cents: GST component (goes to ATO)
- labour_wages_cents: Base wages paid to workers
- labour_super_cents: Superannuation (11.5%)
- labour_payg_cents: PAYG withholding

The existing revenue_cents now represents ex-GST revenue.
The existing labour_cents remains as total labour cost for backwards compatibility.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = '004_earnings_snapshot_enhancements'
down_revision = '003_earnings_snapshots'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check if table exists
    existing_tables = inspector.get_table_names()
    if 'earnings_snapshots' not in existing_tables:
        return

    # Get existing columns
    existing_columns = [col['name'] for col in inspector.get_columns('earnings_snapshots')]

    # Add revenue breakdown fields
    if 'revenue_inc_gst_cents' not in existing_columns:
        op.add_column('earnings_snapshots',
            sa.Column('revenue_inc_gst_cents', sa.Integer(), server_default='0'))

    if 'gst_collected_cents' not in existing_columns:
        op.add_column('earnings_snapshots',
            sa.Column('gst_collected_cents', sa.Integer(), server_default='0'))

    # Add labour breakdown fields
    if 'labour_wages_cents' not in existing_columns:
        op.add_column('earnings_snapshots',
            sa.Column('labour_wages_cents', sa.Integer(), server_default='0'))

    if 'labour_super_cents' not in existing_columns:
        op.add_column('earnings_snapshots',
            sa.Column('labour_super_cents', sa.Integer(), server_default='0'))

    if 'labour_payg_cents' not in existing_columns:
        op.add_column('earnings_snapshots',
            sa.Column('labour_payg_cents', sa.Integer(), server_default='0'))


def downgrade() -> None:
    # Remove the new columns
    op.drop_column('earnings_snapshots', 'labour_payg_cents')
    op.drop_column('earnings_snapshots', 'labour_super_cents')
    op.drop_column('earnings_snapshots', 'labour_wages_cents')
    op.drop_column('earnings_snapshots', 'gst_collected_cents')
    op.drop_column('earnings_snapshots', 'revenue_inc_gst_cents')
