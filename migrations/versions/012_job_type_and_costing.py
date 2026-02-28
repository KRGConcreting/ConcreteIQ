"""Add job_type to quotes and job_costing table

Revision ID: 012_job_type_and_costing
Revises: 011_amendment_upgrade
"""
from alembic import op
import sqlalchemy as sa

revision = '012_job_type_and_costing'
down_revision = '011_amendment_upgrade'
branch_labels = None
depends_on = None


def column_exists(table_name, column_name):
    """Check if a column already exists in the table."""
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def table_exists(table_name):
    """Check if a table already exists."""
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade():
    # Add job_type to quotes
    if not column_exists('quotes', 'job_type'):
        op.add_column('quotes', sa.Column('job_type', sa.String(50), nullable=True))

    # Create job_costings table for post-job profitability analysis
    if not table_exists('job_costings'):
        op.create_table(
            'job_costings',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('quote_id', sa.Integer(), sa.ForeignKey('quotes.id'), nullable=False, unique=True),

            # Quoted amounts (copied from quote at creation time for snapshot)
            sa.Column('quoted_total_cents', sa.Integer(), default=0),

            # Actual costs (entered post-job)
            sa.Column('actual_concrete_cents', sa.Integer(), default=0),
            sa.Column('actual_concrete_m3', sa.Numeric(8, 2), nullable=True),
            sa.Column('actual_labour_cents', sa.Integer(), default=0),
            sa.Column('actual_labour_hours', sa.Numeric(6, 2), nullable=True),
            sa.Column('actual_materials_cents', sa.Integer(), default=0),
            sa.Column('actual_pump_cents', sa.Integer(), default=0),
            sa.Column('actual_other_cents', sa.Integer(), default=0),
            sa.Column('other_description', sa.Text(), nullable=True),

            # Calculated fields (updated on save)
            sa.Column('actual_total_cents', sa.Integer(), default=0),
            sa.Column('profit_cents', sa.Integer(), default=0),
            sa.Column('margin_percent', sa.Numeric(5, 2), nullable=True),

            # Notes
            sa.Column('notes', sa.Text(), nullable=True),

            # Timestamps
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index('idx_job_costing_quote', 'job_costings', ['quote_id'])


def downgrade():
    op.drop_table('job_costings')
    op.drop_column('quotes', 'job_type')
