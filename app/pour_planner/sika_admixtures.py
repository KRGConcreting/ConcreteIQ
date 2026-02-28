"""
Sika Admixture Recommendation Engine.

Multi-product system for P & J Nagle Concrete (Howlong):
  - Plant-standard admixtures (Plastiment 45 / ECO-3W / Air LS) are already
    in the concrete when it arrives. These are INFORMATIONAL.
  - User-specified admixtures (Retarder N / Rapid AF) are what you TELL the plant.
    These drive the finishing timeline.

Setting time models are FIELD-CALIBRATED against real Albury-Wodonga pour data
(Feb 2026). We do NOT use the larger TDS retarder numbers because the field
model already implicitly accounts for plant-standard admixtures.
"""

from typing import Dict, Any, Optional, Tuple, List
import math

from app.pour_planner.sika_products import (
    PRODUCTS,
    SUMMER_MIX,
    WINTER_MIX,
    IDEAL_MIX,
    SEASON_THRESHOLD_TEMP,
    get_season,
    get_plant_standard_product,
    calculate_air_ls_dose,
    validate_dose,
    get_addition_order_for_mix,
)


# =============================================================================
# CEMENT CONTENT BY GRADE (kg/m³) - P & J Nagle Concrete mixes
# =============================================================================

CEMENT_CONTENT: Dict[str, int] = {
    "N20": 280,
    "N25": 320,
    "N32": 380,
    "N40": 420,
    "Exposed N20": 300,
    "Exposed N25": 340,
    "Exposed N32": 400,
}


# =============================================================================
# TEMPERATURE THRESHOLDS
# =============================================================================

RETARDER_THRESHOLD = 23   # Above this, retarder is recommended
ACCELERATOR_THRESHOLD = 15  # Below this, accelerator is recommended
IDEAL_MIN = 15
IDEAL_MAX = 23


def get_cement_content(concrete_grade: str) -> int:
    """Get cement content in kg/m³ for a concrete grade."""
    return CEMENT_CONTENT.get(concrete_grade, CEMENT_CONTENT.get("N25", 320))


# =============================================================================
# INTERNAL HELPERS
# =============================================================================

def _find_dose_range(
    temp: float,
    table: Dict[Tuple[int, int], Tuple[int, int]]
) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """Find the dosing range for a given temperature. Returns ((min_t, max_t), (min_dose, max_dose))."""
    for (min_temp, max_temp), values in table.items():
        if min_temp <= temp < max_temp:
            return (min_temp, max_temp), values
    return None


def _interpolate(
    temp: float,
    temp_min: int,
    temp_max: int,
    val_min: float,
    val_max: float
) -> float:
    """Linear interpolation within a temperature range."""
    if temp_max == temp_min:
        return val_min
    ratio = (temp - temp_min) / (temp_max - temp_min)
    return val_min + (val_max - val_min) * ratio


def _calculate_dose_and_totals(
    product_name: str,
    effective_temp: float,
    concrete_grade: str,
    volume_m3: float,
    reverse_interpolation: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Calculate dose for a product based on temperature.

    Args:
        product_name: Key in PRODUCTS dict
        effective_temp: Weighted effective temperature
        concrete_grade: N25, N32, etc
        volume_m3: Cubic metres of concrete
        reverse_interpolation: If True, colder = higher dose (for accelerators)

    Returns:
        Dict with dose details, or None if temp is outside product range
    """
    product = PRODUCTS[product_name]
    table = product["temp_dose_table"]

    # Find the matching temperature range
    result = _find_dose_range(effective_temp, table)

    if result is None:
        # Check edge cases
        all_temps = list(table.keys())
        min_temps = [t[0] for t in all_temps]
        max_temps = [t[1] for t in all_temps]

        if effective_temp >= max(max_temps):
            # Above all ranges — use highest range
            highest = max(all_temps, key=lambda x: x[1])
            result = (highest, table[highest])
        elif effective_temp < min(min_temps):
            # Below all ranges — use lowest range
            lowest = min(all_temps, key=lambda x: x[0])
            result = (lowest, table[lowest])
        else:
            return None

    (min_temp, max_temp), (min_dose, max_dose) = result

    # Interpolate dose
    if reverse_interpolation:
        # Colder = higher dose (accelerators)
        dose_per_100kg = round(_interpolate(
            effective_temp, min_temp, max_temp, max_dose, min_dose
        ))
    else:
        # Hotter = higher dose (retarders, water reducers)
        dose_per_100kg = round(_interpolate(
            effective_temp, min_temp, max_temp, min_dose, max_dose
        ))

    # Calculate total dose for job volume
    cement_kg = get_cement_content(concrete_grade) * volume_m3
    total_dose_ml = round(dose_per_100kg * cement_kg / 100)
    total_dose_L = round(total_dose_ml / 1000, 2)

    # Calculate percentage for plant communication
    dose_per_m3_L = total_dose_L / volume_m3 if volume_m3 > 0 else 0
    percentage = round(dose_per_m3_L, 1)

    # Round to nearest 0.5% for plant communication
    percentage_display = round(percentage * 2) / 2
    if percentage_display == int(percentage_display):
        percentage_display = int(percentage_display)

    # Validate dose
    dosage_warnings = validate_dose(product_name, dose_per_100kg)

    return {
        "product": product_name,
        "short_name": product["short_name"],
        "as1478_class": product["as1478_class"],
        "dose_per_100kg": dose_per_100kg,
        "dose_unit": product["dose_unit"],
        "total_dose_ml": total_dose_ml,
        "total_dose_L": total_dose_L,
        "percentage": percentage,
        "percentage_display": f"{percentage_display}%",
        "temp_range": f"{min_temp}-{max_temp}°C",
        "dosage_warnings": dosage_warnings,
    }


# =============================================================================
# FIELD-CALIBRATED SET TIME EFFECT MODELS
# =============================================================================
# These are the PROVEN models calibrated against real Albury pour data.
# They intentionally produce smaller values than TDS because the plant-standard
# admixtures (Plastiment 45, ECO-3W) are already factored into the base
# concrete that was used for calibration.

def _retarder_n_set_effect(dose_per_100kg: int, effective_temp: float) -> float:
    """
    Sika Retarder N: field-calibrated set time extension (hours).

    Uses the proven diminishing-returns model from SACRED calibration:
        hrs = 2.0 × (1 - e^(-dose/500))

    This model matched the Feb 2026 Albury pour:
        N25, ~28°C effective, ~250mL/100kg → +0.7-0.8 hrs actual
    """
    if dose_per_100kg <= 0:
        return 0.0
    max_effect = 2.0    # Max hours any dose can add
    scale = 500         # Dose for 63% of max effect
    return max_effect * (1 - math.exp(-dose_per_100kg / scale))


def _rapid_af_set_effect(dose_per_100kg: int, effective_temp: float) -> float:
    """
    SikaRapid AF: field-calibrated set time reduction (hours, negative).

    Uses existing percentage-reduction model that varies by temperature.
    More effective in cold weather (up to 45% reduction at ≤5°C).
    """
    if dose_per_100kg <= 0:
        return 0.0

    # Determine reduction percentage by temperature
    if effective_temp <= 5:
        reduction = 0.45
    elif effective_temp <= 10:
        reduction = 0.35
    elif effective_temp <= 15:
        reduction = 0.25
    else:
        reduction = 0.15

    # Scale by dose (400-2000 range → 50-100% of reduction)
    clamped = max(400, min(2000, dose_per_100kg))
    dose_factor = 0.5 + 0.5 * ((clamped - 400) / 1600)
    return -(reduction * dose_factor)  # Negative = acceleration


def _plastiment_45_set_effect(dose_per_100kg: int, effective_temp: float) -> float:
    """
    Sika Plastiment 45: mild retardation from PCE chemistry (hours, positive).

    TDS says +15 to +45 min at standard dose (250-450), +30 to +90 min extended.
    We use a conservative model since this is informational only.
    """
    if dose_per_100kg <= 0:
        return 0.0
    clamped = max(250, min(600, dose_per_100kg))
    # +0.3h at 250mL → +0.7h at 600mL
    return 0.3 + 0.4 * ((clamped - 250) / 350)


def _eco3w_set_effect(dose_per_100kg: int, effective_temp: float) -> float:
    """
    Sikament ECO-3W: acceleration from WRAc chemistry (hours, negative).

    Dual-function: water reduction + acceleration. At typical 600 mL/100kg,
    expect -0.5 to -1.0h acceleration.
    """
    if dose_per_100kg <= 0:
        return 0.0
    clamped = max(350, min(1200, dose_per_100kg))
    # -0.5h at 350mL → -1.0h at 1200mL
    return -(0.5 + 0.5 * ((clamped - 350) / 850))


# Map product names to effect functions
_SET_EFFECT_FUNCTIONS = {
    "Sika Retarder N": _retarder_n_set_effect,
    "SikaRapid AF": _rapid_af_set_effect,
    "Sika Plastiment 45": _plastiment_45_set_effect,
    "Sikament ECO-3W": _eco3w_set_effect,
    # Sika Air LS: always 0, no function needed
}


# =============================================================================
# PLANT-STANDARD PRODUCT INFO
# =============================================================================

def _get_plant_standard_info(
    plant_product_name: str,
    effective_temp: float,
    concrete_grade: str,
    volume_m3: float,
) -> Dict[str, Any]:
    """
    Build informational dict for a plant-standard product.

    These products are already in the mix — we estimate their dose and effect.
    """
    product = PRODUCTS[plant_product_name]
    typical_dose = product.get("dose_typical", product["dose_min"])

    # Try to get temperature-adjusted dose estimate
    dose_info = _calculate_dose_and_totals(
        plant_product_name, effective_temp, concrete_grade, volume_m3,
        reverse_interpolation=(product["set_time_effect"] == "accelerate"),
    )

    if dose_info:
        estimated_dose = dose_info["dose_per_100kg"]
    else:
        estimated_dose = typical_dose

    # Calculate informational set time effect
    effect_fn = _SET_EFFECT_FUNCTIONS.get(plant_product_name)
    set_effect = effect_fn(estimated_dose, effective_temp) if effect_fn else 0.0
    set_effect = round(set_effect, 1)

    # Build role description
    role_map = {
        "Sika Plastiment 45": "Water reducer (PCE)",
        "Sikament ECO-3W": "Water-reducing accelerator",
    }
    role = role_map.get(plant_product_name, product["as1478_class"])

    # Build effect description
    if set_effect > 0:
        effect_desc = f"+{set_effect}h workability"
        wr = product.get("water_reduction_percent")
        if wr:
            effect_desc += f", {wr[0]}-{wr[1]}% water reduction"
    elif set_effect < 0:
        effect_desc = f"{set_effect}h set time (acceleration)"
        wr = product.get("water_reduction_percent")
        if wr:
            effect_desc += f", {wr[0]}-{wr[1]}% water reduction"
    else:
        effect_desc = "No effect on set time"

    result = {
        "product": plant_product_name,
        "short_name": product["short_name"],
        "role": role,
        "as1478_class": product["as1478_class"],
        "dose_per_100kg": estimated_dose,
        "dose_unit": product["dose_unit"],
        "set_time_effect_hours": set_effect,
        "effect_description": effect_desc,
    }

    # Add workability retention for Plastiment 45
    if plant_product_name == "Sika Plastiment 45":
        retention = product.get("workability_retention_min", {})
        if estimated_dose <= 450:
            ret_range = retention.get("standard", (60, 90))
        else:
            ret_range = retention.get("extended", (90, 120))
        result["workability_retention_min"] = round((ret_range[0] + ret_range[1]) / 2)

    return result


# =============================================================================
# MAIN RECOMMENDATION ENGINE
# =============================================================================

def get_sika_recommendation(
    effective_temp: float,
    concrete_grade: str,
    volume_m3: float = 1.0,
) -> Dict[str, Any]:
    """
    Get complete Sika admixture recommendation based on temperature.

    Returns a multi-product structure:
      - "your_product": The admixture YOU tell the plant (Retarder N or Rapid AF)
      - "plant_standard": What's already in the mix (informational)
      - "air_entrainer": Sika Air LS info
      - "addition_order_steps": Correct mixing sequence

    The "recommended" field is True when you need to specify Retarder or Accelerator.
    Even when False (ideal range), plant-standard products are still shown.

    Args:
        effective_temp: Weighted effective temperature (°C)
        concrete_grade: N25, N32, Exposed N25, etc.
        volume_m3: Volume of concrete in m³

    Returns:
        Dict with complete mix breakdown
    """
    cement_content = get_cement_content(concrete_grade)
    season = get_season(effective_temp)
    plant_product_name = get_plant_standard_product(effective_temp)

    # Determine what YOU need to tell the plant
    your_product = None
    condition = "ideal"
    mix_name = IDEAL_MIX["name"]

    if effective_temp >= RETARDER_THRESHOLD:
        # Hot weather — recommend Retarder N
        condition = "hot"
        mix_name = SUMMER_MIX["name"]
        dose_info = _calculate_dose_and_totals(
            "Sika Retarder N", effective_temp, concrete_grade, volume_m3,
            reverse_interpolation=False,  # Hotter = higher dose
        )
        if dose_info:
            # Calculate field-calibrated set time effect
            effect_hours = _retarder_n_set_effect(
                dose_info["dose_per_100kg"], effective_temp
            )
            effect_hours = round(effect_hours, 1)
            dose_info["set_time_effect_hours"] = effect_hours
            dose_info["effect_description"] = f"+{effect_hours} hrs to initial set"
            dose_info["instructions"] = PRODUCTS["Sika Retarder N"]["addition_instruction"]
            dose_info["addition_order"] = PRODUCTS["Sika Retarder N"]["addition_order"]
            # Backwards-compat fields
            dose_info["hours_extended"] = effect_hours
            your_product = dose_info

    elif effective_temp <= ACCELERATOR_THRESHOLD:
        # Cold weather — recommend Rapid AF
        condition = "cold"
        mix_name = WINTER_MIX["name"]
        dose_info = _calculate_dose_and_totals(
            "SikaRapid AF", effective_temp, concrete_grade, volume_m3,
            reverse_interpolation=True,  # Colder = higher dose
        )
        if dose_info:
            # Calculate field-calibrated set time effect
            effect_hours = _rapid_af_set_effect(
                dose_info["dose_per_100kg"], effective_temp
            )
            effect_hours = round(effect_hours, 1)
            dose_info["set_time_effect_hours"] = effect_hours
            dose_info["effect_description"] = f"{effect_hours} hrs from initial set"
            dose_info["instructions"] = PRODUCTS["SikaRapid AF"]["addition_instruction"]
            dose_info["addition_order"] = PRODUCTS["SikaRapid AF"]["addition_order"]
            # Backwards-compat fields
            dose_info["hours_reduced"] = abs(effect_hours)

            # Extra warning for extreme cold
            if effective_temp < 5:
                dose_info["warning"] = PRODUCTS["SikaRapid AF"]["warnings"]["below_5"]

            your_product = dose_info

    # Build plant-standard info
    plant_standard_info = _get_plant_standard_info(
        plant_product_name, effective_temp, concrete_grade, volume_m3
    )

    # Build Air LS info
    has_pce = plant_product_name == "Sika Plastiment 45"
    has_retarder = your_product and your_product["product"] == "Sika Retarder N"
    air_ls_info = calculate_air_ls_dose(
        concrete_temp=effective_temp,
        volume_m3=volume_m3,
        has_pce_reducer=has_pce,
        has_retarder=has_retarder,
    )

    # Build addition order (only for products in this mix)
    mix_products = ["Sika Air LS", plant_product_name]
    if your_product:
        mix_products.append(your_product["product"])
    addition_order_steps = get_addition_order_for_mix(mix_products)

    # Aggregate warnings
    all_warnings = []
    dosage_warnings = []

    if your_product:
        dosage_warnings.extend(your_product.get("dosage_warnings", []))
        if your_product.get("warning"):
            all_warnings.append(your_product["warning"])

    # Calculate total combined set time effect (informational)
    user_effect = your_product["set_time_effect_hours"] if your_product else 0.0
    plant_effect = plant_standard_info.get("set_time_effect_hours", 0.0)
    total_effect = round(user_effect + plant_effect, 1)

    # Determine temp zone label
    if effective_temp >= 35:
        temp_zone = "Extreme Hot"
    elif effective_temp >= 30:
        temp_zone = "Hot"
    elif effective_temp >= 25:
        temp_zone = "Warm"
    elif effective_temp >= IDEAL_MIN:
        temp_zone = "Ideal"
    elif effective_temp >= 10:
        temp_zone = "Cool"
    elif effective_temp >= 5:
        temp_zone = "Cold"
    else:
        temp_zone = "Extreme Cold"

    # Build tell_plant strings (what you actually say when ordering)
    if your_product:
        product_short = your_product["short_name"]
        pct = your_product["percentage_display"]
        if "Retarder" in your_product["product"]:
            tell_plant = f"Retarder {pct}"
        elif "Rapid" in your_product["product"]:
            tell_plant = f"Accelerator {pct}"
        else:
            tell_plant = f"{product_short} {pct}"
        tell_plant_full = f"{concrete_grade} with {tell_plant}"
    else:
        tell_plant = "No admixture required"
        tell_plant_full = f"{concrete_grade}, no admixture"

    # Build the reason string
    if condition == "hot":
        reason = (
            f"Weighted temp {effective_temp:.0f}°C is above {RETARDER_THRESHOLD}°C threshold. "
            f"Retarder extends working time."
        )
    elif condition == "cold":
        reason = (
            f"Weighted temp {effective_temp:.0f}°C is below {ACCELERATOR_THRESHOLD}°C threshold. "
            f"Accelerator needed for adequate strength gain."
        )
    else:
        reason = (
            f"Temperature {effective_temp:.0f}°C is in ideal range "
            f"({IDEAL_MIN}-{IDEAL_MAX}°C)"
        )

    # Assemble the full recommendation
    recommendation = {
        # Core decision
        "condition": condition,
        "mix_name": mix_name,
        "recommended": your_product is not None,
        "reason": reason,
        "temp_zone": temp_zone,
        "season": season,

        # YOUR recommendation (what you tell the plant)
        "your_product": your_product,

        # Plant-standard products (already in the mix)
        "plant_standard": [plant_standard_info],
        "air_entrainer": air_ls_info,

        # Combined picture
        "total_set_time_effect_hours": total_effect,

        # Addition order
        "addition_order_steps": addition_order_steps,

        # Tell the plant
        "tell_plant": tell_plant,
        "tell_plant_full": tell_plant_full,

        # Warnings
        "warnings": all_warnings,
        "dosage_warnings": dosage_warnings,

        # Metadata
        "effective_temp": effective_temp,
        "concrete_grade": concrete_grade,
        "volume_m3": volume_m3,
        "cement_content_kg_m3": cement_content,

        # === BACKWARDS COMPATIBILITY ===
        # These fields match the old single-product API so existing code
        # (template, job_service) doesn't break during migration
        "product": your_product["product"] if your_product else None,
        "percentage_display": your_product["percentage_display"] if your_product else None,
        "dose_per_100kg": your_product["dose_per_100kg"] if your_product else None,
        "total_dose_L": your_product["total_dose_L"] if your_product else None,
        "total_dose_ml": your_product["total_dose_ml"] if your_product else None,
        "effect_description": your_product["effect_description"] if your_product else None,
        "instructions": your_product["instructions"] if your_product else None,
        "hours_extended": your_product.get("hours_extended") if your_product else None,
        "hours_reduced": your_product.get("hours_reduced") if your_product else None,
    }

    return recommendation


# =============================================================================
# ENHANCED SET TIME (applies admixture effects to SACRED base)
# =============================================================================

def calculate_enhanced_set_time(
    base_initial_set_hours: float,
    base_final_set_hours: float,
    sika_recommendation: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Apply Sika-specific adjustments to base SACRED set times.

    IMPORTANT: The SACRED model in service.py provides base set times with
    temperature and grade factors but NO admixture adjustments (routes.py calls
    calculate_setting_time() without admixture params). This function adds the
    field-calibrated admixture effect.

    Only the user-specified product (Retarder N or Rapid AF) adjusts the
    timeline. Plant-standard effects are informational only (the field
    calibration already implicitly includes them).

    Args:
        base_initial_set_hours: From SACRED (temp + grade, no admixture)
        base_final_set_hours: From SACRED
        sika_recommendation: Output from get_sika_recommendation()

    Returns:
        Dict with adjusted set times and explanation
    """
    adjusted_initial = base_initial_set_hours
    adjustment_reason = None
    adjustment_details = []

    your_product = sika_recommendation.get("your_product")

    if your_product and sika_recommendation.get("recommended"):
        product_name = your_product.get("product", "")
        effect_hours = your_product.get("set_time_effect_hours", 0)

        if product_name == "Sika Retarder N":
            adjusted_initial = base_initial_set_hours + effect_hours
            adjustment_reason = f"Sika Retarder N adds +{effect_hours} hrs"
        elif product_name == "SikaRapid AF":
            adjusted_initial = max(1.5, base_initial_set_hours + effect_hours)
            adjustment_reason = f"SikaRapid AF reduces {effect_hours} hrs"

        adjustment_details.append({
            "product": product_name,
            "effect_hours": effect_hours,
            "description": your_product.get("effect_description", ""),
        })

    # Final set is ~1.8x initial
    adjusted_final = adjusted_initial * 1.8

    # Time to trowel (65% of adjusted initial)
    time_to_trowel = adjusted_initial * 0.65

    return {
        "base_initial_hours": round(base_initial_set_hours, 1),
        "base_final_hours": round(base_final_set_hours, 1),
        "adjusted_initial_hours": round(adjusted_initial, 1),
        "adjusted_final_hours": round(adjusted_final, 1),
        "time_to_trowel_hours": round(time_to_trowel, 1),
        "adjustment_reason": adjustment_reason,
        "adjustment_details": adjustment_details,
        "admixture_applied": sika_recommendation.get("recommended", False),
    }
