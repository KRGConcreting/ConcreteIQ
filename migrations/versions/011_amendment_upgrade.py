"""Add declined_at and decline_reason to quote_amendments

Revision ID: 011_amendment_upgrade
Revises: 010_comms_features
"""
from alembic import op
import sqlalchemy as sa

revision = '011_amendment_upgrade'
down_revision = '010_comms_features'
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
    if not column_exists('quote_amendments', 'declined_at'):
        op.add_column('quote_amendments', sa.Column('declined_at', sa.DateTime(timezone=True), nullable=True))
    if not column_exists('quote_amendments', 'decline_reason'):
        op.add_column('quote_amendments', sa.Column('decline_reason', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('quote_amendments', 'decline_reason')
    op.drop_column('quote_amendments', 'declined_at')
