"""
================================================================================
CONCRETEIQ - QUOTE CALCULATOR
================================================================================
⚠️ SACRED CODE - Ported from KRG_BMS calculator.py

ALL MONEY IN CENTS
DO NOT SIMPLIFY OR "IMPROVE" THE CALCULATIONS
================================================================================
"""

import math
import re
from dataclasses import dataclass, field
from typing import List

from app.quotes.pricing import (
    # Concrete
    get_concrete_price, CONCRETE_FREE_KM, CONCRETE_TRAVEL_RATE,
    SHORT_LOAD_THRESHOLD, SHORT_LOAD_FEE_PER_M3, CONCRETE_BUFFER,
    MIX_ADDITIVES, CONCRETE_COLOURS, CONCRETE_FIBRES,
    RETARDER_COST_PER_LITRE, CEMENT_CONTENT_PER_GRADE,
    # Reinforcement
    get_reinforcement, get_dowel_spacing, get_dowel_rate, STEEL_DELIVERY,
    # Materials & Chemicals
    MATERIALS, CHEMICALS, CONTROL_JOINTS,
    # Productivity
    PRODUCTIVITY, POUR_RATES, POUR_MINUTES_PER_SQM,
    # Equipment
    EXCAVATOR_HIRE_HALF_DAY, EXCAVATOR_HIRE_FULL_DAY, EXCAVATOR_FUEL_PER_HR,
    PRESSURE_WASHER_HALF_DAY, PRESSURE_WASHER_FULL_DAY,
    # Disposal
    SKIPBIN_MINIMUM_M3, SKIPBIN_SOIL_PER_M3, SKIPBIN_CONCRETE_PER_M3,
    TRAILER_CAPACITY_M3, TRAILER_SOIL_PER_LOAD, TRAILER_CONCRETE_PER_LOAD,
    WASTE_TIP_DESTINATIONS,
    # Concrete removal
    CONCRETE_REMOVAL,
    # Complexity
    COMPLEXITY_MULTIPLIERS,
    # Crew & Teams
    TEAM_RATES, get_team_cost_hourly,
    # Markup
    get_material_markup,
    # Overhead & Travel
    calculate_overhead, TRAVEL,
    # Plate compactor
    PLATE_COMPACTOR,
    # GST & Minimums
    GST_RATE, MINIMUM_QUOTE,
)


# ==============================================================================
# INPUT DATACLASS
# ==============================================================================

@dataclass
class CalculatorInput:
    """All inputs for quote calculation."""
    
    # Measurements
    slab_area: float = 0
    perimeter: float = 0         # lm - for sawcut removal calculation
    slab_thickness: float = 100  # mm
    edge_formwork: float = 0     # lm
    internal_formwork: float = 0 # lm
    control_joints: float = 0    # lm
    isolation_joints: float = 0  # lm
    dowel_bars: float = 0        # lm
    fence_sheeting: float = 0       # lm
    steps: int = 0
    
    # Subbase
    subbase_thickness: float = 0  # mm
    compaction: bool = False
    delivery_distance_km: float = 0  # km from sand supplier (Peards, 119 Borella Rd)
    
    # Excavation
    excavation: bool = False
    excavation_depth: float = 0   # mm
    soil_type: str = "soil"       # topsoil, soil, clay, rock
    dig_method: str = "hand"      # hand or machine
    excavation_disposal: str = "none"  # none, skipbin, trailer
    waste_tip_destination: str = "jacksons"  # jacksons, albury_waste, wodonga_transfer

    # Equipment hire
    pressure_washer: bool = False
    pressure_washer_duration: str = "half"  # half or full day

    # Concrete removal
    concrete_removal: bool = False
    removal_area: float = 0       # m²
    removal_thickness: float = 100  # mm
    removal_method: str = "manual" # manual or machine
    removal_reinforced: bool = False
    removal_disposal: str = "skip_bin"  # skip_bin or trailer
    
    # Specification
    concrete_grade: str = "N32"
    mix_additive: str = "None"
    reinforcement: str = "GFRP 450mm"
    control_joint_method: str = "Sawcut"
    pump_required: bool = False
    placement_method: str = "Chute"  # Display only (used by customer_lines.py for PDF)
    season: str = "Summer"           # Display only (pour rate is flat 11 min/m²)
    falls_complexity_pct: float = 0  # 0-100 — extra pour/finish time for complex falls (auto-calculated or manual)

    # Falls detail fields
    fall_type: str = "none"           # none, single, strip_1way, strip_2way, small_pits
    fall_pit_count: int = 0           # number of small pits (only used when fall_type=small_pits)

    # Rebates (starter bars / tie-ins)
    rebates: float = 0  # lineal metres

    # Pier holes
    pier_holes: int = 0
    pier_diameter: float = 300    # mm
    pier_depth: float = 600       # mm
    pier_starters: int = 4        # bars per pier

    # Edge beams
    edge_beams: bool = False
    edge_beam_length: float = 0   # lm
    edge_beam_depth: float = 200  # mm extra depth
    edge_beam_width: float = 300  # mm

    # Plumbing & Drainage (new simple model)
    drainage: bool = False
    plumber_hours: float = 0             # hours of plumber time
    plumber_rate: int = 9500             # cents per hour (default $95/hr)
    plumber_materials_cents: int = 0     # materials allowance in cents
    plumber_description: str = ""        # free-text description of drainage work

    # Legacy drainage fields (kept for backward compatibility with saved quotes)
    drain_pits_300: int = 0
    drain_pits_450: int = 0
    drain_centralising_pits: int = 0
    drain_grates_standard: int = 0
    drain_grates_heavy: int = 0
    drain_surface_drain_lm: float = 0
    drain_ag_pipe_lm: float = 0
    drain_stormwater_100_lm: float = 0
    drain_stormwater_150_lm: float = 0
    drain_tpiece_connections: int = 0
    drain_trench_lm: float = 0
    drain_relocations: int = 0
    drain_labour_hrs: float = 0

    # Inclusions
    inc_release_agent: bool = True
    inc_evap_retarder: bool = True
    inc_durability_enhancer: bool = False
    inc_surface_retarder: bool = False
    inc_curing_compound: bool = False
    inc_sealer: bool = False
    inc_moisture_barrier: bool = True
    inc_isolation_joint: bool = True
    inc_fence_sheeting: bool = False
    inc_formwork_wear: bool = True
    
    # Concrete fibres
    concrete_fibre: str = "None"  # None, Polypropylene, Steel, Glass

    # Coloured concrete
    coloured_concrete: bool = False
    concrete_colour: str = ""

    # Concrete volume override
    concrete_volume_override: float = 0  # 0 = use calculated, >0 = manual m³

    # Exposed aggregate
    wash_off: str = "N/A"  # N/A, Same Day, Next Day
    acid_wash: bool = False
    
    # Legacy fields (kept for backward compat with saved quotes / customer_lines.py PDF)
    site_prep: bool = False
    site_prep_rate: int = 0
    mobilisation: bool = False
    mob_cost: int = 0
    site_clean: bool = False
    clean_rate: int = 0

    # Control joint rate override (0 = use default from pricing.py)
    control_joint_rate: int = 0     # cents per lm (0 = auto)

    # Distances
    distance_km: float = 0          # from base
    concrete_distance_km: float = 0 # from concrete yard

    # Pricing
    tier: str = "Standard"          # Economy, Standard, Premium
    complexity: str = "Standard"    # Easy, Standard, Complex, Very Complex
    team_tier: str = "Standard"     # Team rate tier
    
    # Customer discount
    customer_discount_percent: float = 0  # 0-100 — applied to subtotal before GST

    # Direct hourly rates (cents) - from worker selection
    setup_hourly_rate: int = 0
    pour_hourly_rate: int = 0
    setup_cost_rate: int = 0
    pour_cost_rate: int = 0
    setup_crew_count: int = 3        # number of workers on setup day (rates calibrated for 3)


# ==============================================================================
# RESULT DATACLASS
# ==============================================================================

@dataclass
class LineItem:
    """Single line item for quote."""
    description: str
    quantity: float
    unit: str
    unit_price_cents: int
    total_cents: int
    category: str = "materials"  # materials, labour, other


@dataclass
class BankSplit:
    """Payment split across bank accounts."""
    payroll_cents: int = 0
    tax_provision_cents: int = 0
    operating_cents: int = 0
    reserve_cents: int = 0
    shortfall_cents: int = 0


@dataclass
class Payment:
    """Single payment in schedule."""
    name: str
    percent: float
    amount_cents: int
    split: BankSplit = field(default_factory=BankSplit)


@dataclass
class CalculatorResult:
    """Complete calculation result."""
    
    # Volume
    volume_m3: float = 0
    calculated_volume_m3: float = 0  # Pre-override calculated volume
    volume_override: bool = False     # True if manual override was used

    # Cost breakdowns (all in cents)
    concrete_cost_cents: int = 0
    short_load_fee_cents: int = 0
    concrete_travel_cents: int = 0
    additive_cost_cents: int = 0
    colour_surcharge_cents: int = 0
    fibre_cost_cents: int = 0
    
    subbase_cost_cents: int = 0
    excavation_cost_cents: int = 0
    removal_cost_cents: int = 0
    removal_sawcut_cents: int = 0
    removal_labour_cents: int = 0
    removal_disposal_cents: int = 0
    removal_volume_m3: float = 0
    removal_sawcut_hours: float = 0
    removal_labour_hours: float = 0

    setup_materials_cents: int = 0
    reo_cost_cents: int = 0  # reinforcement cost for frontend display
    pour_materials_cents: int = 0
    finish_materials_cents: int = 0
    
    setup_labour_cents: int = 0
    pour_labour_cents: int = 0
    finish_labour_cents: int = 0
    
    overhead_cents: int = 0
    travel_cents: int = 0
    
    # Hours
    setup_hours: float = 0
    pour_hours: float = 0
    finish_hours: float = 0
    sawcut_hours: float = 0

    # Hour breakdowns — list of {"task": str, "hours": float} for detailed view
    setup_hour_details: List[dict] = field(default_factory=list)
    pour_hour_details: List[dict] = field(default_factory=list)
    finish_hour_details: List[dict] = field(default_factory=list)
    setup_manhours: float = 0
    control_joint_rate_used: int = 0  # actual CJ rate in cents/lm (for display)

    # Totals
    raw_cost_cents: int = 0
    markup_cents: int = 0
    discount_cents: int = 0
    subtotal_cents: int = 0
    gst_cents: int = 0
    total_cents: int = 0
    
    # Per m² rate
    rate_per_sqm_cents: int = 0
    
    # Profit tracking
    labour_cost_cents: int = 0
    labour_sell_cents: int = 0
    labour_margin_cents: int = 0
    profit_cents: int = 0
    
    # Minimum tracking
    minimum_applied: bool = False
    original_total_cents: int = 0
    
    # Dowels
    dowel_count: int = 0
    dowel_spacing: int = 0
    
    # Pier holes
    pier_volume_m3: float = 0
    pier_cost_cents: int = 0

    # Edge beams
    edge_beam_volume_m3: float = 0
    edge_beam_cost_cents: int = 0

    # Drainage
    drainage_cost_cents: int = 0

    # Display flags
    show_excavation: bool = False
    show_removal: bool = False
    show_subbase: bool = False
    show_exposed: bool = False
    show_travel: bool = False
    show_sawcut: bool = False
    show_piers: bool = False
    show_edge_beams: bool = False
    show_drainage: bool = False
    
    # Line items for PDF
    line_items: List[LineItem] = field(default_factory=list)
    
    # Payment schedule
    payments: List[Payment] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "volume_m3": self.volume_m3,
            "calculated_volume_m3": self.calculated_volume_m3,
            "volume_override": self.volume_override,
            "concrete_cost_cents": self.concrete_cost_cents,
            "concrete_travel_cents": self.concrete_travel_cents,
            "additive_cost_cents": self.additive_cost_cents,
            "colour_surcharge_cents": self.colour_surcharge_cents,
            "fibre_cost_cents": self.fibre_cost_cents,
            "short_load_fee_cents": self.short_load_fee_cents,
            "subbase_cost_cents": self.subbase_cost_cents,
            "excavation_cost_cents": self.excavation_cost_cents,
            "removal_cost_cents": self.removal_cost_cents,
            "removal_sawcut_cents": self.removal_sawcut_cents,
            "removal_labour_cents": self.removal_labour_cents,
            "removal_disposal_cents": self.removal_disposal_cents,
            "removal_volume_m3": self.removal_volume_m3,
            "removal_sawcut_hours": self.removal_sawcut_hours,
            "removal_labour_hours": self.removal_labour_hours,
            "overhead_cents": self.overhead_cents,
            "travel_cents": self.travel_cents,
            "subtotal_cents": self.subtotal_cents,
            "gst_cents": self.gst_cents,
            "total_cents": self.total_cents,
            "rate_per_sqm_cents": self.rate_per_sqm_cents,
            "setup_hours": self.setup_hours,
            "pour_hours": self.pour_hours,
            "finish_hours": self.finish_hours,
            "sawcut_hours": self.sawcut_hours,
            "setup_hour_details": self.setup_hour_details,
            "pour_hour_details": self.pour_hour_details,
            "finish_hour_details": self.finish_hour_details,
            "setup_manhours": self.setup_manhours,
            "control_joint_rate_used": self.control_joint_rate_used,
            "profit_cents": self.profit_cents,
            "labour_margin_cents": self.labour_margin_cents,
            "labour_cost_cents": self.labour_cost_cents,
            "labour_sell_cents": self.labour_sell_cents,
            "markup_cents": self.markup_cents,
            "discount_cents": self.discount_cents,
            "raw_cost_cents": self.raw_cost_cents,
            "setup_materials_cents": self.setup_materials_cents,
            "reo_cost_cents": self.reo_cost_cents,
            "pour_materials_cents": self.pour_materials_cents,
            "finish_materials_cents": self.finish_materials_cents,
            "setup_labour_cents": self.setup_labour_cents,
            "pour_labour_cents": self.pour_labour_cents,
            "finish_labour_cents": self.finish_labour_cents,
            "minimum_applied": self.minimum_applied,
            "original_total_cents": self.original_total_cents,
            "dowel_count": self.dowel_count,
            "dowel_spacing": self.dowel_spacing,
            "show_excavation": self.show_excavation,
            "show_removal": self.show_removal,
            "show_subbase": self.show_subbase,
            "show_exposed": self.show_exposed,
            "show_travel": self.show_travel,
            "show_sawcut": self.show_sawcut,
            "show_piers": self.show_piers,
            "show_edge_beams": self.show_edge_beams,
            "show_drainage": self.show_drainage,
            "drainage_cost_cents": self.drainage_cost_cents,
            "pier_volume_m3": self.pier_volume_m3,
            "pier_cost_cents": self.pier_cost_cents,
            "edge_beam_volume_m3": self.edge_beam_volume_m3,
            "edge_beam_cost_cents": self.edge_beam_cost_cents,
            "line_items": [
                {
                    "description": li.description,
                    "quantity": li.quantity,
                    "unit": li.unit,
                    "unit_price_cents": li.unit_price_cents,
                    "total_cents": li.total_cents,
                    "category": li.category,
                }
                for li in self.line_items
            ],
            "payments": [
                {
                    "name": p.name,
                    "percent": p.percent,
                    "amount_cents": p.amount_cents,
                }
                for p in self.payments
            ],
        }


# ==============================================================================
# SAFE HELPERS
# ==============================================================================

def safe_float(value, default=0, min_val=None, max_val=None) -> float:
    """Safely convert to float with bounds."""
    try:
        result = float(value or default)
    except (ValueError, TypeError):
        result = default
    if min_val is not None:
        result = max(min_val, result)
    if max_val is not None:
        result = min(max_val, result)
    return result


def safe_div(numerator, denominator, default=0) -> float:
    """Safely divide, returning default if denominator is zero."""
    if not denominator or denominator == 0:
        return default
    return numerator / denominator


def to_cents(dollars: float) -> int:
    """Convert dollars to cents."""
    return int(round(dollars * 100))


# ==============================================================================
# MAIN CALCULATOR
# ==============================================================================

def calculate(inp: CalculatorInput, pricing: dict = None) -> CalculatorResult:
    """
    Main calculation function.
    
    ⚠️ SACRED - Ported from KRG_BMS
    ⚠️ ALL CALCULATIONS MUST MATCH KRG_BMS
    """
    r = CalculatorResult()
    
    # ==========================================================================
    # INPUT VALIDATION
    # ==========================================================================
    inp.slab_area = safe_float(inp.slab_area, 0, min_val=0, max_val=10000)
    inp.slab_thickness = safe_float(inp.slab_thickness, 100, min_val=50, max_val=500)
    inp.edge_formwork = safe_float(inp.edge_formwork, 0, min_val=0, max_val=5000)
    inp.internal_formwork = safe_float(inp.internal_formwork, 0, min_val=0, max_val=5000)
    inp.control_joints = safe_float(inp.control_joints, 0, min_val=0, max_val=5000)
    inp.isolation_joints = safe_float(inp.isolation_joints, 0, min_val=0, max_val=1000)
    inp.fence_sheeting = safe_float(inp.fence_sheeting, 0, min_val=0, max_val=1000)
    inp.distance_km = safe_float(inp.distance_km, 0, min_val=0, max_val=500)
    inp.concrete_distance_km = safe_float(inp.concrete_distance_km, 0, min_val=0, max_val=200)
    inp.setup_crew_count = int(safe_float(inp.setup_crew_count, 3, min_val=1, max_val=10))
    inp.subbase_thickness = safe_float(inp.subbase_thickness, 0, min_val=0, max_val=300)
    inp.delivery_distance_km = safe_float(inp.delivery_distance_km, 0, min_val=0, max_val=500)
    inp.excavation_depth = safe_float(inp.excavation_depth, 0, min_val=0, max_val=500)
    inp.dig_method = inp.dig_method if inp.dig_method in ("hand", "machine") else "hand"
    inp.excavation_disposal = inp.excavation_disposal if inp.excavation_disposal in ("none", "skipbin", "trailer") else "none"
    inp.pressure_washer = bool(getattr(inp, 'pressure_washer', False))
    inp.pressure_washer_duration = inp.pressure_washer_duration if inp.pressure_washer_duration in ("half", "full") else "half"
    inp.removal_area = safe_float(inp.removal_area, 0, min_val=0, max_val=10000)
    inp.removal_thickness = safe_float(inp.removal_thickness, 100, min_val=50, max_val=500)
    inp.removal_reinforced = bool(getattr(inp, 'removal_reinforced', False))
    inp.removal_method = inp.removal_method if inp.removal_method in ("manual", "machine") else "manual"
    inp.removal_disposal = inp.removal_disposal if inp.removal_disposal in ("skip_bin", "trailer") else "skip_bin"
    # tip_truck disposal removed — only skip_bin and trailer supported
    inp.perimeter = safe_float(inp.perimeter, 0, min_val=0, max_val=5000)
    inp.steps = int(safe_float(inp.steps, 0, min_val=0, max_val=20))
    inp.dowel_bars = safe_float(inp.dowel_bars, 0, min_val=0, max_val=1000)
    inp.fall_type = inp.fall_type if inp.fall_type in (
        "none", "minimal", "pits", "complex_pits",
        # Legacy values for backward compat with old saved quotes
        "single", "strip_1way", "strip_2way", "small_pits"
    ) else "none"
    inp.fall_pit_count = int(safe_float(inp.fall_pit_count, 0, min_val=0, max_val=20))

    # Auto-calculate falls_complexity_pct from fall_type preset + pit count
    # Presets:
    #   flat         → 0%
    #   minimal      → 8%  (gentle slope, 1-2 fall directions)
    #   pits         → 12% base + 5% per extra pit (~4 falls each)
    #   complex_pits → 20% base + 8% per extra pit (~6 falls each)
    if inp.fall_type == "none":
        inp.falls_complexity_pct = 0
    elif inp.fall_type == "minimal":
        inp.falls_complexity_pct = 8
    elif inp.fall_type == "pits":
        pits = max(1, inp.fall_pit_count)
        inp.falls_complexity_pct = min(100, 12 + (pits - 1) * 5)
    elif inp.fall_type == "complex_pits":
        pits = max(1, inp.fall_pit_count)
        inp.falls_complexity_pct = min(100, 20 + (pits - 1) * 8)
    # Legacy fall types from old quotes — map to reasonable %
    elif inp.fall_type == "single":
        inp.falls_complexity_pct = 5
    elif inp.fall_type == "strip_1way":
        inp.falls_complexity_pct = 8
    elif inp.fall_type == "strip_2way":
        inp.falls_complexity_pct = 12
    elif inp.fall_type == "small_pits":
        pits = max(1, inp.fall_pit_count)
        inp.falls_complexity_pct = min(100, 12 + (pits - 1) * 5)

    inp.falls_complexity_pct = safe_float(inp.falls_complexity_pct, 0, min_val=0, max_val=100)
    inp.rebates = safe_float(inp.rebates, 0, min_val=0, max_val=1000)
    inp.pier_holes = int(safe_float(inp.pier_holes, 0, min_val=0, max_val=50))
    inp.pier_diameter = safe_float(inp.pier_diameter, 300, min_val=150, max_val=1200)
    inp.pier_depth = safe_float(inp.pier_depth, 600, min_val=300, max_val=3000)
    inp.pier_starters = int(safe_float(inp.pier_starters, 4, min_val=0, max_val=20))
    inp.edge_beam_length = safe_float(inp.edge_beam_length, 0, min_val=0, max_val=500)
    inp.edge_beam_depth = safe_float(inp.edge_beam_depth, 200, min_val=50, max_val=1000)
    inp.edge_beam_width = safe_float(inp.edge_beam_width, 300, min_val=100, max_val=1000)
    inp.concrete_volume_override = safe_float(inp.concrete_volume_override, 0, min_val=0, max_val=100)
    inp.customer_discount_percent = safe_float(inp.customer_discount_percent, 0, min_val=0, max_val=100)

    # String defaults
    inp.concrete_grade = inp.concrete_grade or "N32"
    inp.tier = inp.tier or "Standard"
    inp.complexity = inp.complexity or "Standard"
    inp.reinforcement = inp.reinforcement or "None"
    inp.mix_additive = inp.mix_additive or "None"
    inp.team_tier = inp.team_tier or "Standard"
    
    # Early return for zero area
    if inp.slab_area <= 0:
        return r
    
    # ==========================================================================
    # CONCRETE VOLUME & COST
    # ==========================================================================
    vol = inp.slab_area * (inp.slab_thickness / 1000) * (1 + CONCRETE_BUFFER)
    calculated_vol = math.ceil(vol * 10) / 10  # Round up to 0.1m³
    r.calculated_volume_m3 = calculated_vol    # Always store calculated for reference

    # Override: snap to 0.2m³ increments (concrete plant minimum order increment)
    # Use round(x, 1) before ceil to avoid floating point bumps (e.g. 1.2000000000001 → 1.4)
    if inp.concrete_volume_override and inp.concrete_volume_override > 0:
        r.volume_m3 = round(math.ceil(round(inp.concrete_volume_override, 2) / 0.2) * 0.2, 1)
        r.volume_override = True
    else:
        # Snap calculated volume to 0.2m³ increments too
        r.volume_m3 = round(math.ceil(round(calculated_vol, 2) / 0.2) * 0.2, 1)
    
    # Base rate
    concrete_rate = get_concrete_price(inp.concrete_grade)
    concrete_base = int(concrete_rate * r.volume_m3)
    
    # Travel surcharge
    concrete_travel_km = max(0, inp.concrete_distance_km - CONCRETE_FREE_KM)
    travel_surcharge = int(concrete_travel_km * CONCRETE_TRAVEL_RATE)
    r.concrete_travel_cents = int(travel_surcharge * r.volume_m3)
    
    # Mix additive
    additive_data = MIX_ADDITIVES.get(inp.mix_additive, {"cost": 0, "time_mins": 0})
    additive_time_effect = additive_data.get("time_mins", 0)

    if additive_data.get("weight_based"):
        # Retarder: $5/L, litres = (cement_kg / 1000) × dosage%
        grade_key = inp.concrete_grade
        if "exposed" in grade_key.lower():
            grade_key = "Exposed"
        else:
            m = re.search(r'N\d+', grade_key)
            if m:
                grade_key = m.group()
        cement_kg = CEMENT_CONTENT_PER_GRADE.get(grade_key, 340)
        pct = additive_data.get("percentage", 1)
        litres = (cement_kg / 1000) * pct
        additive_rate = int(litres * RETARDER_COST_PER_LITRE)
        r.additive_cost_cents = int(additive_rate * r.volume_m3)
    else:
        additive_rate = additive_data.get("cost", 0)
        r.additive_cost_cents = int(additive_rate * r.volume_m3)

    # Colour surcharge
    colour_rate = 0
    if inp.coloured_concrete and inp.concrete_colour:
        colour_rate = CONCRETE_COLOURS.get(inp.concrete_colour, 0)
        r.colour_surcharge_cents = int(colour_rate * r.volume_m3)

    # Fibre cost
    fibre_rate = 0
    if inp.concrete_fibre and inp.concrete_fibre != "None":
        fibre_rate = CONCRETE_FIBRES.get(inp.concrete_fibre, 0)
        r.fibre_cost_cents = int(fibre_rate * r.volume_m3)

    # Short load fee
    if r.volume_m3 < SHORT_LOAD_THRESHOLD:
        r.short_load_fee_cents = int((SHORT_LOAD_THRESHOLD - r.volume_m3) * SHORT_LOAD_FEE_PER_M3)

    # Total concrete cost
    r.concrete_cost_cents = concrete_base + r.concrete_travel_cents + r.additive_cost_cents + r.short_load_fee_cents + r.colour_surcharge_cents + r.fibre_cost_cents

    # Concrete line item description
    concrete_desc = f"Supply {inp.concrete_grade} concrete"
    if inp.coloured_concrete and inp.concrete_colour:
        concrete_desc += f" — {inp.concrete_colour}"
    if inp.concrete_fibre and inp.concrete_fibre != "None":
        concrete_desc += f" + {inp.concrete_fibre} fibres"

    # Add line item
    r.line_items.append(LineItem(
        description=concrete_desc,
        quantity=r.volume_m3,
        unit="m³",
        unit_price_cents=int(r.concrete_cost_cents / r.volume_m3) if r.volume_m3 > 0 else 0,
        total_cents=r.concrete_cost_cents,
        category="materials",
    ))
    
    # ==========================================================================
    # SUBBASE
    # ==========================================================================
    if inp.subbase_thickness > 0:
        r.show_subbase = True
        base_thickness = 37.5  # mm
        thickness_factor = inp.subbase_thickness / base_thickness
        
        # Material cost
        material_cost = int(MATERIALS["subbase_sand"] * inp.slab_area * thickness_factor)

        # Delivery cost — distance-based from Peards (119 Borella Road, Albury)
        # $50 flat for ≤20km, then +$1.50/km beyond 20km radius
        delivery_cost = MATERIALS["subbase_delivery_flat"]
        if inp.delivery_distance_km > MATERIALS["subbase_delivery_radius_km"]:
            extra_km = inp.delivery_distance_km - MATERIALS["subbase_delivery_radius_km"]
            delivery_cost += int(extra_km * MATERIALS["subbase_delivery_per_km"])
        
        # Compactor costs
        compactor_cost = 0
        if inp.compaction:
            pc = PLATE_COMPACTOR
            fuel_litres = pc["fuel_per_sqm"] * inp.slab_area
            fuel_cost = int(fuel_litres * pc["fuel_price"])
            
            jobs_per_year = pc["jobs_per_year"] or 100
            annual_depreciation = pc["purchase_price"] / max(1, pc["total_lifespan"])
            depreciation = int(annual_depreciation / jobs_per_year)
            service = int(pc["annual_service"] / jobs_per_year)
            
            compactor_cost = fuel_cost + depreciation + service
        
        r.subbase_cost_cents = material_cost + delivery_cost + compactor_cost
        
        r.line_items.append(LineItem(
            description=f"Supply & compact {int(inp.subbase_thickness)}mm crusher dust subbase",
            quantity=inp.slab_area,
            unit="m²",
            unit_price_cents=int(r.subbase_cost_cents / inp.slab_area),
            total_cents=r.subbase_cost_cents,
            category="materials",
        ))
    
    # ==========================================================================
    # PIER HOLES
    # ==========================================================================
    if inp.pier_holes > 0:
        r.show_piers = True
        radius_m = (inp.pier_diameter / 2) / 1000
        depth_m = inp.pier_depth / 1000
        vol_per = math.pi * radius_m**2 * depth_m
        r.pier_volume_m3 = round(vol_per * inp.pier_holes, 2)
        r.volume_m3 += r.pier_volume_m3  # Add to concrete order

        sono_cost = inp.pier_holes * MATERIALS["pier_sono_tube"]
        bar_cost = inp.pier_holes * inp.pier_starters * MATERIALS["pier_starter_bar"]
        r.pier_cost_cents = sono_cost + bar_cost

        r.line_items.append(LineItem(
            description=f"Pier holes ({inp.pier_holes}× {int(inp.pier_diameter)}mm dia × {int(inp.pier_depth)}mm deep)",
            quantity=inp.pier_holes,
            unit="piers",
            unit_price_cents=int(r.pier_cost_cents / inp.pier_holes) if inp.pier_holes > 0 else 0,
            total_cents=r.pier_cost_cents,
            category="materials",
        ))

    # ==========================================================================
    # EDGE BEAMS
    # ==========================================================================
    if inp.edge_beams and inp.edge_beam_length > 0:
        r.show_edge_beams = True
        width_m = inp.edge_beam_width / 1000
        depth_m = inp.edge_beam_depth / 1000
        r.edge_beam_volume_m3 = round(inp.edge_beam_length * width_m * depth_m, 2)
        r.volume_m3 += r.edge_beam_volume_m3  # Add to concrete order
        r.edge_beam_cost_cents = int(MATERIALS["edge_beam_reo"] * inp.edge_beam_length)

        r.line_items.append(LineItem(
            description=f"Edge beams ({inp.edge_beam_length}lm × {int(inp.edge_beam_width)}mm × {int(inp.edge_beam_depth)}mm)",
            quantity=inp.edge_beam_length,
            unit="lm",
            unit_price_cents=MATERIALS["edge_beam_reo"],
            total_cents=r.edge_beam_cost_cents,
            category="materials",
        ))

    # Final snap: ensure total order volume (slab + piers + beams) is in 0.2m³ increments
    if not r.volume_override:
        r.volume_m3 = round(math.ceil(round(r.volume_m3, 2) / 0.2) * 0.2, 1)
    else:
        r.volume_m3 = round(r.volume_m3, 1)  # Clean float

    # ==========================================================================
    # EXCAVATION (Digout) — hours added in setup section
    # ==========================================================================
    # Hand dig: 0.38 m³/hr, Machine: 1.0 m³/hr (1.7t excavator)
    # Both methods add labour hours to setup based on volume.
    # Machine adds hire cost (half/full day based on dig hours) + fuel.
    if inp.excavation and inp.excavation_depth > 0:
        r.show_excavation = True

    if inp.excavation and inp.excavation_depth > 0 and inp.dig_method == "machine":
        exc_volume = inp.slab_area * (inp.excavation_depth / 1000)
        dig_hrs = exc_volume / PRODUCTIVITY["machine_dig"]  # 1.0 m³/hr

        # Half day (≤4hrs) or full day (>4hrs)
        if dig_hrs <= 4:
            hire_cost = EXCAVATOR_HIRE_HALF_DAY
            hire_label = "half day"
        else:
            hire_cost = EXCAVATOR_HIRE_FULL_DAY
            hire_label = "full day"

        # Fuel: ~3L/hr × $1.77/L ≈ $5.50/hr
        fuel_cost = int(dig_hrs * EXCAVATOR_FUEL_PER_HR)

        total_equip = hire_cost + fuel_cost
        r.excavation_cost_cents = total_equip

        r.line_items.append(LineItem(
            description=f"1.7t Excavator Hire — {hire_label} ({round(exc_volume, 1)}m³)",
            quantity=1,
            unit="ea",
            unit_price_cents=hire_cost,
            total_cents=hire_cost,
            category="other",
        ))
        r.line_items.append(LineItem(
            description=f"Excavator Fuel ({round(dig_hrs, 1)}hrs × ~3L/hr)",
            quantity=round(dig_hrs, 1),
            unit="hr",
            unit_price_cents=EXCAVATOR_FUEL_PER_HR,
            total_cents=fuel_cost,
            category="other",
        ))

    # --- Excavation Soil Disposal ---
    if inp.excavation and inp.excavation_depth > 0 and inp.excavation_disposal != "none":
        exc_volume = inp.slab_area * (inp.excavation_depth / 1000)

        if inp.excavation_disposal == "skipbin":
            # $160/m³ ex GST, minimum 3m³
            billable_vol = max(exc_volume, SKIPBIN_MINIMUM_M3)
            disposal_cost = int(billable_vol * SKIPBIN_SOIL_PER_M3)
            min_note = f" (min {SKIPBIN_MINIMUM_M3}m³)" if exc_volume < SKIPBIN_MINIMUM_M3 else ""
            r.line_items.append(LineItem(
                description=f"Skip Bin — soil disposal{min_note}",
                quantity=round(billable_vol, 1),
                unit="m³",
                unit_price_cents=SKIPBIN_SOIL_PER_M3,
                total_cents=disposal_cost,
                category="other",
            ))
        else:  # trailer
            num_loads = max(1, math.ceil(exc_volume / TRAILER_CAPACITY_M3))
            disposal_cost = num_loads * TRAILER_SOIL_PER_LOAD
            tip_name = WASTE_TIP_DESTINATIONS.get(inp.waste_tip_destination, "tip")
            r.line_items.append(LineItem(
                description=f"Trailer Disposal — soil to {tip_name} ({num_loads} load{'s' if num_loads > 1 else ''})",
                quantity=num_loads,
                unit="load",
                unit_price_cents=TRAILER_SOIL_PER_LOAD,
                total_cents=disposal_cost,
                category="other",
            ))

        r.excavation_cost_cents += disposal_cost

    # ==========================================================================
    # PRESSURE WASHER HIRE
    # ==========================================================================
    if inp.pressure_washer:
        if inp.pressure_washer_duration == "full":
            pw_cost = PRESSURE_WASHER_FULL_DAY
            pw_label = "full day"
        else:
            pw_cost = PRESSURE_WASHER_HALF_DAY
            pw_label = "half day"

        r.line_items.append(LineItem(
            description=f"Pressure Washer Hire — {pw_label}",
            quantity=1,
            unit="ea",
            unit_price_cents=pw_cost,
            total_cents=pw_cost,
            category="other",
        ))
        # Add to excavation_cost_cents (equipment bucket)
        r.excavation_cost_cents += pw_cost

    # ==========================================================================
    # CONCRETE REMOVAL
    # ==========================================================================
    # Component-based: sawcutting + breaking/removal labour + disposal
    if inp.concrete_removal and inp.removal_area > 0:
        r.show_removal = True
        cr = CONCRETE_REMOVAL  # shorthand

        # --- 1. Volume ---
        r.removal_volume_m3 = round(inp.removal_area * (inp.removal_thickness / 1000), 2)

        # --- 2. Sawcutting ---
        # Approximate lineal metres of sawcuts from area + strip width
        # Internal grid: cuts in two directions spaced at strip_width
        # Plus perimeter cuts around the edge
        side = math.sqrt(inp.removal_area)
        strip_w_mm = cr["manual_strip_width"] if inp.removal_method == "manual" else cr["machine_strip_width"]
        strip_w_m = strip_w_mm / 1000

        cuts_per_dir = max(0, math.floor(side / strip_w_m) - 1)
        internal_lm = cuts_per_dir * side * 2  # two directions
        perimeter_lm = side * 4  # approximate perimeter
        total_sawcut_lm = internal_lm + perimeter_lm

        sawcut_hours = total_sawcut_lm / cr["sawcut_rate_lm_hr"]
        if inp.removal_reinforced:
            sawcut_hours *= (1 + cr["reinforced_sawcut_penalty"])
        r.removal_sawcut_hours = round(sawcut_hours, 2)

        sawcut_cost = int(sawcut_hours * (cr["labour_rate_hr"] + cr["sawcut_consumables_hr"]))
        r.removal_sawcut_cents = sawcut_cost

        # --- 3. Breaking / Removal Labour ---
        if inp.removal_method == "manual":
            loading_rate = cr["manual_loading_rate"]  # m³/hr
        else:
            loading_rate = cr["machine_loading_rate"]  # m³/hr

        labour_hours = r.removal_volume_m3 / loading_rate if loading_rate > 0 else 0
        if inp.removal_reinforced:
            labour_hours *= (1 + cr["reinforced_removal_penalty"])
        r.removal_labour_hours = round(labour_hours, 2)

        labour_cost = int(labour_hours * cr["loading_labour_rate_hr"])

        # Machine day rate on top of labour (excavator hire)
        if inp.removal_method == "machine":
            machine_days = max(1, math.ceil(r.removal_volume_m3 / cr["machine_capacity_m3_day"]))
            labour_cost += machine_days * cr["machine_day_rate"]

        r.removal_labour_cents = labour_cost

        # --- 4. Disposal ---
        if inp.removal_disposal == "skip_bin":
            # $330/m³ ex GST, minimum 3m³
            billable_vol = max(r.removal_volume_m3, SKIPBIN_MINIMUM_M3)
            disposal_cost = int(billable_vol * SKIPBIN_CONCRETE_PER_M3)
        else:  # trailer
            # $167/load, 0.5m³ per load
            num_loads = max(1, math.ceil(r.removal_volume_m3 / TRAILER_CAPACITY_M3)) if r.removal_volume_m3 > 0 else 1
            disposal_cost = num_loads * TRAILER_CONCRETE_PER_LOAD
            tip_name = WASTE_TIP_DESTINATIONS.get(inp.waste_tip_destination, "tip")

        r.removal_disposal_cents = disposal_cost

        # --- 5. Total ---
        r.removal_cost_cents = r.removal_sawcut_cents + r.removal_labour_cents + r.removal_disposal_cents

        # Line item for quote
        method_label = "Manual" if inp.removal_method == "manual" else "Machine"
        tip_dest = WASTE_TIP_DESTINATIONS.get(inp.waste_tip_destination, "tip")
        disposal_labels = {"skip_bin": "skip bin", "trailer": f"trailer to {tip_dest}"}
        disposal_label = disposal_labels.get(inp.removal_disposal, "skip bin")
        reo_label = " (reinforced)" if inp.removal_reinforced else ""

        r.line_items.append(LineItem(
            description=f"Concrete removal{reo_label} — {method_label}, {disposal_label} disposal",
            quantity=inp.removal_area,
            unit="m²",
            unit_price_cents=int(r.removal_cost_cents / inp.removal_area) if inp.removal_area > 0 else 0,
            total_cents=r.removal_cost_cents,
            category="other",
        ))
    
    # ==========================================================================
    # PLUMBING & DRAINAGE
    # ==========================================================================
    if inp.drainage:
        r.show_drainage = True

        # New simple model: plumber hours + rate + materials
        use_new_model = inp.plumber_hours > 0 or inp.plumber_materials_cents > 0

        if use_new_model:
            labour_cost = int(inp.plumber_hours * inp.plumber_rate)
            materials_cost = inp.plumber_materials_cents
            drain_total = labour_cost + materials_cost

            r.drainage_cost_cents = drain_total

            # Build description
            parts = []
            if inp.plumber_hours > 0:
                parts.append(f"{inp.plumber_hours:.1f}hrs @ ${inp.plumber_rate / 100:.0f}/hr")
            if materials_cost > 0:
                parts.append(f"${materials_cost / 100:.0f} materials")
            if inp.plumber_description:
                desc = f"Plumbing & drainage — {inp.plumber_description}"
            else:
                desc = "Plumbing & drainage — " + ", ".join(parts) if parts else "Plumbing & drainage"

            r.line_items.append(LineItem(
                description=desc,
                quantity=inp.plumber_hours if inp.plumber_hours > 0 else 1,
                unit="hrs" if inp.plumber_hours > 0 else "lot",
                unit_price_cents=inp.plumber_rate if inp.plumber_hours > 0 else drain_total,
                total_cents=drain_total,
                category="other",
            ))
        else:
            # Legacy itemized model (backward compat for old saved quotes)
            drain_total = 0
            drain_total += inp.drain_pits_300 * MATERIALS.get("drain_pit_300", 35000)
            drain_total += inp.drain_pits_450 * MATERIALS.get("drain_pit_450", 55000)
            drain_total += inp.drain_centralising_pits * MATERIALS.get("drain_centralising_pit", 45000)
            drain_total += inp.drain_grates_standard * MATERIALS.get("drain_grate_standard", 18000)
            drain_total += inp.drain_grates_heavy * MATERIALS.get("drain_grate_heavy", 28000)
            drain_total += int(inp.drain_surface_drain_lm * MATERIALS.get("drain_surface_drain_lm", 12000))
            drain_total += int(inp.drain_ag_pipe_lm * MATERIALS.get("drain_ag_pipe_90_lm", 3500))
            drain_total += int(inp.drain_stormwater_100_lm * MATERIALS.get("drain_stormwater_100_lm", 5500))
            drain_total += int(inp.drain_stormwater_150_lm * MATERIALS.get("drain_stormwater_150_lm", 7500))
            drain_total += inp.drain_tpiece_connections * MATERIALS.get("drain_tpiece_junction", 8500)
            drain_total += int(inp.drain_trench_lm * MATERIALS.get("drain_trench_lm", 4500))
            total_pipe_lm = inp.drain_ag_pipe_lm + inp.drain_stormwater_100_lm + inp.drain_stormwater_150_lm
            drain_total += int(total_pipe_lm * MATERIALS.get("drain_gravel_lm", 2500))
            drain_total += inp.drain_relocations * MATERIALS.get("drain_relocate", 25000)
            drain_total += int(inp.drain_labour_hrs * MATERIALS.get("drain_labour_hr", 8500))

            r.drainage_cost_cents = drain_total

            parts = []
            total_pits = inp.drain_pits_300 + inp.drain_pits_450 + inp.drain_centralising_pits
            if total_pits > 0:
                parts.append(f"{total_pits} pit{'s' if total_pits != 1 else ''}")
            total_grates = inp.drain_grates_standard + inp.drain_grates_heavy
            if total_grates > 0:
                parts.append(f"{total_grates} grate{'s' if total_grates != 1 else ''}")
            if total_pipe_lm > 0:
                parts.append(f"{total_pipe_lm:.0f}lm pipe")
            if inp.drain_surface_drain_lm > 0:
                parts.append(f"{inp.drain_surface_drain_lm:.0f}lm surface drain")
            if inp.drain_relocations > 0:
                parts.append(f"{inp.drain_relocations} relocation{'s' if inp.drain_relocations != 1 else ''}")

            desc = "Plumbing & drainage — " + ", ".join(parts) if parts else "Plumbing & drainage system"
            r.line_items.append(LineItem(
                description=desc,
                quantity=1,
                unit="lot",
                unit_price_cents=drain_total,
                total_cents=drain_total,
                category="other",
            ))

    # ==========================================================================
    # DOWELS
    # ==========================================================================
    r.dowel_spacing = get_dowel_spacing(inp.reinforcement)
    dowels_per_lm = 1000 / r.dowel_spacing if r.dowel_spacing > 0 else 2.5
    r.dowel_count = int(inp.dowel_bars * dowels_per_lm)
    
    # ==========================================================================
    # SETUP HOURS (with per-task breakdown)
    # ==========================================================================
    hrs = 0
    setup_details = []
    thickness_factor = inp.slab_thickness / 100

    def _add_setup(task_name, task_hrs):
        nonlocal hrs
        if task_hrs > 0:
            hrs += task_hrs
            setup_details.append({"task": task_name, "hours": round(task_hrs, 2)})

    # --- Boxing (formwork erect + form oil combined) ---
    total_formwork = inp.edge_formwork + inp.internal_formwork
    if total_formwork > 0:
        _add_setup("Boxing", safe_div(total_formwork, PRODUCTIVITY["boxing"]))

    # --- Sand (subbase prep + wheelbarrow + sand screed) ---
    # Base rate: 10 m²/hr at 60mm depth. Multiplier = depth ÷ 60.
    if inp.subbase_thickness > 0:
        subbase_factor = inp.subbase_thickness / 60
        sand_hrs = safe_div(inp.slab_area, PRODUCTIVITY["sand_compact"]) * subbase_factor
        _add_setup("Sand & Screed", sand_hrs)

    # --- Compaction (optional — 3 min/m² = 20 m²/hr) ---
    if inp.compaction:
        _add_setup("Compaction", safe_div(inp.slab_area, PRODUCTIVITY["plate_compaction"]))

    # --- Excavation (volume based) ---
    # Hand: 0.38 m³/hr | Machine (1.7t excavator): 1.0 m³/hr
    if inp.excavation and inp.excavation_depth > 0:
        dig_volume = inp.slab_area * (inp.excavation_depth / 1000)
        if inp.dig_method == "hand":
            _add_setup("Hand Dig", safe_div(dig_volume, PRODUCTIVITY["hand_dig"]))
        else:
            _add_setup("Machine Dig", safe_div(dig_volume, PRODUCTIVITY["machine_dig"]))

    reo = get_reinforcement(inp.reinforcement)
    if reo.get("rate", 0) > 0:
        _add_setup(inp.reinforcement, safe_div(inp.slab_area, reo["rate"]))

    if inp.inc_moisture_barrier:
        _add_setup("Moisture Barrier", safe_div(inp.slab_area, PRODUCTIVITY["moisture_barrier_full"]))

    if inp.isolation_joints > 0:
        _add_setup("Ableflex", safe_div(inp.isolation_joints, PRODUCTIVITY["isolation_joint"]))

    if r.dowel_count > 0:
        _add_setup("Dowels", safe_div(r.dowel_count, PRODUCTIVITY["dowels"]))

    if inp.fence_sheeting > 0:
        _add_setup("Fence Sheeting", safe_div(inp.fence_sheeting, PRODUCTIVITY["fence_sheeting"]))

    if inp.steps > 0:
        _add_setup("Steps", inp.steps * MATERIALS["step_hours"])

    if inp.rebates > 0:
        _add_setup("Rebates", safe_div(inp.rebates, PRODUCTIVITY["rebates"]))

    if inp.pier_holes > 0:
        _add_setup("Pier Holes", inp.pier_holes * MATERIALS["pier_labour_hrs"])

    r.setup_hour_details = setup_details
    r.setup_manhours = round(hrs, 2)

    # Productivity rates are TASK rates — how long each task takes the team.
    # Tasks are mostly sequential (can't lay reo while screeding sand),
    # so total setup time = sum of all task durations. No buffer applied.
    crew_count = max(1, inp.setup_crew_count)
    r.setup_hours = hrs
    
    # ==========================================================================
    # POUR HOURS (with per-task breakdown)
    # ==========================================================================
    pour_details = []
    minimum_hrs = POUR_RATES["minimum_hours"]

    # 11 minutes per m² — flat rate regardless of season
    pour_base = max(inp.slab_area * POUR_MINUTES_PER_SQM / 60, minimum_hrs)
    pour_thickness_factor = 1 + (thickness_factor - 1) * 0.3
    pour_base *= pour_thickness_factor
    pour_details.append({"task": "Pour & Finish", "hours": round(pour_base, 2)})

    if additive_time_effect != 0:
        additive_hrs = additive_time_effect / 60
        pour_base += additive_hrs
        pour_details.append({"task": f"Additive Effect ({inp.mix_additive})", "hours": round(additive_hrs, 2)})

    # Screeding & tooled joints are included in the 11 min/m² pour rate.
    # Tooled joints only add material cost ($3/lm), not extra time.
    # Only sawcutting adds separate time (done next day).

    # No complexity buffer on pour - concrete set time is dictated by weather, not site
    r.pour_hours = pour_base

    # Falls complexity — extra time for complex drainage falls to pits etc.
    if inp.falls_complexity_pct > 0:
        falls_mult = 1 + (inp.falls_complexity_pct / 100)
        r.pour_hours *= falls_mult

    r.pour_hour_details = pour_details

    # Sawcutting (separate day)
    if inp.control_joint_method == "Sawcut" and inp.control_joints > 0:
        sawcut_setup = (PRODUCTIVITY["sawcut_setup_min"] + PRODUCTIVITY["sawcut_setup_max"]) / 2 / 60
        r.sawcut_hours = sawcut_setup + safe_div(inp.control_joints, PRODUCTIVITY["sawcut"])
        r.show_sawcut = True

    # ==========================================================================
    # FINISH HOURS (with per-task breakdown)
    # ==========================================================================
    finish_details = []
    finish_hrs = 0

    def _add_finish(task_name, task_hrs):
        nonlocal finish_hrs
        if task_hrs > 0:
            finish_hrs += task_hrs
            finish_details.append({"task": task_name, "hours": round(task_hrs, 2)})

    if inp.wash_off == "Next Day":
        _add_finish("Wash Off (Next Day)", safe_div(inp.slab_area, PRODUCTIVITY["wash_next"]))
        r.show_exposed = True
    elif inp.wash_off == "Same Day":
        _add_finish("Wash Off (Same Day)", safe_div(inp.slab_area, PRODUCTIVITY["wash_same"]))
        r.show_exposed = True

    if inp.acid_wash:
        _add_finish("Acid Wash", safe_div(inp.slab_area, PRODUCTIVITY["acid_wash"]))
        r.show_exposed = True

    if inp.inc_curing_compound:
        _add_finish("Curing Spray", safe_div(inp.slab_area, PRODUCTIVITY["curing_spray"]))

    if inp.inc_sealer:
        _add_finish("Sealer", safe_div(inp.slab_area, PRODUCTIVITY["sealer"]))

    # Raw task hours (no buffer)
    r.finish_hours = finish_hrs

    # Falls complexity also affects finishing
    if inp.falls_complexity_pct > 0:
        falls_mult = 1 + (inp.falls_complexity_pct / 100)
        r.finish_hours *= falls_mult

    r.finish_hour_details = finish_details

    # ==========================================================================
    # SETUP MATERIALS
    # ==========================================================================
    mat = 0
    
    # Reinforcement
    reo_cost = reo.get("cost", 0) * inp.slab_area
    r.reo_cost_cents = int(reo_cost)
    mat += reo_cost
    
    if "Mesh" in inp.reinforcement:
        mat += STEEL_DELIVERY
    
    # Dowels
    if r.dowel_count > 0:
        dowel_rate = get_dowel_rate(inp.reinforcement)
        dowel_cost = int(dowel_rate * inp.dowel_bars)
        mat += dowel_cost
    
    # Moisture barrier
    if inp.inc_moisture_barrier:
        mat += int(MATERIALS["moisture_barrier"] * inp.slab_area)
    
    # Isolation joints
    if inp.inc_isolation_joint and inp.isolation_joints > 0:
        mat += int(MATERIALS["isolation_joint"] * inp.isolation_joints)
    
    # Fence sheeting
    if inp.fence_sheeting > 0:
        mat += int(MATERIALS["fence_sheeting"] * inp.fence_sheeting)
    
    # Formwork wear
    if inp.inc_formwork_wear:
        mat += int(MATERIALS["formwork_depreciation"] * total_formwork)
    
    # Release agent
    if inp.inc_release_agent:
        mat += int(CHEMICALS["Release Agent"] * inp.slab_area)
    
    # Steps
    if inp.steps > 0:
        mat += int(MATERIALS["step_materials"] * inp.steps)

    # Rebates (starter bars / tie-ins)
    if inp.rebates > 0:
        mat += int(MATERIALS["rebate_per_lm"] * inp.rebates)

    # Pier holes
    if inp.pier_holes > 0:
        mat += r.pier_cost_cents

    # Edge beams
    if inp.edge_beams and inp.edge_beam_length > 0:
        mat += r.edge_beam_cost_cents

    # Subbase already added
    mat += r.subbase_cost_cents

    r.setup_materials_cents = mat
    
    # ==========================================================================
    # POUR MATERIALS
    # ==========================================================================
    mat = r.concrete_cost_cents
    
    # Pump
    if inp.pump_required:
        pump_cost = int(MATERIALS["pump_per_sqm"] * inp.slab_area)
        mat += pump_cost
        
        r.line_items.append(LineItem(
            description="Concrete pump hire",
            quantity=inp.slab_area,
            unit="m²",
            unit_price_cents=MATERIALS["pump_per_sqm"],
            total_cents=pump_cost,
            category="other",
        ))
    
    # Chemicals
    if inp.inc_evap_retarder:
        mat += int(CHEMICALS["Evap Retarder"] * inp.slab_area)
    
    if inp.inc_durability_enhancer:
        mat += int(CHEMICALS["Durability Enhancer"] * inp.slab_area)
    
    if inp.inc_surface_retarder:
        mat += int(CHEMICALS["Surface Retarder"] * inp.slab_area)
    
    if inp.inc_curing_compound:
        mat += int(CHEMICALS["Curing Compound"] * inp.slab_area)
    
    if inp.inc_sealer:
        mat += int(CHEMICALS["Sealer"] * inp.slab_area)
    
    # Equipment wear
    mat += int(CHEMICALS["Equipment"] * inp.slab_area)
    
    # Control joints (use override rate if provided, else default from pricing)
    if inp.control_joints > 0:
        if inp.control_joint_rate > 0:
            cj_rate = inp.control_joint_rate
        elif inp.control_joint_method == "Sawcut":
            cj_rate = CONTROL_JOINTS["sawcut"]
        else:
            cj_rate = CONTROL_JOINTS["tooled"]
        mat += int(cj_rate * inp.control_joints)
        r.control_joint_rate_used = cj_rate

    r.pour_materials_cents = mat
    
    # ==========================================================================
    # FINISH MATERIALS
    # ==========================================================================
    if inp.wash_off != "N/A":
        # Pressure washer hire
        half_day = inp.slab_area <= 60
        r.finish_materials_cents = 15000 if half_day else 22000  # cents

    # Acid wash chemical cost ($3/m² = 300 cents/m²)
    if inp.acid_wash and inp.slab_area > 0:
        r.finish_materials_cents += int(CHEMICALS.get("Acid Wash", 300) * inp.slab_area)
    
    # ==========================================================================
    # LABOUR
    # ==========================================================================
    team = TEAM_RATES.get(inp.team_tier, TEAM_RATES["Standard"])
    # Use settings-sourced team tier rates if pricing dict available
    if pricing:
        team = {
            "hourly": pricing.get(
                f"team_{inp.team_tier.lower()}_hourly",
                team["hourly"],
            ),
            "per_sqm": pricing.get(
                f"team_{inp.team_tier.lower()}_sqm",
                team.get("per_sqm", 0),
            ),
            "per_worker": team.get("per_worker", 6000),
            "base_crew": team.get("base_crew", 3),
        }

    # Scale setup hourly rate by crew size.
    # Full crew (3) = team["hourly"]. Fewer workers = lower rate.
    # E.g. Standard: 3 guys=$220/hr, 2 guys=$160/hr, 1 guy=$100/hr
    base_crew = team.get("base_crew", 3)
    per_worker = team.get("per_worker", 6000)
    crew_count = max(1, inp.setup_crew_count)
    crew_diff = base_crew - crew_count
    scaled_setup_rate = max(per_worker, team["hourly"] - crew_diff * per_worker)

    # Use direct hourly rates if provided, else use crew-scaled team rates
    setup_hourly = inp.setup_hourly_rate if inp.setup_hourly_rate > 0 else scaled_setup_rate
    pour_hourly = inp.pour_hourly_rate if inp.pour_hourly_rate > 0 else team["hourly"]
    finish_hourly = pour_hourly
    
    r.setup_labour_cents = int(r.setup_hours * setup_hourly)
    r.pour_labour_cents = int(r.pour_hours * pour_hourly)
    r.finish_labour_cents = int(r.finish_hours * finish_hourly)
    
    # Complexity multiplier
    complexity_mult = COMPLEXITY_MULTIPLIERS.get(inp.complexity, 1.0)
    r.setup_labour_cents = int(r.setup_labour_cents * complexity_mult)
    r.pour_labour_cents = int(r.pour_labour_cents * complexity_mult)
    r.finish_labour_cents = int(r.finish_labour_cents * complexity_mult)
    
    r.labour_sell_cents = r.setup_labour_cents + r.pour_labour_cents + r.finish_labour_cents
    
    # Calculate actual cost
    total_hours = r.setup_hours + r.pour_hours + r.finish_hours
    if inp.setup_cost_rate > 0 or inp.pour_cost_rate > 0:
        setup_cost = inp.setup_cost_rate if inp.setup_cost_rate > 0 else 0
        pour_cost = inp.pour_cost_rate if inp.pour_cost_rate > 0 else 0
        finish_cost = pour_cost
        
        r.labour_cost_cents = int((r.setup_hours * setup_cost + 
                                   r.pour_hours * pour_cost + 
                                   r.finish_hours * finish_cost) * complexity_mult)
    else:
        team_cost = get_team_cost_hourly(pricing)
        r.labour_cost_cents = int(total_hours * team_cost)
    
    r.labour_margin_cents = r.labour_sell_cents - r.labour_cost_cents
    
    # Add labour line item
    r.line_items.append(LineItem(
        description="Labour - concrete placement and finishing",
        quantity=round(total_hours, 1),
        unit="hrs",
        unit_price_cents=int(r.labour_sell_cents / total_hours) if total_hours > 0 else 0,
        total_cents=r.labour_sell_cents,
        category="labour",
    ))
    
    # Sawcut labour (separate) - $150/hr + $25/hr consumables for contraction joints
    sawcut_labour_cents = 0
    sawcut_consumables_cents = 0
    if r.show_sawcut:
        sawcut_labour_cents = int(r.sawcut_hours * CONCRETE_REMOVAL["labour_rate_hr"])
        sawcut_consumables_cents = int(r.sawcut_hours * CONCRETE_REMOVAL["sawcut_joint_consumables_hr"])
    
    # ==========================================================================
    # OVERHEAD & TRAVEL
    # ==========================================================================
    r.overhead_cents = calculate_overhead(inp.slab_area)
    
    chargeable_km = max(0, inp.distance_km - TRAVEL["free_km"])
    if chargeable_km > 0:
        r.travel_cents = int(chargeable_km * TRAVEL["rate"])
        r.show_travel = True
        # Travel cost is spread into the total (no separate line item)
    
    # ==========================================================================
    # TOTALS WITH TIERED MARKUP
    # ==========================================================================
    total_materials = r.setup_materials_cents + r.pour_materials_cents + r.finish_materials_cents + sawcut_consumables_cents
    total_labour = r.labour_sell_cents + sawcut_labour_cents
    
    # Calculate tiered markup
    pricing_tier = inp.tier
    total_markup = 0
    
    # Markup on materials (varies by item cost)
    material_items = [
        (int(reo.get("cost", 0) * inp.slab_area), reo.get("cost", 671)),
        (r.subbase_cost_cents, MATERIALS["subbase_sand"]),
        (r.concrete_cost_cents, get_concrete_price(inp.concrete_grade)),
    ]
    
    for item_total, unit_cost in material_items:
        if item_total > 0 and unit_cost > 0:
            markup_pct = get_material_markup(unit_cost, pricing_tier)
            total_markup += int(item_total * markup_pct)
    
    # Markup on other materials
    other_materials = total_materials - sum(item[0] for item in material_items if item[0] > 0)
    if other_materials > 0:
        total_markup += int(other_materials * get_material_markup(500, pricing_tier))
    
    r.markup_cents = total_markup
    
    r.raw_cost_cents = (r.excavation_cost_cents + r.removal_cost_cents +
                        r.drainage_cost_cents +
                        total_materials + total_labour +
                        r.overhead_cents + r.travel_cents)
    
    r.subtotal_cents = r.raw_cost_cents + r.markup_cents

    # Customer discount (applied before GST)
    if inp.customer_discount_percent > 0:
        r.discount_cents = int(round(r.subtotal_cents * inp.customer_discount_percent / 100))

    net_subtotal = r.subtotal_cents - r.discount_cents
    r.gst_cents = int(round(net_subtotal * GST_RATE))
    r.total_cents = net_subtotal + r.gst_cents
    
    # Minimum quote
    if r.total_cents > 0 and r.total_cents < MINIMUM_QUOTE:
        r.minimum_applied = True
        r.original_total_cents = r.total_cents
        r.total_cents = MINIMUM_QUOTE
        r.subtotal_cents = int(round(MINIMUM_QUOTE / (1 + GST_RATE)))
        r.gst_cents = MINIMUM_QUOTE - r.subtotal_cents
    
    # Rate per m²
    if inp.slab_area > 0:
        r.rate_per_sqm_cents = int(r.total_cents / inp.slab_area)
    
    # Profit
    r.profit_cents = r.markup_cents + r.labour_margin_cents
    
    # ==========================================================================
    # PAYMENT SCHEDULE
    # ==========================================================================
    
    # Payment schedule: 30 / 60 / 10 for ALL jobs
    deposit_percent = 0.30
    progress_percent = 0.60
    final_percent = 0.10

    deposit_amount = int(r.total_cents * deposit_percent)
    progress_amount = int(r.total_cents * progress_percent)
    final_amount = r.total_cents - deposit_amount - progress_amount  # remainder

    r.payments.append(Payment(
        name="First Payment (30%)",
        percent=deposit_percent,
        amount_cents=deposit_amount,
    ))
    r.payments.append(Payment(
        name="Progress Payment (60%)",
        percent=progress_percent,
        amount_cents=progress_amount,
    ))
    r.payments.append(Payment(
        name="Final Payment (10%)",
        percent=final_percent,
        amount_cents=final_amount,
    ))
    
    return r


# ==============================================================================
# CONVENIENCE FUNCTION FOR SCHEMA CONVERSION
# ==============================================================================

def calculate_quote(input_data: dict, pricing: dict = None) -> dict:
    """
    Calculate quote from dictionary input.

    This is the main entry point for the API.
    Accepts an optional pricing dict from DB settings for crew/team rates.
    """
    inp = CalculatorInput(**{k: v for k, v in input_data.items() if hasattr(CalculatorInput, k)})
    result = calculate(inp, pricing=pricing)
    return result.to_dict()
