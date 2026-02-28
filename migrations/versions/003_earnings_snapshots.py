"""Add earnings_snapshots table for historical take-home tracking.

Revision ID: 003_earnings_snapshots
Revises: 002_expenses_reminders
Create Date: 2026-02-01

This migration adds:
- earnings_snapshots table for tracking owner take-home earnings over time
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = '003_earnings_snapshots'
down_revision = '002_expenses_reminders'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # Create earnings_snapshots table if not exists
    if 'earnings_snapshots' not in existing_tables:
        op.create_table(
            'earnings_snapshots',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('period_type', sa.String(20), nullable=False),
            sa.Column('period_start', sa.Date(), nullable=False),
            sa.Column('period_end', sa.Date(), nullable=False),
            sa.Column('revenue_cents', sa.Integer(), server_default='0'),
            sa.Column('materials_cents', sa.Integer(), server_default='0'),
            sa.Column('labour_cents', sa.Integer(), server_default='0'),
            sa.Column('expenses_cents', sa.Integer(), server_default='0'),
            sa.Column('take_home_cents', sa.Integer(), server_default='0'),
            sa.Column('jobs_completed', sa.Integer(), server_default='0'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        # Create unique constraint
        op.create_unique_constraint(
            'uq_earnings_snapshot_period',
            'earnings_snapshots',
            ['period_type', 'period_start']
        )
        # Create indexes
        op.create_index('idx_earnings_period_type', 'earnings_snapshots', ['period_type'])
        op.create_index('idx_earnings_period_start', 'earnings_snapshots', ['period_start'])


def downgrade() -> None:
    # Drop indexes
    op.drop_index('idx_earnings_period_start', table_name='earnings_snapshots')
    op.drop_index('idx_earnings_period_type', table_name='earnings_snapshots')

    # Drop unique constraint
    op.drop_constraint('uq_earnings_snapshot_period', 'earnings_snapshots', type_='unique')

    # Drop table
    op.drop_table('earnings_snapshots')
