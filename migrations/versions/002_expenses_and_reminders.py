"""Add expenses table, reminders table, and review_requested column.

Revision ID: 002_expenses_reminders
Revises: 001_settings
Create Date: 2026-02-01

This migration adds:
- expenses table for expense tracking
- reminders table for automated reminders
- review_requested column to quotes table
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = '002_expenses_reminders'
down_revision = '001_settings'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # Add review_requested column to quotes table
    if 'quotes' in existing_tables:
        columns = [col['name'] for col in inspector.get_columns('quotes')]
        if 'review_requested' not in columns:
            op.add_column('quotes', sa.Column('review_requested', sa.Boolean(), server_default='0', nullable=False))

    # Create reminders table if not exists
    if 'reminders' not in existing_tables:
        op.create_table(
            'reminders',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('reminder_type', sa.String(50), nullable=False),
            sa.Column('entity_type', sa.String(50), nullable=False),
            sa.Column('entity_id', sa.Integer(), nullable=False),
            sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=False),
            sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index('idx_reminder_scheduled', 'reminders', ['scheduled_for'])
        op.create_index('idx_reminder_entity', 'reminders', ['entity_type', 'entity_id'])
        op.create_index('idx_reminder_type', 'reminders', ['reminder_type'])

    # Create expenses table if not exists
    if 'expenses' not in existing_tables:
        op.create_table(
            'expenses',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('expense_number', sa.String(20), nullable=False, unique=True),
            sa.Column('category', sa.String(50), nullable=False),
            sa.Column('description', sa.Text(), nullable=False),
            sa.Column('vendor', sa.String(255), nullable=True),
            sa.Column('amount_cents', sa.Integer(), nullable=False),
            sa.Column('gst_cents', sa.Integer(), server_default='0'),
            sa.Column('expense_date', sa.Date(), nullable=False),
            sa.Column('receipt_photo_id', sa.Integer(), sa.ForeignKey('photos.id'), nullable=True),
            sa.Column('receipt_url', sa.Text(), nullable=True),
            sa.Column('quote_id', sa.Integer(), sa.ForeignKey('quotes.id'), nullable=True),
            sa.Column('payment_method', sa.String(50), server_default='card'),
            sa.Column('xero_bill_id', sa.String(100), nullable=True),
            sa.Column('synced_to_xero_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index('idx_expense_number', 'expenses', ['expense_number'])
        op.create_index('idx_expense_date', 'expenses', ['expense_date'])
        op.create_index('idx_expense_category', 'expenses', ['category'])
        op.create_index('idx_expense_quote', 'expenses', ['quote_id'])


def downgrade() -> None:
    # Drop expenses table
    op.drop_index('idx_expense_quote', table_name='expenses')
    op.drop_index('idx_expense_category', table_name='expenses')
    op.drop_index('idx_expense_date', table_name='expenses')
    op.drop_index('idx_expense_number', table_name='expenses')
    op.drop_table('expenses')

    # Drop reminders table
    op.drop_index('idx_reminder_type', table_name='reminders')
    op.drop_index('idx_reminder_entity', table_name='reminders')
    op.drop_index('idx_reminder_scheduled', table_name='reminders')
    op.drop_table('reminders')

    # Remove review_requested column from quotes
    op.drop_column('quotes', 'review_requested')
