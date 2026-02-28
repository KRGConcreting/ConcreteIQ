"""Add safety_forms table, signature_type column, and SMS inbox columns.

Features covered:
- Feature 1: signature_type on quotes (draw/typed)
- Feature 6: safety_forms table for SWMS/JSA
- Feature 9: read_at and from_phone on communication_log for two-way SMS

Revision ID: 013_safety_forms_and_sms_inbox
Revises: 012_job_type_and_costing
"""
from alembic import op
import sqlalchemy as sa

revision = '013_safety_forms_and_sms_inbox'
down_revision = '012_job_type_and_costing'
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
    # --- Feature 1: Customer Signature Type ---
    if not column_exists('quotes', 'signature_type'):
        op.add_column('quotes', sa.Column('signature_type', sa.String(10), nullable=True))

    # --- Feature 6: Safety Forms (SWMS / JSA) ---
    if not table_exists('safety_forms'):
        op.create_table(
            'safety_forms',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('quote_id', sa.Integer(), sa.ForeignKey('quotes.id'), nullable=False),
            sa.Column('form_type', sa.String(10), nullable=False),  # 'swms' or 'jsa'
            sa.Column('form_data', sa.JSON(), nullable=True),
            sa.Column('status', sa.String(20), default='draft'),
            sa.Column('signed_by', sa.String(255), nullable=True),
            sa.Column('signed_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('signature_data', sa.Text(), nullable=True),
            sa.Column('pdf_path', sa.String(500), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index('idx_safety_form_quote', 'safety_forms', ['quote_id'])
        op.create_index('idx_safety_form_type', 'safety_forms', ['form_type'])

    # --- Feature 9: Two-Way SMS ---
    if not column_exists('communication_log', 'read_at'):
        op.add_column('communication_log', sa.Column('read_at', sa.DateTime(timezone=True), nullable=True))

    if not column_exists('communication_log', 'from_phone'):
        op.add_column('communication_log', sa.Column('from_phone', sa.String(50), nullable=True))


def downgrade():
    # Remove SMS inbox columns
    if column_exists('communication_log', 'from_phone'):
        op.drop_column('communication_log', 'from_phone')
    if column_exists('communication_log', 'read_at'):
        op.drop_column('communication_log', 'read_at')

    # Remove safety_forms table
    if table_exists('safety_forms'):
        op.drop_table('safety_forms')

    # Remove signature_type
    if column_exists('quotes', 'signature_type'):
        op.drop_column('quotes', 'signature_type')
