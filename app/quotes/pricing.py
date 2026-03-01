"""
================================================================================
CONCRETEIQ - PRICING CONFIGURATION
================================================================================
ALL PRICES IN CENTS (ex GST unless noted)
Last Updated: February 2026

NOTE: These hardcoded values are defaults. The settings module allows
admin configuration via database. Use get_pricing_async() to get
values from the database (falls back to these defaults).
================================================================================
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# ==============================================================================
# CONCRETE SUPPLY (cents per m³ ex GST)
# ==============================================================================
CONCRETE_PRICES = {
    "N20": 23300,
    "N25": 23800,
    "N32": 24600,
    "Exposed": 27300,
}

CONCRETE_FREE_KM = 20
CONCRETE_TRAVEL_RATE = 150  # cents per km per m³

SHORT_LOAD_THRESHOLD = 3.0  # m³
SHORT_LOAD_FEE_PER_M3 = 5000  # cents ($50/m³)

CONCRETE_BUFFER = 0.08  # 8% wastage

# ==============================================================================
# CEMENT CONTENT PER GRADE (kg cementitious per m³)
# Used for weight-based retarder pricing (litres = cement_kg/1000 × dosage%)
# ==============================================================================
CEMENT_CONTENT_PER_GRADE = {
    "N20": 310,
    "N25": 340,
    "N32": 400,
}

# ==============================================================================
# RETARDER PRICING — $5.00 per litre
# litres_per_m3 = (cement_kg / 1000) × dosage%
# cost_per_m3 = litres × $5.00/L
# ==============================================================================
RETARDER_COST_PER_LITRE = 500  # cents ($5.00/L)

# ==============================================================================
# MIX ADDITIVES (cents per m³ ex GST)
# ==============================================================================
MIX_ADDITIVES = {
    "None": {"cost": 0, "time_mins": 0},
    "Accelerator 1%": {"cost": 600, "time_mins": -30},
    "Accelerator 2%": {"cost": 1200, "time_mins": -45},
    "Accelerator 3%": {"cost": 1800, "time_mins": -75},
    "Retarder 1%": {"cost": 0, "time_mins": 30, "weight_based": True, "percentage": 1},
    "Retarder 2%": {"cost": 0, "time_mins": 45, "weight_based": True, "percentage": 2},
    "Retarder 3%": {"cost": 0, "time_mins": 75, "weight_based": True, "percentage": 3},
}

# ==============================================================================
# COLOURED CONCRETE (surcharge in cents per m³ ex GST, on top of any grade)
# ==============================================================================
CONCRETE_COLOURS = {
    "CHARCOAL": 5000,
    "CINNAMON": 4266,
    "CORNSTALK": 4594,
    "DAINTREE": 4758,
    "COPPER": 4922,
    "BLUESTONE": 5625,
    "EARTH BROWN": 5742,
    "CHARDONNAY": 5906,
    "CLASSIC SUEDE": 6070,
    "BILLABONG": 6234,
    "KOSCIUSKO": 6234,
    "BUTTERSCOTCH": 6563,
    "ESPRESSO": 6563,
    "BRANDY SNAP": 6891,
    "GUM LEAF": 6891,
    "DYNASTY": 7109,
    "GOANNA": 7266,
    "HIDDEN VALLEY": 7547,
    "DUSTY ROSE": 7711,
    "BEACH": 7711,
    "BRAZIL NUT": 8039,
    "CHOC MALT": 8695,
    "FLOWER GUM": 9188,
    "COLONIAL SUNSET": 9844,
    "BLACK": 10000,
    "DESERT BEIGE": 10336,
    "BRONZED AUSSIE": 11484,
    "HAWKESBURY": 11813,
    "BRICK": 12797,
    "BROLGA": 13125,
    "ANTIQUE ROSE": 15422,
    "JADE GREEN": 15422,
}

# ==============================================================================
# CONCRETE FIBRES (cents per m³ ex GST)
# ==============================================================================
CONCRETE_FIBRES = {
    "None": 0,
    "Polypropylene": 2000,   # $20/m³
    "Steel": 10000,          # $100/m³
    "Glass": 3000,           # $30/m³
}

# ==============================================================================
# REINFORCEMENT (all-in cents per m² ex GST - includes bars/mesh + chairs + ties)
# ==============================================================================
REINFORCEMENT = {
    "GFRP 500mm": {"cost": 1052, "rate": 18.2, "spacing": 500},
    "GFRP 450mm": {"cost": 1182, "rate": 15.0, "spacing": 450},
    "GFRP 400mm": {"cost": 1355, "rate": 12.0, "spacing": 400},
    "GFRP 350mm": {"cost": 1598, "rate": 9.2, "spacing": 350},
    "SL72 Mesh": {"cost": 659, "rate": 25, "spacing": 200},
    "SL82 Mesh": {"cost": 815, "rate": 25, "spacing": 200},
    "SL92 Mesh": {"cost": 987, "rate": 24, "spacing": 200},
    "SL102 Mesh": {"cost": 1414, "rate": 23, "spacing": 200},
    "None": {"cost": 0, "rate": 0, "spacing": 0},
}

# ==============================================================================
# DOWELS (cents per lineal metre ex GST)
# ==============================================================================
DOWELS = {
    "GFRP 350mm": 137,
    "GFRP 400mm": 120,
    "GFRP 450mm": 107,
    "GFRP 500mm": 96,
    "Steel 400mm": 325,
}

STEEL_DELIVERY = 5000  # cents ($50)

# ==============================================================================
# CONTROL JOINTS (cents per lm ex GST - includes labour)
# ==============================================================================
CONTROL_JOINTS = {
    "sawcut": 800,    # $8/lm
    "tooled": 300,    # $3/lm
}

# ==============================================================================
# CHEMICALS (cents per m² ex GST)
# ==============================================================================
CHEMICALS = {
    "Release Agent": 30,
    "Evap Retarder": 75,
    "Curing Compound": 217,
    "Durability Enhancer": 280,
    "Surface Retarder": 205,
    "Sealer": 300,
    "Acid Wash": 300,
    "Equipment": 10,
}

# ==============================================================================
# MATERIALS (cents ex GST)
# ==============================================================================
MATERIALS = {
    "moisture_barrier": 60,        # cents per m²
    "isolation_joint": 207,        # cents per lm
    "fence_sheeting": 700,         # cents per lm
    "formwork_depreciation": 30,   # cents per lm
    "subbase_sand": 354,           # cents per m² (at 37.5mm base depth)
    "subbase_delivery_flat": 5000, # cents flat fee ($50) for ≤20km from Peards
    "subbase_delivery_radius_km": 20,  # km — free delivery radius
    "subbase_delivery_per_km": 150,    # cents per km beyond radius ($1.50/km)
    "pump_per_sqm": 3273,          # cents per m²
    "step_materials": 7727,        # cents per step
    "step_hours": 1.5,             # hours per step
    "rebate_per_lm": 1700,        # cents per lm - formwork + reo for stepped edge
    "pier_sono_tube": 2000,        # cents per pier - cardboard form tube
    "pier_starter_bar": 700,       # cents per bar - N12 reo
    "pier_labour_hrs": 0.6,        # hours per pier - dig, form, pour
    "edge_beam_reo": 1000,         # cents per lm - ligatures + bars
    # Plumbing & Drainage
    "drain_pit_300": 43750,
    "drain_pit_450": 68750,
    "drain_centralising_pit": 56250,
    "drain_grate_standard": 22500,
    "drain_grate_heavy": 35000,
    "drain_surface_drain_lm": 15000,
    "drain_ag_pipe_90_lm": 4375,
    "drain_stormwater_100_lm": 6875,
    "drain_stormwater_150_lm": 9375,
    "drain_tpiece_junction": 10625,
    "drain_trench_lm": 5625,
    "drain_gravel_lm": 3125,
    "drain_relocate": 43750,
    "drain_labour_hr": 12500,
}

# Plate compactor specs
PLATE_COMPACTOR = {
    "purchase_price": 180000,
    "years_owned": 5,
    "total_lifespan": 20,
    "fuel_per_sqm": 0.015,
    "fuel_price": 200,
    "annual_service": 30000,
    "jobs_per_year": 100,
}

# ==============================================================================
# EQUIPMENT HIRE (cents ex GST)
# ==============================================================================
# 1.7t Excavator
EXCAVATOR_HIRE_HALF_DAY = 24860   # $273.46 inc GST → $248.60 ex GST
EXCAVATOR_HIRE_FULL_DAY = 35750   # $393.25 inc GST → $357.50 ex GST
EXCAVATOR_FUEL_PER_HR = 550       # ~3L/hr × $1.77/L ≈ $5.50/hr (diesel)

# Pressure Washer
PRESSURE_WASHER_HALF_DAY = 13909  # $153.00 inc GST → $139.09 ex GST
PRESSURE_WASHER_FULL_DAY = 19818  # $218.00 inc GST → $198.18 ex GST

# ==============================================================================
# DISPOSAL — SKIP BIN (charge rates, cents ex GST, minimum 3m³)
# ==============================================================================
SKIPBIN_MINIMUM_M3 = 3.0

# Soil: cost $124/m³ → charge $160/m³ ex GST ($176 inc)
SKIPBIN_SOIL_PER_M3 = 16000

# Concrete: cost $259/m³ → charge $330/m³ ex GST ($363 inc)
SKIPBIN_CONCRETE_PER_M3 = 33000

# ==============================================================================
# DISPOSAL — TRAILER (own trailer, per load, cents ex GST)
# ==============================================================================
TRAILER_CAPACITY_M3 = 0.5

# Soil: tip $10 + unloading $84 + fuel $20 = $114/load
TRAILER_SOIL_PER_LOAD = 11400

# Concrete: tip $63 + unloading $84 + fuel $20 = $167/load
TRAILER_CONCRETE_PER_LOAD = 16700

# Waste tip destinations (address options for trailer disposal)
WASTE_TIP_DESTINATIONS = {
    "jacksons": "Jacksons Wodonga, 17 Kendall St, Wodonga",
    "albury_waste": "Albury Waste Management, 565 Mudge St, Lavington",
    "wodonga_transfer": "Wodonga Waste Transfer, 29 Kane Rd, Wodonga",
}

# ==============================================================================
# PRODUCTIVITY RATES (m² or lm per hour)
# ==============================================================================
PRODUCTIVITY = {
    # --- Setup Tasks ---
    "boxing": 19,                     # lm/hr - erect formwork + apply form oil
    "sand_compact": 10,               # m²/hr - subbase prep, barrow, screed
    "plate_compaction": 20,           # m²/hr (3 min/m²) - optional compaction
    "fence_sheeting": 20,             # lm/hr

    # --- Excavation ---
    "hand_dig": 0.38,                 # m³/hr - hand digging
    "machine_dig": 1.0,               # m³/hr - 1.7t excavator

    # --- Sawcutting ---
    "sawcut": 25,                     # lm/hr - contraction/control joints
    "sawcut_setup_min": 15,           # minutes - setup time (low end)
    "sawcut_setup_max": 30,           # minutes - setup time (high end)

    # --- Joints & Edge Treatments ---
    "isolation_joint": 30,            # lm/hr - ableflex
    "rebates": 10,                    # lm/hr - drilling & epoxy insertion
    "dowels": 30,                     # dowels/hr (mark, drill & install)

    # --- Concrete Underlay / Vapour Barrier ---
    "moisture_barrier": 100,          # m²/hr - roll out only
    "moisture_barrier_full": 30,      # m²/hr (2 min/m²) - full install inc taping

    # --- Curing & Sealing ---
    "curing_spray": 125,              # m²/hr
    "wash_same": 25,                  # m²/hr (same day wash)
    "wash_next": 30,                  # m²/hr (next day wash)
    "acid_wash": 40,                  # m²/hr
    "sealer": 50,                     # m²/hr (per coat)
}

# ==============================================================================
# CONCRETE REMOVAL PRICING (cents ex GST)
# ==============================================================================
CONCRETE_REMOVAL = {
    # Sawcutting rates
    "sawcut_rate_lm_hr": 12,
    "sawcut_consumables_hr": 3300,
    "sawcut_joint_consumables_hr": 2500,
    "labour_rate_hr": 15000,

    # Grid cut spacing by method (mm)
    "manual_strip_width": 350,
    "machine_strip_width": 800,

    # Loading & carting rates (m³/hr)
    "manual_loading_rate": 1.5,
    "machine_loading_rate": 4.0,

    # Machine cost (excavator hire for removal day)
    "machine_day_rate": 60000,
    "machine_capacity_m3_day": 20,

    # Loading labour rate (same crew)
    "loading_labour_rate_hr": 15000,

    # Reinforced concrete penalties
    "reinforced_sawcut_penalty": 0.30,
    "reinforced_removal_penalty": 0.20,
}

# ==============================================================================
# POUR RATES
# ==============================================================================
# Flat 11 minutes per m² (includes pour, spread, finish)
POUR_MINUTES_PER_SQM = 11

POUR_RATES = {
    "minimum_hours": 6.0,
}

# Average monthly temperatures (Albury-Wodonga region)
MONTHLY_TEMPS = {
    1: 32, 2: 31, 3: 27, 4: 22, 5: 17.5, 6: 14,
    7: 13, 8: 15, 9: 18, 10: 21, 11: 26, 12: 30,
}

SUMMER_TEMP_THRESHOLD = 22

def get_season_from_month(month: int) -> str:
    """Determine season based on average monthly temperature."""
    temp = MONTHLY_TEMPS.get(month, 20)
    return "Summer" if temp > SUMMER_TEMP_THRESHOLD else "Winter"

def is_summer_month(month: int) -> bool:
    """Check if month uses summer pour rates."""
    return MONTHLY_TEMPS.get(month, 20) > SUMMER_TEMP_THRESHOLD

# ==============================================================================
# COMPLEXITY
# ==============================================================================
COMPLEXITY_MULTIPLIERS = {
    "Easy": 0.90,
    "Standard": 1.00,
    "Complex": 1.15,
    "Very Complex": 1.30,
}

# ==============================================================================
# CREW RATES (cents per hour)
# ==============================================================================
CREW_ROLES = {
    "owner": {
        "base_rate": 9400,      # $94/hr — Head Concretor (Kyle)
        "casual_rate": 9400,    # No casual loading (owner)
        "loaded_rate": 9400,    # $94/hr loaded
        "min_sell_rate": 9400,  # $94/hr sell rate
    },
    "finisher": {
        "base_rate": 3463,      # $34.63/hr — Concretor
        "casual_rate": 4329,    # base_rate * 1.25 (25% casual loading per Award)
        "loaded_rate": 4514,    # $45.14/hr loaded (super + WC + PAYG)
        "min_sell_rate": 5450,  # $54.50/hr sell rate
    },
    "exp_labourer": {
        "base_rate": 3398,      # $33.98/hr — Experienced Labourer
        "casual_rate": 4248,    # base_rate * 1.25 (25% casual loading per Award)
        "loaded_rate": 4433,    # $44.33/hr loaded (super + WC + PAYG)
        "min_sell_rate": 5350,  # $53.50/hr sell rate
    },
    "labourer": {
        "base_rate": 3238,      # $32.38/hr — Labourer
        "casual_rate": 4048,    # base_rate * 1.25 (25% casual loading per Award)
        "loaded_rate": 4235,    # $42.35/hr loaded (super + WC + PAYG)
        "min_sell_rate": 5100,  # $51.00/hr sell rate
    },
}

ROLE_TO_SETTINGS_PREFIX = {
    "owner": "crew_owner",
    "finisher": "crew_finisher",
    "exp_labourer": "crew_exp_labourer",
    "labourer": "crew_labourer",
}

# Team tier pricing (cents per hour / cents per m²)
TEAM_RATES = {
    "Lean": {"hourly": 14500, "per_worker": 3500, "base_crew": 3, "per_sqm": 3200},
    "Standard": {"hourly": 14750, "per_worker": 4000, "base_crew": 3, "per_sqm": 3300},
    "Premium": {"hourly": 16500, "per_worker": 4500, "base_crew": 3, "per_sqm": 3700},
}

# ==============================================================================
# MATERIAL MARKUP TIERS
# ==============================================================================
MATERIAL_MARKUP_TIERS = [
    {"max": 500, "Economy": 0.25, "Standard": 0.30, "Premium": 0.35},
    {"max": 1500, "Economy": 0.20, "Standard": 0.25, "Premium": 0.30},
    {"max": 5000, "Economy": 0.15, "Standard": 0.20, "Premium": 0.25},
    {"max": 15000, "Economy": 0.125, "Standard": 0.15, "Premium": 0.185},
    {"max": 99999999, "Economy": 0.08, "Standard": 0.10, "Premium": 0.125},
]

# ==============================================================================
# OVERHEAD & TRAVEL
# ==============================================================================
OVERHEAD = {
    "insurance": 60,
    "xero": 20,
    "software": 10,
    "equipment": 5,
    "accountant": 15,
    "base_area": 30,
    "minimum": 150,
}

TRAVEL = {
    "free_km": 0,
    "rate": 158,  # cents per km (vehicle + driving time)
}

# ==============================================================================
# GST & PAYROLL
# ==============================================================================
GST_RATE = 0.10
SUPER_RATE = 0.12  # 12% super guarantee
PAYG_RATE = 0.17
WORKCOVER_RATE = 0.085

# ==============================================================================
# MINIMUM QUOTE
# ==============================================================================
MINIMUM_QUOTE = 275000  # cents inc GST ($2,750)

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def get_concrete_price(grade: str) -> int:
    """Get concrete price in cents per m³."""
    if "exposed" in grade.lower():
        return CONCRETE_PRICES.get("Exposed", 27300)
    match = re.search(r'N\d+', grade)
    if match:
        return CONCRETE_PRICES.get(match.group(), CONCRETE_PRICES["N32"])
    return CONCRETE_PRICES.get(grade, CONCRETE_PRICES["N32"])


def get_reinforcement(reo_type: str) -> dict:
    """Get reinforcement data."""
    return REINFORCEMENT.get(reo_type, REINFORCEMENT["None"])


def get_dowel_spacing(reo_type: str) -> int:
    """Get dowel spacing for reinforcement type."""
    if "Mesh" in reo_type or "SL" in reo_type:
        return 400
    reo = REINFORCEMENT.get(reo_type, {})
    return reo.get("spacing", 400)


def get_dowel_rate(reo_type: str) -> int:
    """Get dowel rate in cents per lm."""
    spacing = get_dowel_spacing(reo_type)
    if "Mesh" in reo_type:
        return DOWELS.get("Steel 400mm", 325)
    else:
        key = f"GFRP {spacing}mm"
        return DOWELS.get(key, 107)


def get_material_markup(raw_cost_cents: int, tier: str = "Standard") -> float:
    """Get markup percentage for a material based on raw cost."""
    for t in MATERIAL_MARKUP_TIERS:
        if raw_cost_cents <= t.get("max", 99999999):
            return t.get(tier, t.get("Standard", 0.15))
    return 0.15


def calculate_overhead(area: float) -> int:
    """Calculate overhead in cents."""
    oh = OVERHEAD
    base = oh["insurance"] + oh["xero"] + oh["software"] + oh["equipment"] + oh.get("accountant", 0)
    per_sqm = max(oh["minimum"], base)
    return int(per_sqm * max(area, oh["base_area"]))


def get_team_cost_hourly(pricing: dict = None) -> int:
    """Get full team cost per hour in cents."""
    if pricing:
        total = 0
        for role, prefix in ROLE_TO_SETTINGS_PREFIX.items():
            total += pricing.get(
                f"{prefix}_loaded",
                CREW_ROLES.get(role, {}).get("loaded_rate", 0),
            )
        return total
    return sum(r.get("loaded_rate", 0) for r in CREW_ROLES.values())


# =============================================================================
# ASYNC SETTINGS ACCESS
# =============================================================================

async def get_pricing_async(db: "AsyncSession") -> dict:
    """Get all pricing settings from database (falls back to hardcoded defaults)."""
    from app.settings.service import get_settings_by_category
    return await get_settings_by_category(db, 'pricing')


async def get_crew_rates_async(db: "AsyncSession") -> dict:
    """Get crew rates from DB settings, keyed by worker role."""
    pricing = await get_pricing_async(db)
    rates = {}
    for role, prefix in ROLE_TO_SETTINGS_PREFIX.items():
        rates[role] = {
            "base": pricing.get(f"{prefix}_base", CREW_ROLES.get(role, {}).get("base_rate", 0)),
            "loaded": pricing.get(f"{prefix}_loaded", CREW_ROLES.get(role, {}).get("loaded_rate", 0)),
            "sell": pricing.get(f"{prefix}_sell", CREW_ROLES.get(role, {}).get("min_sell_rate", 0)),
        }
    return rates
