"""Add GPS to photos, unified communication log, PII encryption columns.

Revision ID: 009_status_flow_comms_gps
Revises: 008_customer_line_items
Create Date: 2026-02-14

Changes:
- photos: Add gps_lat, gps_lng columns
- communication_log: New unified table (replaces email_log + sms_log)
- customers: Add encrypted PII columns + hash indexes
- Data migration: Copy email_log and sms_log rows into communication_log
- Old tables (email_log, sms_log) kept for safety — drop in a future migration
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = '009_status_flow_comms_gps'
down_revision = '008_customer_line_items'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # 1. GPS on Photos
    # =========================================================================
    op.add_column('photos', sa.Column('gps_lat', sa.Numeric(10, 7), nullable=True))
    op.add_column('photos', sa.Column('gps_lng', sa.Numeric(10, 7), nullable=True))

    # =========================================================================
    # 2. Unified Communication Log
    # =========================================================================
    op.create_table(
        'communication_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('channel', sa.String(20), nullable=False),
        sa.Column('direction', sa.String(20), server_default='outbound'),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customers.id'), nullable=True),
        sa.Column('quote_id', sa.Integer(), sa.ForeignKey('quotes.id'), nullable=True),
        sa.Column('invoice_id', sa.Integer(), sa.ForeignKey('invoices.id'), nullable=True),
        sa.Column('to_address', sa.String(255), nullable=True),
        sa.Column('to_phone', sa.String(50), nullable=True),
        sa.Column('subject', sa.String(255), nullable=True),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('template', sa.String(100), nullable=True),
        sa.Column('provider_message_id', sa.String(255), nullable=True),
        sa.Column('status', sa.String(50), server_default='sent'),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('clicked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index('idx_comm_customer', 'communication_log', ['customer_id'])
    op.create_index('idx_comm_quote', 'communication_log', ['quote_id'])
    op.create_index('idx_comm_invoice', 'communication_log', ['invoice_id'])
    op.create_index('idx_comm_channel', 'communication_log', ['channel'])
    op.create_index('idx_comm_created', 'communication_log', ['created_at'])

    # =========================================================================
    # 2b. Migrate existing email_log data into communication_log
    # =========================================================================
    # Check if email_log exists before migrating (might not exist in fresh installs)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'email_log' in existing_tables:
        op.execute("""
            INSERT INTO communication_log
                (channel, direction, quote_id, invoice_id,
                 to_address, subject, template,
                 provider_message_id, status,
                 sent_at, delivered_at, opened_at, clicked_at, created_at)
            SELECT
                'email', 'outbound', quote_id, invoice_id,
                to_address, subject, template,
                postmark_message_id, status,
                sent_at, delivered_at, opened_at, clicked_at, created_at
            FROM email_log
        """)

    if 'sms_log' in existing_tables:
        op.execute("""
            INSERT INTO communication_log
                (channel, direction, quote_id, invoice_id,
                 to_phone, body,
                 provider_message_id, status,
                 created_at)
            SELECT
                'sms', 'outbound', quote_id, invoice_id,
                to_phone, message,
                provider_message_id, status,
                created_at
            FROM sms_log
        """)

    # NOTE: Not dropping email_log or sms_log for safety.
    # Drop them in a future migration once verified.

    # =========================================================================
    # 3. PII Encryption columns on Customers
    # =========================================================================
    op.add_column('customers', sa.Column('email_encrypted', sa.Text(), nullable=True))
    op.add_column('customers', sa.Column('phone_encrypted', sa.Text(), nullable=True))
    op.add_column('customers', sa.Column('phone2_encrypted', sa.Text(), nullable=True))
    op.add_column('customers', sa.Column('email_hash', sa.String(64), nullable=True))
    op.add_column('customers', sa.Column('phone_hash', sa.String(64), nullable=True))
    op.add_column('customers', sa.Column('phone2_hash', sa.String(64), nullable=True))

    op.create_index('idx_customer_email_hash', 'customers', ['email_hash'])
    op.create_index('idx_customer_phone_hash', 'customers', ['phone_hash'])


def downgrade() -> None:
    # 3. Remove PII encryption columns
    op.drop_index('idx_customer_phone_hash', 'customers')
    op.drop_index('idx_customer_email_hash', 'customers')
    op.drop_column('customers', 'phone2_hash')
    op.drop_column('customers', 'phone_hash')
    op.drop_column('customers', 'email_hash')
    op.drop_column('customers', 'phone2_encrypted')
    op.drop_column('customers', 'phone_encrypted')
    op.drop_column('customers', 'email_encrypted')

    # 2. Remove communication_log
    op.drop_index('idx_comm_created', 'communication_log')
    op.drop_index('idx_comm_channel', 'communication_log')
    op.drop_index('idx_comm_invoice', 'communication_log')
    op.drop_index('idx_comm_quote', 'communication_log')
    op.drop_index('idx_comm_customer', 'communication_log')
    op.drop_table('communication_log')

    # 1. Remove GPS columns
    op.drop_column('photos', 'gps_lng')
    op.drop_column('photos', 'gps_lat')
