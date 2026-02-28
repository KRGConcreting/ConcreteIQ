"""
Sika Australia Product Database for P & J Nagle Concrete (Howlong).

All data sourced from Sika Australia Technical Data Sheets and AS 1478.1-2000.

Product hierarchy:
  - PLANT STANDARD: Plastiment 45 (summer) / ECO-3W (winter) / Air LS (most mixes)
    → These are already in the concrete when it arrives on-site
  - USER SPECIFIED: Retarder N (hot weather) / Rapid AF (cold weather)
    → You tell the plant what % to add

Season detection: weighted effective temp ≥ 20°C = summer mix, < 20°C = winter mix.
"""

from typing import Dict, Tuple, List, Any


# =============================================================================
# PRODUCT DEFINITIONS
# =============================================================================

# Dosing tables: (min_temp, max_temp) -> (min_dose, max_dose)
# Dose units are mL/100kg cementitious unless noted otherwise

PRODUCTS: Dict[str, Dict[str, Any]] = {

    # -----------------------------------------------------------------
    # SikaRapid AF — Non-chloride set accelerator
    # Classification: Ac per AS 1478.1-2000
    # User-specified: you tell the plant what % to add
    # -----------------------------------------------------------------
    "SikaRapid AF": {
        "short_name": "Rapid AF",
        "as1478_class": "Ac",
        "density_kg_L": 1.42,
        "dose_unit": "mL/100kg",
        "dose_min": 400,       # Sika minimum — CRITICAL: 1 L/m³ for 350kg mix = 286 mL/100kg, BELOW this!
        "dose_max": 2000,
        "who_controls": "user",
        "set_time_effect": "accelerate",
        "temp_dose_table": {
            (15, 20): (400, 600),
            (10, 15): (600, 1000),
            (5, 10): (1000, 1400),
            (-10, 5): (1400, 2000),
        },
        "notes": [
            "Non-chloride formulation",
            "Add BEFORE water reducer or superplasticiser",
            "If added on-site to agitator truck, mix additional 1 min/m³",
            "AS 1478.1 Ac: ≥1 hr initial set reduction, ≥125% 1-day strength",
        ],
        "warnings": {
            "below_5": "Below 5°C: SikaRapid AF alone is insufficient. Use heated water + insulated formwork.",
            "dosage_low": "Dose may be below Sika minimum (400 mL/100kg). Check with plant.",
        },
        "addition_order": 2,
        "addition_instruction": "Add with initial batching water, before water reducer",
    },

    # -----------------------------------------------------------------
    # Sika Plastiment 45 — PCE water reducer (summer mixes)
    # Classification: WR per AS 1478.1-2000
    # Plant standard: already in summer concrete mixes
    # -----------------------------------------------------------------
    "Sika Plastiment 45": {
        "short_name": "Plastiment 45",
        "as1478_class": "WR",
        "density_kg_L": 1.06,
        "dose_unit": "mL/100kg",
        "dose_min": 250,
        "dose_max": 600,
        "dose_typical": 400,     # Typical plant dose for summer mixes
        "who_controls": "plant",
        "set_time_effect": "mild_retard",
        "season": "summer",      # Plant uses this in summer mixes
        "temp_dose_table": {
            (30, 50): (400, 600),    # Hot: push dose higher for slump retention
            (25, 30): (350, 500),
            (20, 25): (250, 450),    # Standard summer range
            (15, 20): (250, 350),
            (5, 15): (200, 300),     # Cold: reduce dose (retarding side-effect more pronounced)
        },
        "water_reduction_percent": (5, 12),  # 5-10% at standard, up to 12% at 600mL
        "workability_retention_min": {
            "standard": (60, 90),    # Minutes at 250-450 mL/100kg
            "extended": (90, 120),   # Minutes at up to 600 mL/100kg
        },
        "notes": [
            "Modified polycarboxylate (PCE) chemistry",
            "Water reduction ≥5% (typical 5-10%, up to 12% at max dose)",
            "May slightly increase air content",
            "Above 30°C: slump loss accelerates, push dose toward upper range",
            "Below 10°C: retarding side-effect more pronounced, reduce dose",
            "AS 1478.1 WR: ≥110% 3-day and 7-day strength",
        ],
        "addition_order": 3,
        "addition_instruction": "Add to batching water after accelerator (if present)",
    },

    # -----------------------------------------------------------------
    # Sikament ECO-3W — Water-reducing accelerator (winter mixes)
    # Classification: WRAc per AS 1478.1-2000
    # Plant standard: already in winter concrete mixes
    # -----------------------------------------------------------------
    "Sikament ECO-3W": {
        "short_name": "ECO-3W",
        "as1478_class": "WRAc",
        "density_kg_L": 1.42,
        "dose_unit": "mL/100kg",
        "dose_min": 350,
        "dose_max": 1200,
        "dose_typical": 600,     # Typical plant dose for winter mixes
        "who_controls": "plant",
        "set_time_effect": "accelerate",
        "season": "winter",      # Plant uses this in winter mixes
        "temp_dose_table": {
            (15, 20): (350, 500),
            (10, 15): (500, 800),
            (5, 10): (800, 1200),
            (-10, 5): (1000, 1200),
        },
        "water_reduction_percent": (5, 10),
        "notes": [
            "Modified copolymer technology, non-chloride",
            "Dual function: water reduction ≥5% AND set acceleration ≥1 hr",
            "Substantially mitigates temperature-induced retardation at 5-10°C",
            "Improves early age strength development",
            "Incompatible with Sikament NN only (not used by P&J Nagle)",
            "AS 1478.1 WRAc: ≥125% 1-day strength, ≥110% 3/7-day",
        ],
        "addition_order": 3,
        "addition_instruction": "Add to gauging water. Minimum 1 min/m³ wet mixing",
    },

    # -----------------------------------------------------------------
    # Sika Air LS — Air-entraining agent (most mixes)
    # Classification: AEA per AS 1478.1-2000
    # Plant standard: included in most mixes
    # -----------------------------------------------------------------
    "Sika Air LS": {
        "short_name": "Air LS",
        "as1478_class": "AEA",
        "density_kg_L": 1.01,   # Typical AEA density
        "dose_unit": "mL/m3",   # NOTE: per m³, NOT per 100kg!
        "dose_min": 250,
        "dose_max": 750,         # General concrete; up to 3000 for low-slump/high fly ash
        "dose_typical": 500,
        "who_controls": "plant",
        "set_time_effect": "none",  # Explicitly no effect on setting times
        "season": "all",
        "target_air_percent": 4.5,  # AS 1379 allows up to 5.0%
        "temp_adjustment": {
            # Air content changes with temperature:
            # Decreases ~25% from 21→38°C (increase dose in summer)
            # Increases ~25-40% from 21→4°C (reduce dose in winter)
            "reference_temp": 21,
            "hot_change_per_17deg": -0.25,   # -25% air content from 21→38°C
            "cold_change_per_17deg": +0.30,  # +30% air content from 21→4°C
        },
        "interactions": {
            "pce_reducers": "PCE water reducers (Plastiment 45) tend to increase air — reduce AEA dose",
            "retarders": "Retarders tend to increase air — reduce AEA dose slightly",
            "accelerators": "Accelerators have negligible effect on air content",
            "pumping": "Pumping causes 2-3% absolute air loss at boom discharge",
        },
        "notes": [
            "Synthetic surfactant chemistry (diluted version of Sika Air)",
            "NO effect on setting times (explicitly stated in TDS)",
            "Each 1% air increase reduces 28-day strength ~3-5%",
            "~20-30% air content change per ~11°C temperature swing",
            "Target 4-5% air for Australian practice (AS 1379)",
            "Always add FIRST and SEPARATELY before all other admixtures",
        ],
        "addition_order": 1,
        "addition_instruction": "Add to gauging water FIRST, before all other admixtures",
    },

    # -----------------------------------------------------------------
    # Sika Retarder N — Carbohydrate retarder (summer/hot weather)
    # Classification: Re per AS 1478.1-2000
    # User-specified: you tell the plant what % to add
    # -----------------------------------------------------------------
    "Sika Retarder N": {
        "short_name": "Retarder N",
        "as1478_class": "Re",
        "density_kg_L": 1.05,
        "dose_unit": "mL/100kg",
        "dose_min": 100,
        "dose_max": 300,         # Consult Sika above 400
        "who_controls": "user",
        "set_time_effect": "retard",
        "temp_dose_table": {
            # Temperature-scaled dosing from industry data:
            (15, 20): (100, 150),    # Minimal retardation needed
            (20, 25): (150, 200),    # Standard summer dosing
            (25, 30): (200, 280),    # Higher to overcome fast hydration
            (30, 35): (280, 350),    # Hot weather: aggressive retardation
            (35, 50): (350, 400),    # Extreme heat: maximum range
        },
        # TDS reference data (NOT used for timeline — field-calibrated model used instead):
        "tds_effect_at_20c": {
            100: (1.0, 1.5),    # hours extended
            200: (2.0, 3.0),
            300: (3.0, 4.0),
            400: "unpredictable",  # Consult Sika
        },
        "notes": [
            "Carbohydrate-based (sugar chemistry)",
            "Approximately linear at low doses, non-linear above 300 mL/100kg",
            "Above 400 mL/100kg: UNPREDICTABLE — consult Sika",
            "After retardation ends, hardening proceeds at ≥ normal rate",
            "NEVER add accelerator to counteract an overdose — wait it out",
            "In hot weather (>32°C), retarder wears off faster due to accelerated hydration",
            "AS 1478.1 Re: ≥1h and ≤3.5h initial set extension, ≥90% 3/7-day strength",
        ],
        "warnings": {
            "overdose": "Above 400 mL/100kg: risk of severe over-retardation. Consult Sika.",
            "no_accelerator_fix": "NEVER add accelerator to counteract overdose. Wait it out.",
        },
        "addition_order": 5,
        "addition_instruction": "Add LAST. Mix 2 minutes before discharge",
    },
}


# =============================================================================
# COMBINATION RULES
# =============================================================================

SUMMER_MIX = {
    "name": "Summer Mix",
    "condition": "hot",
    "plant_standard": "Sika Plastiment 45",   # PCE water reducer (in plant mix)
    "user_specified": "Sika Retarder N",       # You tell the plant what %
    "air_entrainer": "Sika Air LS",            # In plant mix
    "effect": "additive_retardation",
    "note": "Plastiment 45 mild retardation stacks with Retarder N dedicated retardation",
}

WINTER_MIX = {
    "name": "Winter Mix",
    "condition": "cold",
    "plant_standard": "Sikament ECO-3W",       # WR+Accelerator (in plant mix)
    "user_specified": "SikaRapid AF",          # You tell the plant what %
    "air_entrainer": "Sika Air LS",            # In plant mix
    "effect": "additive_acceleration",
    "conservative_note": "Start conservative. Flash set risk at max combined doses with reactive cements.",
}

IDEAL_MIX = {
    "name": "Standard Mix",
    "condition": "ideal",
    "plant_standard": None,  # Determined by season (Plastiment or ECO-3W)
    "user_specified": None,  # No additional admixture needed
    "air_entrainer": "Sika Air LS",
}


# =============================================================================
# ADDITION ORDER (all products, ascending order)
# =============================================================================

ADDITION_ORDER: List[Dict[str, Any]] = [
    {
        "order": 1,
        "product": "Sika Air LS",
        "instruction": "Add to gauging water FIRST, before all others",
    },
    {
        "order": 2,
        "product": "SikaRapid AF",
        "instruction": "Add with initial batching water, before water reducer",
    },
    {
        "order": 3,
        "product": "Sikament ECO-3W",
        "instruction": "Add to gauging water. Minimum 1 min/m³ wet mixing",
    },
    {
        "order": 3,
        "product": "Sika Plastiment 45",
        "instruction": "Add to batching water after accelerator (if present)",
    },
    {
        "order": 5,
        "product": "Sika Retarder N",
        "instruction": "Add LAST. Mix 2 minutes before discharge",
    },
]

# Mixing rules
MIXING_RULES = {
    "between_additions_seconds": (30, 60),  # 30-60s mixing between each product
    "total_wet_mixing_min_per_m3": 1,       # At least 1 min/m³ after all admixtures
    "never_premix": True,                   # All admixtures must be added separately
    "water_contribution_threshold_L_m3": 3, # If total liquid > 3 L/m³, include in w/c ratio
}


# =============================================================================
# SEASON DETECTION
# =============================================================================

# Plant uses Plastiment 45 in summer mixes, ECO-3W in winter mixes
# Threshold: weighted effective temp ≥ 20°C = summer, < 20°C = winter
SEASON_THRESHOLD_TEMP = 20  # °C


def get_season(weighted_eff_temp: float) -> str:
    """Determine season for plant-standard mix selection."""
    return "summer" if weighted_eff_temp >= SEASON_THRESHOLD_TEMP else "winter"


def get_plant_standard_product(weighted_eff_temp: float) -> str:
    """Get the plant-standard water reducer/accelerator for the season."""
    if get_season(weighted_eff_temp) == "summer":
        return "Sika Plastiment 45"
    else:
        return "Sikament ECO-3W"


# =============================================================================
# AIR ENTRAINER DOSING MODEL
# =============================================================================

def calculate_air_ls_dose(
    concrete_temp: float,
    volume_m3: float,
    has_pce_reducer: bool = False,
    has_retarder: bool = False,
    is_pumped: bool = False,
) -> Dict[str, Any]:
    """
    Calculate temperature-adjusted Sika Air LS dose.

    Air content is temperature-sensitive:
    - Decreases ~25% when concrete temp rises 21→38°C (increase dose)
    - Increases ~25-40% when temp drops 21→4°C (reduce dose)
    """
    product = PRODUCTS["Sika Air LS"]
    base_dose = product["dose_typical"]  # 500 mL/m³ at 21°C
    ref_temp = product["temp_adjustment"]["reference_temp"]  # 21°C

    # Temperature adjustment
    if concrete_temp > ref_temp:
        # Hot: air content drops, need more AEA
        temp_range = 38 - ref_temp  # 17°C range
        change = product["temp_adjustment"]["hot_change_per_17deg"]
        factor = 1 - change * ((concrete_temp - ref_temp) / temp_range)
    else:
        # Cold: air content rises, need less AEA
        temp_range = ref_temp - 4  # 17°C range
        change = product["temp_adjustment"]["cold_change_per_17deg"]
        factor = 1 - change * ((ref_temp - concrete_temp) / temp_range)

    factor = max(0.5, min(1.5, factor))  # Clamp
    adjusted_dose = round(base_dose * factor)

    # Interaction adjustments
    interaction_factor = 1.0
    interaction_notes = []
    if has_pce_reducer:
        interaction_factor *= 0.85  # Reduce 15% when combined with PCE
        interaction_notes.append("Reduced 15% (PCE water reducer increases air)")
    if has_retarder:
        interaction_factor *= 0.90  # Reduce 10% when combined with retarder
        interaction_notes.append("Reduced 10% (retarder increases air)")

    adjusted_dose = round(adjusted_dose * interaction_factor)

    # Pumping adjustment (informational — applied post-pump)
    pump_note = None
    if is_pumped:
        pump_note = "Pumping causes 2-3% absolute air loss at boom discharge. Consider +20-30% extra dose."

    # Clamp to product range
    adjusted_dose = max(product["dose_min"], min(product["dose_max"], adjusted_dose))

    total_ml = round(adjusted_dose * volume_m3)
    total_L = round(total_ml / 1000, 2)

    return {
        "product": "Sika Air LS",
        "short_name": "Air LS",
        "role": "Air entrainer",
        "as1478_class": "AEA",
        "dose_per_m3_ml": adjusted_dose,
        "dose_unit": "mL/m³",
        "total_dose_ml": total_ml,
        "total_dose_L": total_L,
        "target_air_percent": product["target_air_percent"],
        "set_time_effect_hours": 0,
        "effect_description": "No effect on set time",
        "temp_adjusted": True,
        "concrete_temp": concrete_temp,
        "interaction_notes": interaction_notes,
        "pump_note": pump_note,
        "strength_note": "Each 1% air reduces 28-day strength ~3-5%",
    }


# =============================================================================
# DOSAGE VALIDATION
# =============================================================================

def validate_dose(product_name: str, dose_per_unit: float) -> List[str]:
    """
    Check if a dose falls within Sika TDS recommended range.

    Returns list of warning strings (empty if dose is valid).
    """
    if product_name not in PRODUCTS:
        return []

    product = PRODUCTS[product_name]
    warnings = []
    unit = product["dose_unit"]

    if dose_per_unit < product["dose_min"]:
        warnings.append(
            f"{product_name} dose {dose_per_unit:.0f} {unit} is BELOW "
            f"Sika minimum ({product['dose_min']} {unit}). "
            f"May be ineffective."
        )

    if dose_per_unit > product["dose_max"]:
        warnings.append(
            f"{product_name} dose {dose_per_unit:.0f} {unit} EXCEEDS "
            f"Sika maximum ({product['dose_max']} {unit}). Consult Sika."
        )

    # Special warning for Retarder N above 400
    if product_name == "Sika Retarder N" and dose_per_unit > 400:
        warnings.append(
            "Sika Retarder N above 400 mL/100kg: UNPREDICTABLE. "
            "Risk of severe over-retardation. Consult Sika Technical Services."
        )

    return warnings


def get_addition_order_for_mix(product_names: List[str]) -> List[Dict[str, Any]]:
    """
    Get the correct addition order for a set of products.

    Returns only the steps for products in the mix, in correct order.
    """
    steps = []
    for entry in ADDITION_ORDER:
        if entry["product"] in product_names:
            steps.append({
                "step": len(steps) + 1,
                "product": entry["product"],
                "instruction": entry["instruction"],
            })
    return steps
