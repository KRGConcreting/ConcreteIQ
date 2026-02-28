"""Add followup_count to quotes, create progress_updates table.

Revision ID: 010_comms_features
Revises: 009_status_flow_comms_gps
"""

from alembic import op
import sqlalchemy as sa

revision = '010_comms_features'
down_revision = '009_status_flow_comms_gps'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add followup_count to quotes
    op.add_column('quotes', sa.Column('followup_count', sa.Integer(), server_default='0', nullable=False))

    # 2. Create progress_updates table
    op.create_table(
        'progress_updates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('quote_id', sa.Integer(), sa.ForeignKey('quotes.id'), nullable=False),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customers.id'), nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('photo_ids', sa.JSON(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('idx_progress_update_quote', 'progress_updates', ['quote_id'])
    op.create_index('idx_progress_update_customer', 'progress_updates', ['customer_id'])


def downgrade() -> None:
    op.drop_index('idx_progress_update_customer', 'progress_updates')
    op.drop_index('idx_progress_update_quote', 'progress_updates')
    op.drop_table('progress_updates')
    op.drop_column('quotes', 'followup_count')
