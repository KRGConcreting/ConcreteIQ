"""
Pydantic Schemas — Request/Response validation.

These schemas ENFORCE the API contract.
Claude Code cannot deviate from these structures.
"""

from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Any
from pydantic import BaseModel, Field, EmailStr, ConfigDict


# =============================================================================
# BASE
# =============================================================================

class BaseSchema(BaseModel):
    """Base schema with common config."""
    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# CUSTOMER SCHEMAS
# =============================================================================

class CustomerCreate(BaseModel):
    """Create a new customer."""
    name: str = Field(..., min_length=1, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    phone2: Optional[str] = Field(None, max_length=50)
    email: Optional[EmailStr] = None
    
    street: Optional[str] = Field(None, max_length=255)
    unit: Optional[str] = Field(None, max_length=50)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=20)
    postcode: Optional[str] = Field(None, max_length=10)
    
    business_name: Optional[str] = Field(None, max_length=255)
    contact_position: Optional[str] = Field(None, max_length=100)
    customer_type: str = Field(default="residential", pattern="^(residential|commercial|council)$")
    
    notify_email: bool = True
    notify_sms: bool = True
    
    source: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None
    
    discount_percent: Decimal = Field(default=0, ge=0, le=100)


class CustomerUpdate(BaseModel):
    """Update an existing customer."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    phone2: Optional[str] = Field(None, max_length=50)
    email: Optional[EmailStr] = None
    
    street: Optional[str] = Field(None, max_length=255)
    unit: Optional[str] = Field(None, max_length=50)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=20)
    postcode: Optional[str] = Field(None, max_length=10)
    
    business_name: Optional[str] = Field(None, max_length=255)
    contact_position: Optional[str] = Field(None, max_length=100)
    customer_type: Optional[str] = Field(None, pattern="^(residential|commercial|council)$")
    
    notify_email: Optional[bool] = None
    notify_sms: Optional[bool] = None
    
    source: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None
    
    discount_percent: Optional[Decimal] = Field(None, ge=0, le=100)


class CustomerResponse(BaseSchema):
    """Customer response."""
    id: int
    name: str
    phone: Optional[str]
    phone2: Optional[str]
    email: Optional[str]
    
    street: Optional[str]
    unit: Optional[str]
    city: Optional[str]
    state: Optional[str]
    postcode: Optional[str]
    
    business_name: Optional[str]
    contact_position: Optional[str]
    customer_type: str
    
    notify_email: bool
    notify_sms: bool
    
    source: Optional[str]
    notes: Optional[str]
    
    discount_percent: Decimal
    
    created_at: datetime
    updated_at: datetime
    
    # Computed
    full_address: Optional[str] = None
    quote_count: int = 0
    total_value_cents: int = 0


# =============================================================================
# CALCULATOR SCHEMAS
# =============================================================================

class CalculatorInput(BaseModel):
    """
    Calculator input - mirrors the calculator dataclass fields.
    Extra fields are allowed and passed through to the calculator.
    """
    model_config = {"extra": "allow"}

    # Basic measurements (required)
    slab_area: float = Field(..., gt=0, description="Slab area in square metres")
    slab_thickness: float = Field(default=100, gt=0, description="Slab thickness in mm")

    # Formwork & joints
    perimeter: float = Field(default=0, ge=0)
    edge_formwork: float = Field(default=0, ge=0)
    internal_formwork: float = Field(default=0, ge=0)
    control_joints: float = Field(default=0, ge=0)
    isolation_joints: float = Field(default=0, ge=0)
    dowel_bars: float = Field(default=0, ge=0)
    fence_sheeting: float = Field(default=0, ge=0)
    steps: int = Field(default=0, ge=0)

    # Subbase
    subbase_thickness: float = Field(default=0, ge=0)
    compaction: bool = False
    delivery_distance_km: float = Field(default=0, ge=0)

    # Excavation
    excavation: bool = False
    excavation_depth: float = Field(default=0, ge=0)
    dig_method: str = Field(default="hand")  # hand or machine
    excavation_disposal: str = Field(default="none")  # none, skipbin, trailer
    waste_tip_destination: str = Field(default="jacksons")
    soil_type: str = Field(default="soil")  # topsoil, soil, clay, rock

    # Equipment hire
    pressure_washer: bool = False
    pressure_washer_duration: str = Field(default="half")

    # Concrete removal
    concrete_removal: bool = False
    removal_area: float = Field(default=0, ge=0)
    removal_thickness: float = Field(default=100, ge=0)
    removal_method: str = Field(default="manual")  # manual, machine
    removal_reinforced: bool = False
    removal_disposal: str = Field(default="skip_bin")  # skip_bin, trailer
    removal_skip_bin_cost: int = Field(default=40000, ge=0)
    removal_trailer_tip_fee: int = Field(default=5500, ge=0)

    # Specification
    concrete_grade: str = Field(default="N32")
    concrete_finish: str = Field(default="Broom")
    mix_additive: str = Field(default="None")
    concrete_fibre: str = Field(default="None")
    coloured_concrete: bool = False
    concrete_colour: str = Field(default="")
    concrete_volume_override: float = Field(default=0, ge=0)
    reinforcement: str = Field(default="GFRP 450mm")
    control_joint_method: str = Field(default="Sawcut")
    control_joint_rate: int = Field(default=0, ge=0)
    pump_required: bool = False
    placement_method: str = Field(default="Chute")
    season: str = Field(default="Summer")
    falls_complexity_pct: float = Field(default=0, ge=0)
    fall_type: str = Field(default="none")
    fall_pit_count: int = Field(default=0, ge=0)

    # Rebates
    rebates: float = Field(default=0, ge=0)

    # Pier holes
    pier_holes: int = Field(default=0, ge=0)
    pier_diameter: float = Field(default=300, ge=0)
    pier_depth: float = Field(default=600, ge=0)
    pier_starters: int = Field(default=4, ge=0)

    # Edge beams
    edge_beams: bool = False
    edge_beam_length: float = Field(default=0, ge=0)
    edge_beam_depth: float = Field(default=200, ge=0)
    edge_beam_width: float = Field(default=300, ge=0)

    # Complexity & pricing
    complexity: str = Field(default="Standard")
    tier: str = Field(default="Standard")
    team_tier: str = Field(default="Standard")
    distance_km: float = Field(default=0, ge=0)
    concrete_distance_km: float = Field(default=0, ge=0)
    setup_crew_count: int = Field(default=3, ge=0)

    # Inclusions
    inc_release_agent: bool = True
    inc_evap_retarder: bool = True
    inc_moisture_barrier: bool = True
    inc_formwork_wear: bool = True
    inc_durability_enhancer: bool = False
    inc_surface_retarder: bool = False
    inc_curing_compound: bool = False
    inc_sealer: bool = False

    # Exposed aggregate
    wash_off: str = Field(default="N/A")
    acid_wash: bool = False

    # Hourly rates (cents)
    setup_hourly_rate: int = Field(default=0, ge=0)
    pour_hourly_rate: int = Field(default=0, ge=0)
    setup_cost_rate: int = Field(default=0, ge=0)
    pour_cost_rate: int = Field(default=0, ge=0)

    # Custom labour
    setup_day_custom: bool = False
    setup_day_custom_rate: float = Field(default=0, ge=0)
    setup_day_custom_workers: int = Field(default=1, ge=1)
    pour_day_custom: bool = False
    pour_day_custom_rate: float = Field(default=0, ge=0)
    pour_day_custom_workers: int = Field(default=1, ge=1)

    # Plumbing & Drainage
    drainage: bool = False
    plumber_hours: float = Field(default=0, ge=0)
    plumber_rate: int = Field(default=9500, ge=0)
    plumber_materials_cents: int = Field(default=0, ge=0)
    plumber_description: str = Field(default="")

    # Customer discount
    customer_discount_percent: float = Field(default=0, ge=0, le=100)


class CalculatorLineItem(BaseModel):
    """A single line item in the quote."""
    description: str
    quantity: Decimal
    unit: str
    unit_price_cents: int
    total_cents: int
    category: str  # concrete, labour, materials, etc.


class BankSplit(BaseModel):
    """Payment split allocation."""
    booking_cents: int = Field(..., description="30% progress payment")
    prepour_cents: int = Field(..., description="60% pre-pour payment")
    completion_cents: int = Field(..., description="10% completion payment")


class CalculatorResult(BaseModel):
    """
    Calculator output - complete breakdown.
    """
    # Volume calculations
    volume_m3: Decimal
    volume_with_wastage_m3: Decimal
    wastage_percent: Decimal
    
    # Line items
    line_items: List[CalculatorLineItem]
    
    # Totals (ALL IN CENTS)
    concrete_cents: int
    labour_cents: int
    materials_cents: int
    excavation_cents: int
    removal_cents: int
    pump_cents: int
    extras_cents: int
    distance_cents: int
    
    subtotal_cents: int
    discount_cents: int
    subtotal_after_discount_cents: int
    gst_cents: int
    total_cents: int
    
    # Payment split
    bank_split: BankSplit
    
    # Metadata
    calculated_at: datetime
    calculator_version: str = "2.0"


# =============================================================================
# QUOTE SCHEMAS
# =============================================================================

class QuoteCreate(BaseModel):
    """Create a new quote (calculator type)."""
    customer_id: int
    job_name: Optional[str] = Field(None, max_length=255)
    job_type: Optional[str] = Field(None, max_length=50)
    job_address: Optional[str] = None
    quote_type: str = Field(default="calculator")

    calculator_input: Optional[CalculatorInput] = None

    notes: Optional[str] = None
    internal_notes: Optional[str] = None


class LabourQuoteCreate(BaseModel):
    """Create a labour invoice quote for subcontractor/day work."""
    customer_id: int
    job_name: Optional[str] = Field(None, max_length=255)
    job_address: Optional[str] = None
    work_date: date
    worker_name: str = Field(..., min_length=1, max_length=255)
    hours: float = Field(..., gt=0)
    team_tier: str = Field(default="Standard")
    hourly_rate_cents: Optional[int] = None  # Override if not using team_tier
    notes: Optional[str] = None
    internal_notes: Optional[str] = None


class CustomQuoteLineItem(BaseModel):
    """A single line item in a custom/freeform quote."""
    description: str = Field(..., min_length=1)
    category: str = Field(default="Service")
    quantity: float = Field(default=1, gt=0)
    unit: str = Field(default="ea")
    unit_price_cents: int = Field(..., ge=0)
    taxable: bool = Field(default=True)


class CustomQuoteCreate(BaseModel):
    """Create a custom/freeform quote with manual line items."""
    customer_id: int
    job_name: Optional[str] = Field(None, max_length=255)
    job_type: Optional[str] = Field(None, max_length=50)
    job_address: Optional[str] = None
    line_items: List[CustomQuoteLineItem] = Field(..., min_length=1)
    notes: Optional[str] = None
    internal_notes: Optional[str] = None


class QuoteUpdate(BaseModel):
    """Update a quote (draft only)."""
    customer_id: Optional[int] = None
    job_name: Optional[str] = Field(None, max_length=255)
    job_type: Optional[str] = Field(None, max_length=50)
    job_address: Optional[str] = None

    calculator_input: Optional[CalculatorInput] = None
    
    notes: Optional[str] = None
    internal_notes: Optional[str] = None
    
    expiry_date: Optional[date] = None


class QuoteResponse(BaseSchema):
    """Quote response."""
    id: int
    quote_number: str
    customer_id: int
    quote_type: Optional[str] = "calculator"

    job_name: Optional[str]
    job_type: Optional[str]
    job_address: Optional[str]
    distance_km: Optional[Decimal]
    
    calculator_input: Optional[dict]
    calculator_result: Optional[dict]
    line_items: Optional[list]
    customer_line_items: Optional[list] = None

    subtotal_cents: int
    discount_cents: int
    gst_cents: int
    total_cents: int
    
    status: str
    
    quote_date: Optional[date]
    expiry_date: Optional[date]
    sent_at: Optional[datetime]
    viewed_at: Optional[datetime]
    accepted_at: Optional[datetime]
    declined_at: Optional[datetime]
    decline_reason: Optional[str]

    requested_start_date: Optional[date]
    confirmed_start_date: Optional[date]

    signature_name: Optional[str]
    signed_at: Optional[datetime]
    
    notes: Optional[str]
    internal_notes: Optional[str]
    
    portal_token: str
    portal_url: Optional[str] = None
    
    created_at: datetime
    updated_at: datetime
    
    # Related
    customer: Optional[CustomerResponse] = None


class QuoteSendRequest(BaseModel):
    """Request to send a quote."""
    send_email: bool = True
    send_sms: bool = False
    email_message: Optional[str] = None
    sms_message: Optional[str] = None


class QuoteAcceptRequest(BaseModel):
    """Customer accepting a quote."""
    signer_name: str = Field(..., min_length=1, max_length=255)
    signature_data: str = Field(..., min_length=1, description="Base64 PNG signature or 'typed:Name'")
    signature_type: str = Field(default="draw", description="'draw' or 'type'")
    terms_accepted: bool = Field(..., description="Must be true")


class QuoteDateSelectRequest(BaseModel):
    """Customer selecting start date."""
    requested_date: date = Field(..., description="Requested start date")


class QuoteDeclineRequest(BaseModel):
    """Customer declining a quote."""
    reason: Optional[str] = Field(None, max_length=1000, description="Optional decline reason")


class ConfirmBookingRequest(BaseModel):
    """Admin confirming a booking date."""
    confirmed_date: date = Field(..., description="The confirmed start date for the job")


class CustomerLineItemSubItem(BaseModel):
    """A sub-item within a customer-facing line item group."""
    description: str
    price_cents: Optional[int] = None


class CustomerLineItem(BaseModel):
    """A single customer-facing line item group."""
    id: str
    category: str
    sub_items: list[CustomerLineItemSubItem]
    total_cents: int  # Can be negative for discount line items
    show_sub_prices: bool = False
    sort_order: int = 0


class QuotePreviewUpdate(BaseModel):
    """Update customer-facing line items from preview page."""
    customer_line_items: list[CustomerLineItem]
    notes: Optional[str] = None


# =============================================================================
# QUOTE AMENDMENT SCHEMAS
# =============================================================================

class AmendmentCreate(BaseModel):
    """Create a quote amendment/variation."""
    quote_id: int
    description: str = Field(..., min_length=1)
    amount_cents: int  # Can be negative for credits


class AmendmentUpdate(BaseModel):
    """Update amendment (draft only)."""
    description: Optional[str] = Field(None, min_length=1)
    amount_cents: Optional[int] = None


class AmendmentResponse(BaseSchema):
    """Amendment response."""
    id: int
    quote_id: int
    amendment_number: int
    description: str
    amount_cents: int
    status: str
    portal_token: Optional[str] = None
    sent_at: Optional[datetime] = None
    accepted_at: Optional[datetime] = None
    declined_at: Optional[datetime] = None
    decline_reason: Optional[str] = None
    signature_name: Optional[str] = None
    created_at: Optional[datetime] = None


class AmendmentAccept(BaseModel):
    """Accept amendment via portal."""
    signature_data: Optional[str] = None
    signature_name: Optional[str] = None


class AmendmentDecline(BaseModel):
    """Decline amendment via portal."""
    reason: Optional[str] = Field(None, max_length=1000)


# =============================================================================
# INVOICE SCHEMAS
# =============================================================================

class InvoiceCreate(BaseModel):
    """Create a new invoice."""
    customer_id: int
    quote_id: Optional[int] = None
    
    description: Optional[str] = Field(None, max_length=255)
    stage: Optional[str] = Field(None, pattern="^(progress|booking|prepour|completion|variation|manual|deposit|final)$")
    
    line_items: Optional[list] = None
    
    subtotal_cents: int = Field(..., gt=0)
    
    issue_date: Optional[date] = None
    due_date: Optional[date] = None
    
    notes: Optional[str] = None


class InvoiceResponse(BaseSchema):
    """Invoice response."""
    id: int
    invoice_number: str
    quote_id: Optional[int]
    customer_id: int
    
    description: Optional[str]
    stage: Optional[str]
    line_items: Optional[list]
    
    subtotal_cents: int
    gst_cents: int
    total_cents: int
    paid_cents: int
    balance_cents: int = 0
    
    status: str
    
    issue_date: Optional[date]
    due_date: Optional[date]
    paid_date: Optional[date]
    
    portal_token: str
    portal_url: Optional[str] = None
    
    xero_invoice_id: Optional[str]
    
    notes: Optional[str]
    
    created_at: datetime
    updated_at: datetime
    
    # Related
    customer: Optional[CustomerResponse] = None


# =============================================================================
# PAYMENT SCHEMAS
# =============================================================================

class PaymentCreate(BaseModel):
    """Record a payment."""
    invoice_id: int
    amount_cents: int = Field(..., gt=0)
    method: str = Field(..., pattern="^(cash|card|bank_transfer|stripe)$")
    reference: Optional[str] = Field(None, max_length=255)
    payment_date: Optional[date] = None
    notes: Optional[str] = None


class PaymentResponse(BaseSchema):
    """Payment response."""
    id: int
    invoice_id: int
    amount_cents: int
    method: Optional[str]
    reference: Optional[str]
    payment_date: Optional[date]
    notes: Optional[str]
    created_at: datetime


class StripeCheckoutRequest(BaseModel):
    """Create Stripe checkout session."""
    invoice_id: int
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class StripeCheckoutResponse(BaseModel):
    """Stripe checkout session response."""
    checkout_url: str
    session_id: str


# =============================================================================
# NOTIFICATION SCHEMAS
# =============================================================================

class NotificationResponse(BaseSchema):
    """Notification response."""
    id: int
    type: str
    title: str
    message: Optional[str]
    
    customer_id: Optional[int]
    quote_id: Optional[int]
    invoice_id: Optional[int]
    
    is_read: bool
    priority: str
    
    created_at: datetime
    
    # For display
    icon: str = "📌"
    link: Optional[str] = None


# =============================================================================
# DASHBOARD SCHEMAS
# =============================================================================

class DashboardStats(BaseModel):
    """Dashboard statistics."""
    quotes_draft: int = 0
    quotes_sent: int = 0
    quotes_accepted_this_month: int = 0
    
    invoices_unpaid: int = 0
    invoices_overdue: int = 0
    
    revenue_this_month_cents: int = 0
    revenue_last_month_cents: int = 0
    outstanding_cents: int = 0
    
    conversion_rate_percent: Decimal = 0


class UpcomingJob(BaseModel):
    """Upcoming job for dashboard."""
    quote_id: int
    quote_number: str
    customer_name: str
    job_name: Optional[str]
    scheduled_date: date
    total_cents: int


class RecentActivity(BaseModel):
    """Recent activity item."""
    id: int
    action: str
    description: str
    entity_type: Optional[str]
    entity_id: Optional[int]
    created_at: datetime


class DashboardResponse(BaseModel):
    """Complete dashboard data."""
    stats: DashboardStats
    notifications: List[NotificationResponse]
    upcoming_jobs: List[UpcomingJob]
    recent_activity: List[RecentActivity]


# =============================================================================
# POUR PLANNER SCHEMAS
# =============================================================================

class WeatherData(BaseModel):
    """Weather conditions."""
    temperature: Decimal
    humidity: int
    wind_speed: Decimal
    rain_probability: int
    uv_index: int
    conditions: str


class PourCalculatorInput(BaseModel):
    """Pour planner calculation input."""
    pour_date: date
    pour_time: str = Field(default="07:00", pattern="^[0-9]{2}:[0-9]{2}$")
    
    concrete_grade: str = "N25"
    slump_ordered: int = Field(default=100, ge=50, le=200)
    
    travel_time_minutes: int = Field(default=30, ge=0, le=180)
    
    # Optional overrides
    air_temp_override: Optional[Decimal] = None
    humidity_override: Optional[int] = None
    wind_speed_override: Optional[Decimal] = None


class PourCalculatorResult(BaseModel):
    """Pour planner calculation result."""
    # Weather
    weather: WeatherData
    
    # Evaporation
    evaporation_rate: Decimal  # kg/m²/hr
    evaporation_risk: str  # low, moderate, high, extreme
    
    # Setting times
    initial_set_minutes: int
    final_set_minutes: int
    
    # Slump
    predicted_arrival_slump: int
    slump_loss: int
    
    # Recommendations
    order_slump: int
    
    # Advisory
    pour_advisory: str  # good, caution, not_recommended
    advisory_reasons: List[str]
    
    # Actions
    recommended_actions: List[str]
    
    # Concrete temperature
    predicted_concrete_temp: Decimal


# =============================================================================
# WORKER SCHEMAS
# =============================================================================

class WorkerCreate(BaseModel):
    """Create a worker."""
    name: str = Field(..., min_length=1, max_length=255)
    role: str = Field(default="labourer", pattern="^(owner|finisher|experienced_labourer|labourer)$")
    hourly_rate_cents: int = Field(default=0, ge=0)
    cost_rate_cents: int = Field(default=0, ge=0)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[EmailStr] = None
    notes: Optional[str] = None
    claims_tax_free_threshold: bool = True
    pay_frequency: str = Field(default="weekly", pattern="^(weekly|fortnightly|monthly)$")


class WorkerUpdate(BaseModel):
    """Update a worker."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    role: Optional[str] = Field(None, pattern="^(owner|finisher|experienced_labourer|labourer)$")
    hourly_rate_cents: Optional[int] = Field(None, ge=0)
    cost_rate_cents: Optional[int] = Field(None, ge=0)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None
    active: Optional[bool] = None
    claims_tax_free_threshold: Optional[bool] = None
    pay_frequency: Optional[str] = Field(None, pattern="^(weekly|fortnightly|monthly)$")


class WorkerResponse(BaseSchema):
    """Worker response."""
    id: int
    name: str
    role: str
    hourly_rate_cents: int
    cost_rate_cents: int
    phone: Optional[str]
    email: Optional[str]
    notes: Optional[str]
    active: bool
    claims_tax_free_threshold: bool
    pay_frequency: str
    created_at: datetime
    updated_at: datetime


class TimeEntryCreate(BaseModel):
    """Log time against a job."""
    quote_id: int
    worker_id: int
    work_date: date
    hours: Decimal = Field(..., gt=0, le=24)
    stage: Optional[str] = Field(None, pattern="^(setup|pour|finish|cleanup)$")
    notes: Optional[str] = None


class TimeEntryResponse(BaseSchema):
    """Time entry response."""
    id: int
    quote_id: int
    worker_id: int
    work_date: date
    hours: Decimal
    stage: Optional[str]
    notes: Optional[str]
    created_at: datetime

    worker_name: Optional[str] = None
    cost_cents: int = 0


# =============================================================================
# JOB ASSIGNMENT SCHEMAS
# =============================================================================

class JobAssignmentCreate(BaseModel):
    """Assign a worker to a job."""
    quote_id: int
    worker_id: int
    role: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = None


class JobAssignmentUpdate(BaseModel):
    """Update a job assignment."""
    role: Optional[str] = Field(None, max_length=50)
    confirmed: Optional[bool] = None
    notes: Optional[str] = None


class JobAssignmentResponse(BaseSchema):
    """Job assignment response."""
    id: int
    quote_id: int
    worker_id: int
    role: Optional[str]
    confirmed: bool
    notes: Optional[str]
    created_at: datetime

    # Nested worker info
    worker: Optional[WorkerResponse] = None
    worker_name: Optional[str] = None


# =============================================================================
# PHOTO SCHEMAS
# =============================================================================

class PhotoUpload(BaseModel):
    """Photo upload request."""
    quote_id: int
    photo_type: str = Field(default="general", pattern="^(before|during|after|issue|general)$")
    caption: Optional[str] = Field(None, max_length=500)


class PhotoResponse(BaseSchema):
    """Photo response."""
    id: int
    quote_id: int
    category: str
    filename: str
    url: str
    thumbnail_url: Optional[str]
    caption: Optional[str]
    taken_at: Optional[datetime]
    shared_with_customer: bool
    created_at: datetime


class PhotoListResponse(BaseModel):
    """List of photos response."""
    success: bool = True
    quote_id: int
    count: int
    photos: List[PhotoResponse]


# =============================================================================
# POUR PLAN SCHEMAS
# =============================================================================

class PourPlanCreate(BaseModel):
    """Create a pour plan for a job."""
    quote_id: int
    planned_date: date
    planned_start_time: Optional[str] = Field(None, pattern="^[0-9]{2}:[0-9]{2}$")


class PourPlanUpdate(BaseModel):
    """Update a pour plan."""
    planned_date: Optional[date] = None
    planned_start_time: Optional[str] = Field(None, pattern="^[0-9]{2}:[0-9]{2}$")


class PourPlanResponse(BaseSchema):
    """Pour plan response."""
    id: int
    quote_id: int
    planned_date: date
    planned_start_time: Optional[str]

    # Weather snapshot
    weather_snapshot: Optional[dict]

    # Calculated values
    evaporation_rate: Optional[float]
    risk_level: Optional[str]
    recommendations: Optional[List[str]]

    created_at: datetime
    updated_at: datetime


class PourConditionsResponse(BaseModel):
    """Pour conditions for a specific job."""
    quote_id: int
    planned_date: Optional[date]

    # Weather
    weather: Optional[dict]

    # Evaporation
    evaporation_rate: Optional[float]
    evaporation_risk: Optional[dict]

    # Setting time
    setting_time: Optional[dict]

    # Slump
    slump_recommendation: Optional[dict]

    # Recommendations
    grade_recommendation: Optional[str]
    admixture_recommendation: Optional[str]
    warnings: List[str] = []
    tips: List[str] = []


# =============================================================================
# API RESPONSES
# =============================================================================

class PaginatedResponse(BaseModel):
    """Paginated list response."""
    items: List[Any]
    total: int
    page: int
    page_size: int
    pages: int


class SuccessResponse(BaseModel):
    """Generic success response."""
    success: bool = True
    message: Optional[str] = None


class ErrorResponse(BaseModel):
    """Error response."""
    success: bool = False
    error: str
    detail: Optional[str] = None
