"""Add settings module with category support.

Revision ID: 001_settings
Revises:
Create Date: 2026-01-31

This migration updates the settings table to support:
- Category-based organization (pricing, business, sms, etc.)
- Value type tracking (string, int, float, bool, json)
- Unique constraint on category+key combination
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = '001_settings'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Check if settings table exists and needs to be altered
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if 'settings' in inspector.get_table_names():
        # Drop old settings table and recreate with new schema
        # NOTE: This will lose existing settings data - backup if needed
        op.drop_table('settings')

    # Create new settings table with category support
    op.create_table(
        'settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('category', sa.String(50), nullable=False),
        sa.Column('key', sa.String(100), nullable=False),
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('value_type', sa.String(20), server_default='string'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Add unique constraint and index
    op.create_unique_constraint('uq_setting_category_key', 'settings', ['category', 'key'])
    op.create_index('idx_setting_category', 'settings', ['category'])


def downgrade() -> None:
    # Drop new settings table
    op.drop_index('idx_setting_category', table_name='settings')
    op.drop_constraint('uq_setting_category_key', 'settings', type_='unique')
    op.drop_table('settings')

    # Recreate original simple key-value settings table
    op.create_table(
        'settings',
        sa.Column('key', sa.String(100), primary_key=True),
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
