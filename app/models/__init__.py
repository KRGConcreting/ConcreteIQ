"""
Database Models — All tables defined here.

RULES:
- All money fields are INTEGER (cents)
- All datetime fields are TIMESTAMP WITH TIMEZONE
- All dates default to Sydney timezone
"""

from datetime import datetime, date, time
from decimal import Decimal
from typing import Optional, List
from sqlalchemy import (
    String, Integer, Text, Boolean, Date, DateTime, Time, Float,
    ForeignKey, Index, UniqueConstraint, Numeric, JSON
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
# Use JSON (not JSONB) for SQLite compatibility — JSONB is PostgreSQL-specific

from app.database import Base
from app.core.dates import sydney_now


# =============================================================================
# CUSTOMERS
# =============================================================================

class Customer(Base):
    """Customer/client information."""
    
    __tablename__ = "customers"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    
    # Basic info
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    phone2: Mapped[Optional[str]] = mapped_column(String(50))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    
    # Address
    street: Mapped[Optional[str]] = mapped_column(String(255))
    unit: Mapped[Optional[str]] = mapped_column(String(50))
    city: Mapped[Optional[str]] = mapped_column(String(100))
    state: Mapped[Optional[str]] = mapped_column(String(20))
    postcode: Mapped[Optional[str]] = mapped_column(String(10))
    
    # Business customers
    business_name: Mapped[Optional[str]] = mapped_column(String(255))
    contact_position: Mapped[Optional[str]] = mapped_column(String(100))
    customer_type: Mapped[str] = mapped_column(String(20), default="residential")
    
    # Preferences
    notify_email: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_sms: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Lead tracking
    source: Mapped[Optional[str]] = mapped_column(String(100))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # Discount
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)

    # Encrypted PII storage (populated when ENCRYPTION_KEY is set)
    email_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    phone_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    phone2_encrypted: Mapped[Optional[str]] = mapped_column(Text)

    # Hashes for exact-match search (SHA256)
    email_hash: Mapped[Optional[str]] = mapped_column(String(64))
    phone_hash: Mapped[Optional[str]] = mapped_column(String(64))
    phone2_hash: Mapped[Optional[str]] = mapped_column(String(64))

    # Portal access
    portal_access_token: Mapped[Optional[str]] = mapped_column(String(64), unique=True)

    # Xero integration
    xero_contact_id: Mapped[Optional[str]] = mapped_column(String(255))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now, onupdate=sydney_now)
    
    # Relationships
    quotes: Mapped[List["Quote"]] = relationship(back_populates="customer")
    invoices: Mapped[List["Invoice"]] = relationship(back_populates="customer")
    
    __table_args__ = (
        Index('idx_customer_name', 'name'),
        Index('idx_customer_email', 'email'),
        Index('idx_customer_phone', 'phone'),
        Index('idx_customer_email_hash', 'email_hash'),
        Index('idx_customer_phone_hash', 'phone_hash'),
        Index('idx_customer_portal_token', 'portal_access_token'),
    )


# =============================================================================
# QUOTES
# =============================================================================

class Quote(Base):
    """Quote/estimate for a job."""
    
    __tablename__ = "quotes"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    quote_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    
    # Quote type: 'calculator', 'labour', 'custom'
    quote_type: Mapped[str] = mapped_column(String(20), default="calculator")

    # Job info
    job_name: Mapped[Optional[str]] = mapped_column(String(255))
    job_type: Mapped[Optional[str]] = mapped_column(String(50))  # Driveway, Pathway, Slab, Crossover, Pool Surround, Alfresco, Commercial, Other
    job_address: Mapped[Optional[str]] = mapped_column(Text)
    job_address_lat: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7))
    job_address_lng: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7))
    distance_km: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    
    # Calculator data (stored as JSON for flexibility)
    calculator_input: Mapped[Optional[dict]] = mapped_column(JSON)
    calculator_result: Mapped[Optional[dict]] = mapped_column(JSON)
    line_items: Mapped[Optional[list]] = mapped_column(JSON)
    customer_line_items: Mapped[Optional[list]] = mapped_column(JSON)  # Editable customer-facing groups

    # Amounts (ALL IN CENTS)
    subtotal_cents: Mapped[int] = mapped_column(Integer, default=0)
    discount_cents: Mapped[int] = mapped_column(Integer, default=0)
    gst_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, default=0)
    
    # Status: draft, sent, viewed, accepted, declined, expired, confirmed, pour_stage, pending_completion, completed
    status: Mapped[str] = mapped_column(String(20), default="draft")
    
    # Dates
    quote_date: Mapped[Optional[date]] = mapped_column(Date)
    expiry_date: Mapped[Optional[date]] = mapped_column(Date)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    viewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    # Decline tracking
    declined_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    decline_reason: Mapped[Optional[str]] = mapped_column(Text)

    # Booking
    requested_start_date: Mapped[Optional[date]] = mapped_column(Date)
    confirmed_start_date: Mapped[Optional[date]] = mapped_column(Date)
    completed_date: Mapped[Optional[date]] = mapped_column(Date)

    # Review tracking
    review_requested: Mapped[bool] = mapped_column(default=False)

    # Followup tracking
    followup_count: Mapped[int] = mapped_column(Integer, default=0)

    # Payment schedule configuration (customizable per quote)
    payment_schedule: Mapped[Optional[dict]] = mapped_column(JSON)  # {"deposit": {"percent": 30, "due": "on_acceptance"}, ...}

    # Cached payment totals (updated when invoices/payments change)
    total_invoiced_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_paid_cents: Mapped[int] = mapped_column(Integer, default=0)

    @property
    def payment_progress_percent(self) -> int:
        """Calculate payment progress as a percentage."""
        if self.total_cents == 0:
            return 0
        return int((self.total_paid_cents / self.total_cents) * 100)

    @property
    def payment_status(self) -> str:
        """Overall payment status for the job."""
        if self.total_paid_cents >= self.total_cents:
            return "paid_in_full"
        elif self.total_paid_cents > 0:
            return "partially_paid"
        else:
            return "unpaid"

    @property
    def outstanding_cents(self) -> int:
        """Amount still outstanding."""
        return max(0, self.total_cents - self.total_paid_cents)

    # Signature capture
    signature_data: Mapped[Optional[str]] = mapped_column(Text)  # Base64 PNG or typed name
    signature_type: Mapped[Optional[str]] = mapped_column(String(10))  # 'drawn' or 'typed'
    signature_name: Mapped[Optional[str]] = mapped_column(String(255))
    signature_ip: Mapped[Optional[str]] = mapped_column(String(50))
    signed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    # Integration
    gcal_event_id: Mapped[Optional[str]] = mapped_column(String(255))
    
    # Portal access
    portal_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    
    # Notes
    notes: Mapped[Optional[str]] = mapped_column(Text)  # Customer-visible
    internal_notes: Mapped[Optional[str]] = mapped_column(Text)  # Internal only
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now, onupdate=sydney_now)
    
    # Relationships
    customer: Mapped["Customer"] = relationship(back_populates="quotes")
    invoices: Mapped[List["Invoice"]] = relationship(back_populates="quote")
    amendments: Mapped[List["QuoteAmendment"]] = relationship(back_populates="quote")
    time_entries: Mapped[List["TimeEntry"]] = relationship(back_populates="quote")
    photos: Mapped[List["Photo"]] = relationship(back_populates="quote")
    job_assignments: Mapped[List["JobAssignment"]] = relationship(back_populates="quote")

    __table_args__ = (
        Index('idx_quote_number', 'quote_number'),
        Index('idx_quote_customer', 'customer_id'),
        Index('idx_quote_status', 'status'),
        Index('idx_quote_portal_token', 'portal_token'),
    )


class QuoteAmendment(Base):
    """Amendment/variation to an accepted quote."""
    
    __tablename__ = "quote_amendments"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), nullable=False)
    amendment_number: Mapped[int] = mapped_column(Integer, nullable=False)
    
    description: Mapped[str] = mapped_column(Text, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)  # Can be negative
    
    # Status: draft, sent, accepted, declined
    status: Mapped[str] = mapped_column(String(20), default="draft")
    
    portal_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    signature_data: Mapped[Optional[str]] = mapped_column(Text)
    signature_name: Mapped[Optional[str]] = mapped_column(String(255))

    declined_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    decline_reason: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)

    # Relationships
    quote: Mapped["Quote"] = relationship(back_populates="amendments")


# =============================================================================
# INVOICES
# =============================================================================

class Invoice(Base):
    """Tax invoice."""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    quote_id: Mapped[Optional[int]] = mapped_column(ForeignKey("quotes.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)

    # Description
    description: Mapped[Optional[str]] = mapped_column(String(255))
    stage: Mapped[Optional[str]] = mapped_column(String(20))  # booking, prepour, completion, variation, manual
    stage_percent: Mapped[Optional[int]] = mapped_column(Integer)  # 30, 60, 10 etc for progress payment tracking
    line_items: Mapped[Optional[list]] = mapped_column(JSON)
    
    # Amounts (ALL IN CENTS)
    subtotal_cents: Mapped[int] = mapped_column(Integer, default=0)
    gst_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, default=0)
    paid_cents: Mapped[int] = mapped_column(Integer, default=0)
    
    # Status: draft, sent, viewed, paid, partial, overdue, voided
    status: Mapped[str] = mapped_column(String(20), default="draft")
    
    # Dates
    issue_date: Mapped[Optional[date]] = mapped_column(Date)
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    paid_date: Mapped[Optional[date]] = mapped_column(Date)
    
    # Integration
    xero_invoice_id: Mapped[Optional[str]] = mapped_column(String(255))
    xero_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Portal
    portal_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    
    notes: Mapped[Optional[str]] = mapped_column(Text)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now, onupdate=sydney_now)
    
    # Relationships
    customer: Mapped["Customer"] = relationship(back_populates="invoices")
    quote: Mapped[Optional["Quote"]] = relationship(back_populates="invoices")
    payments: Mapped[List["Payment"]] = relationship(back_populates="invoice")
    
    __table_args__ = (
        Index('idx_invoice_number', 'invoice_number'),
        Index('idx_invoice_customer', 'customer_id'),
        Index('idx_invoice_quote', 'quote_id'),
        Index('idx_invoice_status', 'status'),
        Index('idx_invoice_portal_token', 'portal_token'),
    )


# =============================================================================
# PAYMENTS
# =============================================================================

class Payment(Base):
    """Payment against an invoice."""
    
    __tablename__ = "payments"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[Optional[str]] = mapped_column(String(50))  # cash, card, bank_transfer, stripe
    reference: Mapped[Optional[str]] = mapped_column(String(255))
    
    # Stripe
    stripe_payment_intent_id: Mapped[Optional[str]] = mapped_column(String(255))
    stripe_checkout_session_id: Mapped[Optional[str]] = mapped_column(String(255))

    # Xero integration
    xero_payment_id: Mapped[Optional[str]] = mapped_column(String(255))

    payment_date: Mapped[Optional[date]] = mapped_column(Date)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)
    
    # Relationships
    invoice: Mapped["Invoice"] = relationship(back_populates="payments")

    __table_args__ = (
        Index('idx_payment_invoice', 'invoice_id'),
        Index('idx_payment_date', 'payment_date'),
    )


# =============================================================================
# NOTIFICATIONS
# =============================================================================

class Notification(Base):
    """In-app notification for the admin."""
    
    __tablename__ = "notifications"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text)
    
    # Links
    customer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("customers.id"))
    quote_id: Mapped[Optional[int]] = mapped_column(ForeignKey("quotes.id"))
    invoice_id: Mapped[Optional[int]] = mapped_column(ForeignKey("invoices.id"))
    
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[str] = mapped_column(String(20), default="normal")  # low, normal, high, critical
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)
    
    __table_args__ = (
        Index('idx_notification_read', 'is_read'),
        Index('idx_notification_created', 'created_at'),
        Index('idx_notification_customer', 'customer_id'),
        Index('idx_notification_quote', 'quote_id'),
        Index('idx_notification_invoice', 'invoice_id'),
    )


# =============================================================================
# ACTIVITY LOG
# =============================================================================

class ActivityLog(Base):
    """Audit trail of all actions."""
    
    __tablename__ = "activity_log"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    
    entity_type: Mapped[Optional[str]] = mapped_column(String(50))  # customer, quote, invoice
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    
    ip_address: Mapped[Optional[str]] = mapped_column(String(50))
    user_agent: Mapped[Optional[str]] = mapped_column(String(500))
    extra_data: Mapped[Optional[dict]] = mapped_column(JSON)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)
    
    __table_args__ = (
        Index('idx_activity_entity', 'entity_type', 'entity_id'),
        Index('idx_activity_created', 'created_at'),
        Index('idx_activity_action', 'action'),
    )


# =============================================================================
# COMMUNICATION LOG (Unified)
# =============================================================================

class CommunicationLog(Base):
    """Unified log of all communications (email, SMS, phone calls, notes)."""

    __tablename__ = "communication_log"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Channel: email, sms, phone_call, note
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    # Direction: outbound, inbound
    direction: Mapped[str] = mapped_column(String(20), default="outbound")

    # Links
    customer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("customers.id"))
    quote_id: Mapped[Optional[int]] = mapped_column(ForeignKey("quotes.id"))
    invoice_id: Mapped[Optional[int]] = mapped_column(ForeignKey("invoices.id"))

    # Contact info
    to_address: Mapped[Optional[str]] = mapped_column(String(255))
    to_phone: Mapped[Optional[str]] = mapped_column(String(50))
    subject: Mapped[Optional[str]] = mapped_column(String(255))
    body: Mapped[Optional[str]] = mapped_column(Text)
    template: Mapped[Optional[str]] = mapped_column(String(100))

    # Provider tracking
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), default="sent")

    # Timestamps
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    clicked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Inbound SMS fields
    from_phone: Mapped[Optional[str]] = mapped_column(String(50))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)

    __table_args__ = (
        Index('idx_comm_customer', 'customer_id'),
        Index('idx_comm_quote', 'quote_id'),
        Index('idx_comm_invoice', 'invoice_id'),
        Index('idx_comm_channel', 'channel'),
        Index('idx_comm_created', 'created_at'),
    )


# =============================================================================
# EMAIL & SMS LOGS (Legacy — kept for backward compatibility)
# =============================================================================

class EmailLog(Base):
    """Log of all sent emails with tracking."""
    
    __tablename__ = "email_log"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    
    to_address: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[Optional[str]] = mapped_column(String(255))
    template: Mapped[Optional[str]] = mapped_column(String(100))
    
    # Links
    quote_id: Mapped[Optional[int]] = mapped_column(ForeignKey("quotes.id"))
    invoice_id: Mapped[Optional[int]] = mapped_column(ForeignKey("invoices.id"))
    
    # Tracking
    postmark_message_id: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), default="sent")  # sent, delivered, opened, clicked, bounced
    
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    clicked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)


class SMSLog(Base):
    """Log of all sent SMS messages."""
    
    __tablename__ = "sms_log"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    
    to_phone: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text)
    
    # Links
    quote_id: Mapped[Optional[int]] = mapped_column(ForeignKey("quotes.id"))
    invoice_id: Mapped[Optional[int]] = mapped_column(ForeignKey("invoices.id"))
    
    # Tracking
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), default="sent")
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)


# =============================================================================
# JOB COSTING
# =============================================================================

class JobCosting(Base):
    """Post-job costing for profitability analysis."""

    __tablename__ = "job_costings"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), unique=True, nullable=False)

    # Quoted amounts (snapshot from quote)
    quoted_total_cents: Mapped[int] = mapped_column(Integer, default=0)

    # Actual costs
    actual_concrete_cents: Mapped[int] = mapped_column(Integer, default=0)
    actual_concrete_m3: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2))
    actual_labour_cents: Mapped[int] = mapped_column(Integer, default=0)
    actual_labour_hours: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    actual_materials_cents: Mapped[int] = mapped_column(Integer, default=0)
    actual_pump_cents: Mapped[int] = mapped_column(Integer, default=0)
    actual_other_cents: Mapped[int] = mapped_column(Integer, default=0)
    other_description: Mapped[Optional[str]] = mapped_column(Text)

    # Calculated
    actual_total_cents: Mapped[int] = mapped_column(Integer, default=0)
    profit_cents: Mapped[int] = mapped_column(Integer, default=0)
    margin_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))

    # Notes
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now, onupdate=sydney_now)

    # Relationships
    quote: Mapped["Quote"] = relationship()

    __table_args__ = (
        Index('idx_job_costing_quote', 'quote_id'),
    )


# =============================================================================
# SETTINGS
# =============================================================================

class Setting(Base):
    """Key-value settings store with categories."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)  # 'pricing', 'business', 'sms', etc.
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Optional[str]] = mapped_column(Text)  # JSON string for complex values
    value_type: Mapped[str] = mapped_column(String(20), default='string')  # 'string', 'int', 'float', 'bool', 'json'
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now, onupdate=sydney_now)

    __table_args__ = (
        UniqueConstraint('category', 'key', name='uq_setting_category_key'),
        Index('idx_setting_category', 'category'),
    )


class Sequence(Base):
    """Sequence numbers for quote/invoice numbering."""
    
    __tablename__ = "sequences"
    
    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    current_value: Mapped[int] = mapped_column(Integer, default=0)


# =============================================================================
# WORKERS & TIME TRACKING
# =============================================================================

class Worker(Base):
    """Crew member."""

    __tablename__ = "workers"

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="labourer")  # finisher, experienced_labourer, labourer
    hourly_rate_cents: Mapped[int] = mapped_column(Integer, default=0)
    cost_rate_cents: Mapped[int] = mapped_column(Integer, default=0)  # Actual gross wage cost
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # PAYG / payroll fields
    claims_tax_free_threshold: Mapped[bool] = mapped_column(Boolean, default=True)
    pay_frequency: Mapped[str] = mapped_column(String(20), default="weekly")  # weekly, fortnightly, monthly

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now, onupdate=sydney_now)

    # Relationships
    job_assignments: Mapped[List["JobAssignment"]] = relationship(back_populates="worker")


class JobAssignment(Base):
    """Assignment of worker to a job/quote."""

    __tablename__ = "job_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)

    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), nullable=False)
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id"), nullable=False)

    role: Mapped[Optional[str]] = mapped_column(String(50))  # Role for this specific job
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)

    # Relationships
    quote: Mapped["Quote"] = relationship(back_populates="job_assignments")
    worker: Mapped["Worker"] = relationship(back_populates="job_assignments")

    __table_args__ = (
        UniqueConstraint('quote_id', 'worker_id', name='uq_job_assignment_quote_worker'),
        Index('idx_job_assignment_quote', 'quote_id'),
        Index('idx_job_assignment_worker', 'worker_id'),
    )


class TimeEntry(Base):
    """Time logged against a job."""
    
    __tablename__ = "time_entries"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), nullable=False)
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id"), nullable=False)
    
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    hours: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    stage: Mapped[Optional[str]] = mapped_column(String(50))  # setup, pour, finish
    notes: Mapped[Optional[str]] = mapped_column(Text)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)
    
    # Relationships
    quote: Mapped["Quote"] = relationship(back_populates="time_entries")
    worker: Mapped["Worker"] = relationship()

    __table_args__ = (
        Index('idx_time_entry_quote', 'quote_id'),
        Index('idx_time_entry_worker', 'worker_id'),
        Index('idx_time_entry_date', 'work_date'),
    )


# =============================================================================
# PHOTOS
# =============================================================================

class Photo(Base):
    """Job photos."""
    
    __tablename__ = "photos"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), nullable=False)
    
    category: Mapped[str] = mapped_column(String(50), default="general")  # before, during, after, issue
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_url: Mapped[str] = mapped_column(Text, nullable=False)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text)
    caption: Mapped[Optional[str]] = mapped_column(Text)

    # GPS coordinates (captured from device)
    gps_lat: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7))
    gps_lng: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7))

    taken_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    shared_with_customer: Mapped[bool] = mapped_column(Boolean, default=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)
    
    # Relationships
    quote: Mapped["Quote"] = relationship(back_populates="photos")

    __table_args__ = (
        Index('idx_photo_quote', 'quote_id'),
        Index('idx_photo_category', 'category'),
    )


# =============================================================================
# POUR PLANNER
# =============================================================================

class PourPlan(Base):
    """Pour plan linked to a job - captures weather snapshot and recommendations."""

    __tablename__ = "pour_plans"

    id: Mapped[int] = mapped_column(primary_key=True)

    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), unique=True, nullable=False)

    # Planned timing
    planned_date: Mapped[date] = mapped_column(Date, nullable=False)
    planned_start_time: Mapped[Optional[time]] = mapped_column(Time)

    # Weather snapshot at planning time
    weather_snapshot: Mapped[Optional[dict]] = mapped_column(JSON)

    # Calculated values
    evaporation_rate: Mapped[Optional[float]] = mapped_column(Float)
    risk_level: Mapped[Optional[str]] = mapped_column(String(20))  # low, moderate, high, very_high, critical

    # Recommendations from service
    recommendations: Mapped[Optional[list]] = mapped_column(JSON)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now, onupdate=sydney_now)

    # Relationships
    quote: Mapped["Quote"] = relationship()

    __table_args__ = (
        Index('idx_pour_plan_quote', 'quote_id'),
    )


class PourResult(Base):
    """Log pour predictions vs actuals for calibration."""

    __tablename__ = "pour_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    pour_plan_id: Mapped[int] = mapped_column(ForeignKey("pour_plans.id"), nullable=False)

    # Predictions (captured at planning time)
    predicted_initial_set_hours: Mapped[Optional[float]] = mapped_column(Float)
    predicted_finish_window_start: Mapped[Optional[str]] = mapped_column(String(20))  # "10:30 AM"
    predicted_finish_window_end: Mapped[Optional[str]] = mapped_column(String(20))    # "12:30 PM"
    recommended_admixture: Mapped[Optional[str]] = mapped_column(String(100))  # "Sika Retarder N"
    recommended_dose_ml: Mapped[Optional[int]] = mapped_column(Integer)

    # Actuals (logged post-pour)
    actual_admixture_used: Mapped[Optional[str]] = mapped_column(String(100))
    actual_dose_ml: Mapped[Optional[int]] = mapped_column(Integer)
    actual_initial_set_hours: Mapped[Optional[float]] = mapped_column(Float)
    actual_finish_time: Mapped[Optional[str]] = mapped_column(String(20))  # "11:00 AM"
    actual_conditions_notes: Mapped[Optional[str]] = mapped_column(Text)

    # Assessment
    prediction_accuracy: Mapped[Optional[str]] = mapped_column(String(20))  # "spot_on", "close", "way_off"

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)

    # Relationships
    pour_plan: Mapped["PourPlan"] = relationship()

    __table_args__ = (
        Index('idx_pour_result_plan', 'pour_plan_id'),
    )


# =============================================================================
# WEBHOOKS & OAUTH
# =============================================================================

class WebhookEvent(Base):
    """Track processed webhooks for idempotency."""
    
    __tablename__ = "webhook_events"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[Optional[str]] = mapped_column(String(100))
    
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)
    
    __table_args__ = (
        UniqueConstraint('provider', 'event_id', name='uq_webhook_event'),
    )


class OAuthToken(Base):
    """OAuth tokens for integrations."""
    
    __tablename__ = "oauth_tokens"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    
    provider: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    access_token: Mapped[Optional[str]] = mapped_column(Text)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    extra_data: Mapped[Optional[dict]] = mapped_column(JSON)
    
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now, onupdate=sydney_now)


# =============================================================================
# FOLLOW-UPS
# =============================================================================

class FollowUp(Base):
    """Follow-up reminders for quotes."""
    
    __tablename__ = "follow_ups"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), nullable=False)
    
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # call, email, sms
    notes: Mapped[Optional[str]] = mapped_column(Text)
    
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)

    __table_args__ = (
        Index('idx_followup_quote', 'quote_id'),
        Index('idx_followup_due_date', 'due_date'),
        Index('idx_followup_completed', 'completed'),
    )


# =============================================================================
# PROGRESS UPDATES
# =============================================================================

class ProgressUpdate(Base):
    """Progress updates sent to customers during a job."""

    __tablename__ = "progress_updates"

    id: Mapped[int] = mapped_column(primary_key=True)

    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    photo_ids: Mapped[Optional[list]] = mapped_column(JSON)  # List of Photo IDs

    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)

    __table_args__ = (
        Index('idx_progress_update_quote', 'quote_id'),
        Index('idx_progress_update_customer', 'customer_id'),
    )


# =============================================================================
# AUTOMATED REMINDERS
# =============================================================================

class Reminder(Base):
    """Automated reminders for payments and jobs."""

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Reminder type: payment_due, payment_overdue, job_tomorrow, job_week
    reminder_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Entity reference
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)  # invoice, quote
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Scheduling
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Status tracking
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)

    __table_args__ = (
        Index('idx_reminder_scheduled', 'scheduled_for'),
        Index('idx_reminder_entity', 'entity_type', 'entity_id'),
        Index('idx_reminder_type', 'reminder_type'),
    )


# =============================================================================
# EXPENSES
# =============================================================================

class Expense(Base):
    """Business expense with optional receipt and job link."""

    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    expense_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)

    # Categorization
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    vendor: Mapped[Optional[str]] = mapped_column(String(255))

    # Amount (cents, ex GST)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    gst_cents: Mapped[int] = mapped_column(Integer, default=0)
    gst_free: Mapped[bool] = mapped_column(Boolean, default=False)  # True for GST-free purchases

    @property
    def total_cents(self) -> int:
        """Total including GST."""
        return self.amount_cents + self.gst_cents

    # Date
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Receipt
    receipt_photo_id: Mapped[Optional[int]] = mapped_column(ForeignKey("photos.id"))
    receipt_url: Mapped[Optional[str]] = mapped_column(Text)

    # Link to job (optional)
    quote_id: Mapped[Optional[int]] = mapped_column(ForeignKey("quotes.id"))

    # Payment method
    payment_method: Mapped[str] = mapped_column(String(50), default="card")

    # Xero sync
    xero_bill_id: Mapped[Optional[str]] = mapped_column(String(100))
    synced_to_xero_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    xero_sync_error: Mapped[Optional[str]] = mapped_column(Text)  # Last sync error message

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now, onupdate=sydney_now)

    __table_args__ = (
        Index('idx_expense_date', 'expense_date'),
        Index('idx_expense_category', 'category'),
        Index('idx_expense_quote', 'quote_id'),
    )


# =============================================================================
# SUPPLIERS
# =============================================================================

class Supplier(Base):
    """
    Supplier / vendor contact book.

    Stores contact details for concrete plants, pump companies, steel suppliers,
    subcontractors, tool shops, etc. Useful for quick reference, TPAR reporting,
    and linking to expenses.
    """

    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Basic info
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    contact_person: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    website: Mapped[Optional[str]] = mapped_column(String(500))

    # Address
    address: Mapped[Optional[str]] = mapped_column(Text)

    # Business details
    abn: Mapped[Optional[str]] = mapped_column(String(20))  # Australian Business Number
    account_number: Mapped[Optional[str]] = mapped_column(String(100))  # Your account # with them

    # Category (concrete_plant, pump_hire, steel_supplier, subcontractor, equipment_hire, other)
    category: Mapped[str] = mapped_column(String(50), default="other")

    # Notes
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # Active flag
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now, onupdate=sydney_now)

    __table_args__ = (
        Index('idx_supplier_category', 'category'),
        Index('idx_supplier_active', 'is_active'),
    )


# =============================================================================
# EARNINGS SNAPSHOTS
# =============================================================================

class EarningsSnapshot(Base):
    """Weekly/monthly snapshot of owner's draw (take-home earnings) for historical tracking."""

    __tablename__ = "earnings_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Period identification
    period_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "weekly" or "monthly"
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    # Revenue breakdown (all in cents)
    revenue_cents: Mapped[int] = mapped_column(Integer, default=0)  # Revenue ex GST for backwards compat
    revenue_inc_gst_cents: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    gst_collected_cents: Mapped[Optional[int]] = mapped_column(Integer, default=0)

    # Cost breakdown (all in cents)
    materials_cents: Mapped[int] = mapped_column(Integer, default=0)
    labour_cents: Mapped[int] = mapped_column(Integer, default=0)  # Total labour for backwards compat
    labour_wages_cents: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    labour_super_cents: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    labour_payg_cents: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    expenses_cents: Mapped[int] = mapped_column(Integer, default=0)

    # Final take-home
    take_home_cents: Mapped[int] = mapped_column(Integer, default=0)

    # Stats
    jobs_completed: Mapped[int] = mapped_column(Integer, default=0)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)

    __table_args__ = (
        UniqueConstraint('period_type', 'period_start', name='uq_earnings_snapshot_period'),
        Index('idx_earnings_period_type', 'period_type'),
        Index('idx_earnings_period_start', 'period_start'),
    )


# =============================================================================
# XERO ACCOUNT MAPPING
# =============================================================================

class XeroAccountMapping(Base):
    """
    Maps ConcreteIQ expense categories to Xero account codes.

    Used when syncing expenses to Xero so each category goes to the correct
    Xero chart-of-accounts code (e.g. Materials → 300, Fuel → 310).
    """

    __tablename__ = "xero_account_mappings"

    id: Mapped[int] = mapped_column(primary_key=True)

    # ConcreteIQ category (must match EXPENSE_CATEGORIES keys)
    category: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)

    # Xero account code (e.g. "300", "310", "400")
    xero_account_code: Mapped[str] = mapped_column(String(20), nullable=False)

    # Human-readable Xero account name (e.g. "Materials Purchased")
    xero_account_name: Mapped[Optional[str]] = mapped_column(String(255))

    # Xero tax type for this category (e.g. "INPUT" for GST on purchases, "NONE" for GST-free)
    xero_tax_type: Mapped[str] = mapped_column(String(50), default="INPUT")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=sydney_now, onupdate=sydney_now)
