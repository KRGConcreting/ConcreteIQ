"""
================================================================================
CONCRETEIQ - POUR PLANNER
================================================================================
⚠️ SACRED CODE - Ported from KRG_BMS pour_planner.py

Based on:
- ACI 305 (Hot Weather Concreting)
- CCAA Guidelines
- SIKA Australia product datasheets
- AS 1379, AS 3600
- Real-world calibration data (Albury-Wodonga, Feb 2026)

DO NOT SIMPLIFY OR "IMPROVE" THESE FORMULAS
================================================================================
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from enum import Enum
import math

from app.core.dates import sydney_now


# =============================================================================
# TEMPERATURE ZONES - Albury-Wodonga climate calibrated
# =============================================================================

class TempZone(Enum):
    """Temperature zones for admixture decisions."""
    EXTREME_HOT = "extreme_hot"   # >38°C - Consider postponing
    HOT = "hot"                   # 30-38°C - Retarder essential
    WARM = "warm"                 # 25-30°C - Retarder recommended
    IDEAL = "ideal"               # 18-25°C - No admixtures needed
    MARGINAL = "marginal"         # 15-18°C - N32 often sufficient alone
    COOL = "cool"                 # 10-15°C - Accelerator recommended
    COLD = "cold"                 # 5-10°C - Accelerator essential
    EXTREME_COLD = "extreme_cold" # <5°C - Accelerator + heated materials


def get_temp_zone(temp: float) -> TempZone:
    """Determine temperature zone from effective temperature."""
    if temp >= 38:
        return TempZone.EXTREME_HOT
    elif temp >= 30:
        return TempZone.HOT
    elif temp >= 25:
        return TempZone.WARM
    elif temp >= 18:
        return TempZone.IDEAL
    elif temp >= 15:
        return TempZone.MARGINAL
    elif temp >= 10:
        return TempZone.COOL
    elif temp >= 5:
        return TempZone.COLD
    else:
        return TempZone.EXTREME_COLD


# =============================================================================
# CEMENT CONTENT BY GRADE (kg/m³)
# =============================================================================

CEMENT_CONTENT = {
    "N20": 280,
    "N25": 320,
    "N32": 380,
    "N40": 420,
    "Exposed N20": 300,
    "Exposed N25": 340,
    "Exposed N32": 400,
}

# Natural set time factor (N25 = 1.0 baseline)
GRADE_SET_FACTORS = {
    "N20": 1.15,       # 15% slower than N25
    "N25": 1.00,       # Baseline
    "N32": 0.80,       # 20% faster than N25
    "N40": 0.70,       # 30% faster than N25
    "Exposed N20": 1.10,
    "Exposed N25": 0.95,
    "Exposed N32": 0.75,
}


# =============================================================================
# SUN EXPOSURE - Time-of-day ramp (calibrated Feb 2026 Albury field data)
# =============================================================================

# Full sun adjustment by hour of day (°C addition to air temp)
# Accounts for sun angle: low early morning, peak midday, drops late arvo
# Albury-Wodonga latitude ~36°S, summer sun rises ~6:10, sets ~8:30
SUN_RAMP_FULL = {
    5: 0,    6: 0,    7: 2,    8: 5,    9: 7,   10: 9,
    11: 10,  12: 10,  13: 10,  14: 9,   15: 8,  16: 6,
    17: 4,   18: 2,   19: 0,   20: 0,
}

# Half sun (partial shade / scattered cloud) is 50% of full
SUN_RAMP_HALF = {h: round(v * 0.5) for h, v in SUN_RAMP_FULL.items()}


def get_sun_adjustment(hour: int, sun_exposure: str = "full") -> float:
    """
    Get sun temperature adjustment for a given hour and exposure level.

    Replaces the old flat +0/+5/+10°C model with a time-of-day ramp
    that reflects actual sun angle and heating effect.

    Calibrated against real-world Albury pours:
    - 7:20 AM full sun = ~+2°C (sun barely up, low angle)
    - 10:00 AM full sun = ~+9°C (strong heating)
    - 12:00 PM full sun = +10°C (peak)

    Args:
        hour: Hour of day (0-23)
        sun_exposure: "shade", "half", or "full"

    Returns:
        Temperature adjustment in °C
    """
    if sun_exposure == "shade":
        return 0

    ramp = SUN_RAMP_FULL if sun_exposure == "full" else SUN_RAMP_HALF

    # Clamp to available hours
    if hour <= 5:
        return 0
    if hour >= 20:
        return 0

    return ramp.get(hour, 0)


def calculate_weighted_effective_temp(
    air_temps: List[float],
    start_hour: int,
    duration_hours: float,
    sun_exposure: str = "full",
) -> float:
    """
    Calculate a weighted average effective temperature across the pour duration.

    Concrete hydration is front-loaded — the first hour after placement is the
    most chemically active period. Later hours still matter but contribute less
    to the overall rate of setting. We use exponentially decaying weights.

    Calibrated against real-world Albury pour data (Feb 2026):
    - 8 AM screed, 22°C air, full sun, N25 + retarder
    - Actual initial set ~3.5 hrs → needs weighted temp ~29°C
    - Without early-weighting, simple average gives 32°C (too hot, too fast)

    Args:
        air_temps: List of hourly air temperatures (index 0 = midnight)
                   If shorter than needed, last value is repeated.
        start_hour: Pour start hour (e.g. 7 for 7 AM)
        duration_hours: Expected duration of setting period in hours
        sun_exposure: "shade", "half", or "full"

    Returns:
        Weighted effective temperature in °C
    """
    if not air_temps:
        return 25.0  # Fallback

    steps = max(1, int(duration_hours * 2))  # Half-hour steps
    total_weighted_temp = 0.0
    total_weight = 0.0

    for step in range(steps):
        t = start_hour + step * 0.5
        hour_idx = int(t) % 24

        # Get air temp for this hour
        idx = min(hour_idx, len(air_temps) - 1)
        air_t = air_temps[idx]

        # Get sun adjustment for this hour
        sun_adj = get_sun_adjustment(hour_idx, sun_exposure)

        # Exponentially decaying weight: first hours matter most
        # decay_rate of 0.5 means each hour is ~61% as important as the previous
        # Calibrated: Feb 2026 Albury pour, predicted sponge 10:56 vs actual 11:10
        hours_elapsed = step * 0.5
        weight = math.exp(-0.5 * hours_elapsed)

        total_weighted_temp += (air_t + sun_adj) * weight
        total_weight += weight

    return total_weighted_temp / total_weight if total_weight > 0 else 25.0


# =============================================================================
# ⚠️ SACRED: EVAPORATION RATE - ACI 305 / Paul Uno Formula
# =============================================================================

def calculate_evaporation_rate(
    air_temp: float,
    concrete_temp: float,
    humidity: float,
    wind_speed: float,
) -> float:
    """
    Calculate evaporation rate using Paul Uno formula (ACI 305 nomograph approximation).
    
    ⚠️ SACRED - DO NOT MODIFY
    
    Formula: E = 5 × [(Tc + 18)^2.5 - r × (Ta + 18)^2.5] × (V + 4) × 10^-6
    
    Where:
        E = Evaporation rate (kg/m²/hr)
        Tc = Concrete temperature (°C)
        Ta = Air temperature (°C)
        r = Relative humidity (decimal)
        V = Wind speed (km/h)
    
    Args:
        air_temp: Air temperature in °C
        concrete_temp: Concrete surface temperature in °C
        humidity: Relative humidity (0-100)
        wind_speed: Wind speed in km/h
    
    Returns:
        Evaporation rate in kg/m²/hr
    
    Risk levels (CCAA/ACI):
        < 0.25: Low risk
        0.25 - 0.50: Moderate risk
        0.50 - 0.75: High risk (CCAA limit)
        0.75 - 1.0: Very high risk
        > 1.0: Critical (ACI limit exceeded)
    """
    r = humidity / 100.0  # Convert to decimal
    v = wind_speed
    
    # Paul Uno formula
    evap_rate = 5 * (
        (concrete_temp + 18) ** 2.5 - r * (air_temp + 18) ** 2.5
    ) * (v + 4) * 1e-6
    
    return max(0, evap_rate)  # Can't be negative


def get_evaporation_risk(evap_rate: float) -> Dict[str, Any]:
    """
    Classify evaporation risk and provide recommendations.
    
    Args:
        evap_rate: Evaporation rate in kg/m²/hr
    
    Returns:
        Dict with risk level, actions, and recommendations
    """
    if evap_rate < 0.25:
        return {
            "level": "low",
            "color": "green",
            "message": "Low evaporation risk",
            "actions": ["Standard finishing procedures"],
            "aliphatic_needed": False,
        }
    elif evap_rate < 0.50:
        return {
            "level": "moderate",
            "color": "yellow",
            "message": "Moderate evaporation risk",
            "actions": [
                "Monitor surface for drying",
                "Have evap retarder ready",
            ],
            "aliphatic_needed": False,
        }
    elif evap_rate < 0.75:
        return {
            "level": "high",
            "color": "orange",
            "message": "High evaporation risk - exceeds CCAA 0.75 limit",
            "actions": [
                "Use evaporation retarder (aliphatic alcohol)",
                "Apply 1:4 dilution after each finishing pass",
                "Work quickly",
            ],
            "aliphatic_needed": True,
            "dilution": "1:4",
        }
    elif evap_rate < 1.0:
        return {
            "level": "very_high",
            "color": "red",
            "message": "Very high evaporation risk",
            "actions": [
                "Evaporation retarder ESSENTIAL",
                "Use 1:3 dilution",
                "Consider early morning or afternoon pour",
                "Fog spray if available",
            ],
            "aliphatic_needed": True,
            "dilution": "1:3",
        }
    else:
        return {
            "level": "critical",
            "color": "red",
            "message": f"CRITICAL - Exceeds ACI 1.0 limit ({evap_rate:.2f} kg/m²/hr)",
            "actions": [
                "Consider postponing pour",
                "If proceeding: 1:2 dilution aliphatic, continuous misting",
                "Work in sections",
                "Cover finished sections immediately",
            ],
            "aliphatic_needed": True,
            "dilution": "1:2",
        }


# =============================================================================
# ⚠️ SACRED: SETTING TIME CALCULATION
# =============================================================================

def calculate_setting_time(
    effective_temp: float,
    concrete_grade: str = "N25",
    retarder_dose_ml_100kg: int = 0,
    accelerator_dose_ml_100kg: int = 0,
) -> Dict[str, Any]:
    """
    Calculate predicted initial and final set times.
    
    ⚠️ SACRED - DO NOT MODIFY
    
    Uses Arrhenius-based temperature adjustment:
    - Set time roughly doubles for every 10°C drop in temperature
    - Set time roughly halves for every 10°C rise in temperature
    
    Args:
        effective_temp: Effective temperature (air + sun adjustment)
        concrete_grade: N20, N25, N32, N40, or Exposed variants
        retarder_dose_ml_100kg: Retarder dose in mL per 100kg cement
        accelerator_dose_ml_100kg: Accelerator dose in mL per 100kg cement
    
    Returns:
        Dict with initial_set_hours, final_set_hours, and notes
    """
    # Base initial set times at 20°C (hours)
    BASE_INITIAL_SET = {
        "N20": 5.5,
        "N25": 5.0,
        "N32": 4.0,
        "N40": 3.5,
        "Exposed N20": 5.0,
        "Exposed N25": 4.5,
        "Exposed N32": 3.5,
    }
    
    base_grade = concrete_grade.replace("Exposed ", "")
    base_set = BASE_INITIAL_SET.get(concrete_grade, BASE_INITIAL_SET.get(base_grade, 5.0))
    
    # Temperature adjustment (Arrhenius approximation)
    # Reference temperature is 20°C
    temp_factor = 2 ** ((20 - effective_temp) / 10)
    
    # Grade factor
    grade_factor = GRADE_SET_FACTORS.get(concrete_grade, GRADE_SET_FACTORS.get(base_grade, 1.0))
    
    # Retarder effect - diminishing returns model
    # Calibrated against real-world Albury pour data (Feb 2026):
    #   N25, ~28°C effective, ~250mL/100kg retarder → added ~0.7 hrs
    #   Total initial set matched at 3.5 hrs actual vs 3.6 hrs predicted
    #
    # Model: hours = max_effect * (1 - e^(-dose/scale))
    #   - max_effect: 2.0 hrs (absolute max any retarder dose can add)
    #   - scale: 500 (mL/100kg at which you get ~63% of max effect)
    #
    # At typical Sika Retarder N doses:
    #   200 mL/100kg → +0.7 hrs
    #   250 mL/100kg → +0.8 hrs
    #   300 mL/100kg → +0.9 hrs
    #   500 mL/100kg → +1.3 hrs (heavy dose)
    if retarder_dose_ml_100kg > 0:
        max_retarder_effect = 2.0  # Max hours any dose can add
        retarder_scale = 500       # Dose for 63% of max effect
        retarder_hours = max_retarder_effect * (
            1 - math.exp(-retarder_dose_ml_100kg / retarder_scale)
        )
    else:
        retarder_hours = 0

    # Accelerator effect: varies by temperature
    # More effective in cold (up to 45% reduction at 5°C)
    if accelerator_dose_ml_100kg > 0:
        if effective_temp <= 5:
            accel_reduction = 0.45
        elif effective_temp <= 10:
            accel_reduction = 0.35
        elif effective_temp <= 15:
            accel_reduction = 0.25
        else:
            accel_reduction = 0.15
        accel_factor = 1 - accel_reduction
    else:
        accel_factor = 1.0

    # Calculate initial set
    initial_set = base_set * grade_factor * temp_factor * accel_factor + retarder_hours
    initial_set = max(1.5, initial_set)  # Minimum 1.5 hours
    
    # Final set is typically 1.5-2x initial set
    final_set = initial_set * 1.8
    
    # Time to trowel (footprint test ready) is ~60% of initial set
    time_to_trowel = initial_set * 0.6
    
    return {
        "initial_set_hours": round(initial_set, 1),
        "final_set_hours": round(final_set, 1),
        "time_to_trowel_hours": round(time_to_trowel, 1),
        "temp_factor": round(temp_factor, 2),
        "grade_factor": grade_factor,
        "notes": [],
    }


# =============================================================================
# ⚠️ SACRED: SLUMP LOSS CALCULATION
# =============================================================================

def calculate_slump_loss(
    initial_slump: int,
    temp_celsius: float,
    travel_time_min: int = 30,
    wait_time_min: int = 10,
    concrete_grade: str = "N25",
) -> Dict[str, Any]:
    """
    Calculate slump loss during transit and waiting.
    
    ⚠️ SACRED - DO NOT MODIFY
    
    Uses temperature-driven Arrhenius model:
    - Slump loss accelerates exponentially with temperature
    - Higher strength grades lose slump faster (more cement)
    
    Args:
        initial_slump: Order slump from plant (mm)
        temp_celsius: Air temperature
        travel_time_min: Travel time from plant to site
        wait_time_min: Wait time on site before discharge
        concrete_grade: Concrete grade
    
    Returns:
        Dict with predicted slumps at each stage
    """
    total_time_min = travel_time_min + wait_time_min
    
    # Base loss rate at 20°C (mm per minute) - varies by grade
    GRADE_LOSS_FACTORS = {
        "N20": 0.25,
        "N25": 0.30,
        "N32": 0.38,
        "N40": 0.45,
        "Exposed": 0.35,
    }
    
    base_grade = concrete_grade.replace("Exposed ", "")
    base_rate = GRADE_LOSS_FACTORS.get(base_grade, 0.30)
    
    # Temperature adjustment (Arrhenius-like)
    reference_temp = 20.0
    if temp_celsius >= reference_temp:
        # Hot weather: exponential increase
        temp_factor = 2.0 ** ((temp_celsius - reference_temp) / 10.0)
    else:
        # Cold weather: slower decrease
        temp_factor = 0.5 ** ((reference_temp - temp_celsius) / 15.0)
    
    # Calculate loss (non-linear - faster initially, then slows)
    def calc_loss(minutes: float, factor: float, rate: float) -> float:
        return rate * factor * (minutes ** 0.85)
    
    # Slump at arrival
    travel_loss = calc_loss(travel_time_min, temp_factor * 1.1, base_rate)  # Slightly higher in transit
    slump_at_arrival = initial_slump - travel_loss
    
    # Slump after waiting
    wait_loss = calc_loss(wait_time_min, temp_factor * 1.2, base_rate)  # Higher when stationary
    slump_after_wait = slump_at_arrival - wait_loss
    
    # Ensure non-negative
    slump_at_arrival = max(0, round(slump_at_arrival))
    slump_after_wait = max(0, round(slump_after_wait))
    
    total_loss = initial_slump - slump_at_arrival
    
    # Workability check
    min_workable = 60
    workable = slump_at_arrival >= min_workable
    
    # Warnings
    warnings = []
    if slump_at_arrival < 80:
        warnings.append(f"Arrival slump ~{slump_at_arrival}mm may be stiff")
    if temp_celsius >= 30 and total_time_min > 45:
        warnings.append(f"Hot weather + long transit = significant slump loss")
    
    return {
        "initial_slump": initial_slump,
        "slump_at_arrival": slump_at_arrival,
        "slump_after_wait": slump_after_wait,
        "total_loss": round(total_loss),
        "total_time_min": total_time_min,
        "temp_factor": round(temp_factor, 2),
        "workable": workable,
        "warnings": warnings,
    }


def recommend_order_slump(
    target_arrival_slump: int,
    temp_celsius: float,
    travel_time_min: int = 30,
    concrete_grade: str = "N25",
) -> Dict[str, Any]:
    """
    Work backwards to determine what slump to order.
    
    Args:
        target_arrival_slump: Desired slump when concrete arrives (mm)
        temp_celsius: Expected temperature
        travel_time_min: Travel time from plant
        concrete_grade: Concrete grade
    
    Returns:
        Dict with recommended order slump
    """
    # Iterative approach - find order slump that gives target arrival
    test_slump = target_arrival_slump
    
    for _ in range(20):
        result = calculate_slump_loss(
            initial_slump=test_slump,
            temp_celsius=temp_celsius,
            travel_time_min=travel_time_min,
            concrete_grade=concrete_grade,
        )
        
        predicted = result["slump_at_arrival"]
        
        if abs(predicted - target_arrival_slump) <= 5:
            break
        
        test_slump += (target_arrival_slump - predicted)
        test_slump = max(80, min(180, test_slump))
    
    # Round to nearest 10mm (batch plants work in 10mm increments)
    order_slump = round(test_slump / 10) * 10
    
    # Standard slumps
    STANDARD_SLUMPS = [80, 100, 120, 140, 160, 180]
    if order_slump not in STANDARD_SLUMPS:
        # Round to nearest standard
        order_slump = min(STANDARD_SLUMPS, key=lambda x: abs(x - order_slump))
    
    return {
        "target_arrival_slump": target_arrival_slump,
        "order_slump": order_slump,
        "order_display": f"{order_slump}mm",
        "tell_plant": f"Order {order_slump}mm slump",
    }


# =============================================================================
# WEATHER SERVICE - Open-Meteo API
# =============================================================================

@dataclass
class WeatherData:
    """Weather data for a specific hour."""
    datetime: datetime
    temperature: float
    humidity: float
    wind_speed: float
    rain_probability: float
    rain_mm: float
    uv_index: float
    cloud_cover: float
    conditions: str


async def get_weather_forecast(
    latitude: float = -36.0737,  # Albury default
    longitude: float = 146.9135,
    date: Optional[str] = None,
) -> List[WeatherData]:
    """
    Fetch weather forecast from Open-Meteo API.
    
    Args:
        latitude: Location latitude
        longitude: Location longitude
        date: Date string YYYY-MM-DD (defaults to today)
    
    Returns:
        List of hourly WeatherData for the day
    """
    import httpx
    
    if date is None:
        date = sydney_now().strftime("%Y-%m-%d")
    
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation_probability,precipitation,uv_index,cloud_cover",
        "start_date": date,
        "end_date": date,
        "timezone": "Australia/Sydney",
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
        
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        
        weather_data = []
        for i, time_str in enumerate(times):
            temp = hourly.get("temperature_2m", [0] * len(times))[i]
            humidity = hourly.get("relative_humidity_2m", [50] * len(times))[i]
            wind = hourly.get("wind_speed_10m", [10] * len(times))[i]
            rain_prob = hourly.get("precipitation_probability", [0] * len(times))[i]
            rain_mm = hourly.get("precipitation", [0] * len(times))[i]
            uv = hourly.get("uv_index", [5] * len(times))[i]
            cloud = hourly.get("cloud_cover", [50] * len(times))[i]
            
            # Determine conditions
            if rain_prob > 70:
                conditions = "Rain likely"
            elif rain_prob > 40:
                conditions = "Chance of rain"
            elif cloud > 80:
                conditions = "Overcast"
            elif cloud > 50:
                conditions = "Partly cloudy"
            else:
                conditions = "Sunny"
            
            weather_data.append(WeatherData(
                datetime=datetime.fromisoformat(time_str),
                temperature=temp,
                humidity=humidity,
                wind_speed=wind,
                rain_probability=rain_prob,
                rain_mm=rain_mm,
                uv_index=uv,
                cloud_cover=cloud,
                conditions=conditions,
            ))
        
        return weather_data
        
    except Exception as e:
        # Return default data if API fails
        return []


# =============================================================================
# MAIN POUR ADVISORY
# =============================================================================

@dataclass
class PourAdvisory:
    """Complete pour advisory with recommendations."""
    date: str
    time: str
    location: str
    
    # Weather
    air_temp: float
    humidity: float
    wind_speed: float
    effective_temp: float       # Point-in-time at pour start (for evaporation)
    weighted_eff_temp: float    # Weighted across setting period (for set time & admixture)
    temp_zone: str
    
    # Evaporation
    evap_rate: float
    evap_risk: Dict[str, Any]
    
    # Setting time
    setting_time: Dict[str, Any]
    
    # Slump
    slump_recommendation: Dict[str, Any]
    
    # Recommendations
    grade: str
    admixture: str
    warnings: List[str]
    tips: List[str]


def generate_pour_advisory(
    pour_date: str,
    pour_time: str = "07:00",
    air_temp: float = 25,
    humidity: float = 50,
    wind_speed: float = 10,
    sun_exposure: str = "half",  # shade, half, full
    concrete_grade: str = "N25",
    is_exposed: bool = True,
    travel_time_min: int = 30,
    hourly_temps: Optional[List[float]] = None,
) -> PourAdvisory:
    """
    Generate complete pour advisory.

    Args:
        pour_date: Date string YYYY-MM-DD
        pour_time: Time string HH:MM
        air_temp: Air temperature °C at pour start
        humidity: Relative humidity %
        wind_speed: Wind speed km/h
        sun_exposure: "shade", "half", or "full"
        concrete_grade: N20, N25, N32, etc.
        is_exposed: Whether exposed aggregate
        travel_time_min: Travel time from batch plant
        hourly_temps: Optional list of 24 hourly temps (index 0 = midnight).
                      If provided, used for weighted effective temp calculation.

    Returns:
        Complete PourAdvisory
    """
    # Parse pour start hour
    try:
        pour_hour = int(pour_time.split(":")[0])
    except (ValueError, IndexError):
        pour_hour = 7

    # Calculate effective temperature using time-of-day sun ramp
    sun_add = get_sun_adjustment(pour_hour, sun_exposure)
    effective_temp = air_temp + sun_add

    # If we have hourly temps, calculate a weighted effective temp across
    # the expected setting period (~3-5 hours) for more accurate set time
    # The point-in-time effective_temp is still used for evaporation (instantaneous)
    if hourly_temps and len(hourly_temps) >= pour_hour + 4:
        # Estimate initial duration for weighting (will be refined)
        est_duration = 4.0  # Rough estimate of setting period
        weighted_eff_temp = calculate_weighted_effective_temp(
            hourly_temps, pour_hour, est_duration, sun_exposure
        )
    else:
        # No hourly data — estimate a ramp from pour start
        # Morning pours: temp rises ~2°C/hr but early hours are weighted more
        # Afternoon pours: temp drops ~1°C/hr
        if pour_hour < 12:
            # Morning — early-weighted average over setting window
            # With decay weighting, the effective ramp is lower than arithmetic avg
            ramp_offset = 2.0  # Early-weighted average ramp
            # Sun adjustment also early-weighted
            total_sun = 0.0
            total_w = 0.0
            for h in range(8):  # 4 hours in half-hour steps
                w = math.exp(-0.5 * h * 0.5)
                total_sun += get_sun_adjustment(pour_hour + h // 2, sun_exposure) * w
                total_w += w
            sun_avg = total_sun / total_w if total_w > 0 else get_sun_adjustment(pour_hour, sun_exposure)
            weighted_eff_temp = air_temp + ramp_offset + sun_avg
        else:
            weighted_eff_temp = effective_temp

    # Concrete temp is ~3°C above effective due to hydration
    concrete_temp = effective_temp + 3
    
    # Get temperature zone
    zone = get_temp_zone(effective_temp)
    
    # Calculate evaporation rate
    evap_rate = calculate_evaporation_rate(
        air_temp=air_temp,
        concrete_temp=concrete_temp,
        humidity=humidity,
        wind_speed=wind_speed,
    )
    evap_risk = get_evaporation_risk(evap_rate)
    
    # Calculate setting time using weighted temp for accuracy
    setting_time = calculate_setting_time(
        effective_temp=weighted_eff_temp,
        concrete_grade=concrete_grade,
    )
    
    # Calculate slump recommendation
    slump_rec = recommend_order_slump(
        target_arrival_slump=100,
        temp_celsius=air_temp,
        travel_time_min=travel_time_min,
        concrete_grade=concrete_grade,
    )
    
    # Determine grade recommendation
    if zone in (TempZone.MARGINAL, TempZone.COOL, TempZone.COLD, TempZone.EXTREME_COLD):
        rec_grade = "N32" if "Exposed" not in concrete_grade else "Exposed N32"
        grade_reason = "Higher cement content provides natural acceleration"
    else:
        rec_grade = concrete_grade
        grade_reason = "Standard grade appropriate"
    
    # Determine admixture
    if zone in (TempZone.HOT, TempZone.EXTREME_HOT):
        admixture = "Retarder 200-300mL/100kg cement"
    elif zone in (TempZone.COLD, TempZone.EXTREME_COLD):
        admixture = "Accelerator 1000-1500mL/100kg cement"
    elif zone == TempZone.COOL:
        admixture = "Accelerator 500-1000mL/100kg cement (optional with N32)"
    else:
        admixture = "None required"
    
    # Build warnings
    warnings = []
    if evap_rate > 0.75:
        warnings.append(f"High evaporation rate ({evap_rate:.2f} kg/m²/hr) - use evap retarder")
    if effective_temp >= 35:
        warnings.append("Very hot - work quickly, consider afternoon pour")
    if effective_temp <= 10:
        warnings.append("Cold conditions - protect from frost overnight")
    
    # Build tips
    tips = []
    if is_exposed:
        tips.append("Apply surface retarder after bull float, not before")
        if effective_temp > 30:
            tips.append("Wash window will be shorter - monitor closely")
    if evap_risk["aliphatic_needed"]:
        tips.append(f"Use aliphatic alcohol at {evap_risk.get('dilution', '1:4')} dilution")
    tips.append("Always do scratch test before washing exposed aggregate")
    
    return PourAdvisory(
        date=pour_date,
        time=pour_time,
        location="Albury-Wodonga",
        air_temp=air_temp,
        humidity=humidity,
        wind_speed=wind_speed,
        effective_temp=round(effective_temp, 1),
        weighted_eff_temp=round(weighted_eff_temp, 1),
        temp_zone=zone.value,
        evap_rate=round(evap_rate, 2),
        evap_risk=evap_risk,
        setting_time=setting_time,
        slump_recommendation=slump_rec,
        grade=rec_grade,
        admixture=admixture,
        warnings=warnings,
        tips=tips,
    )


# =============================================================================
# API CONVENIENCE FUNCTION
# =============================================================================

def calculate_pour_conditions(data: dict) -> dict:
    """
    Main entry point for pour planner API.

    Accepts weather conditions, returns complete advisory.
    """
    advisory = generate_pour_advisory(
        pour_date=data.get("date", sydney_now().strftime("%Y-%m-%d")),
        pour_time=data.get("time", "07:00"),
        air_temp=data.get("air_temp", 25),
        humidity=data.get("humidity", 50),
        wind_speed=data.get("wind_speed", 10),
        sun_exposure=data.get("sun_exposure", "half"),
        concrete_grade=data.get("concrete_grade", "N25"),
        is_exposed=data.get("is_exposed", True),
        travel_time_min=data.get("travel_time_min", 30),
        hourly_temps=data.get("hourly_temps"),
    )
    
    return {
        "date": advisory.date,
        "time": advisory.time,
        "weather": {
            "air_temp": advisory.air_temp,
            "humidity": advisory.humidity,
            "wind_speed": advisory.wind_speed,
            "effective_temp": advisory.effective_temp,
            "weighted_eff_temp": advisory.weighted_eff_temp,
        },
        "temp_zone": advisory.temp_zone,
        "evaporation": {
            "rate": advisory.evap_rate,
            "risk_level": advisory.evap_risk["level"],
            "message": advisory.evap_risk["message"],
            "actions": advisory.evap_risk["actions"],
        },
        "setting_time": {
            "initial_hours": advisory.setting_time["initial_set_hours"],
            "final_hours": advisory.setting_time["final_set_hours"],
            "time_to_trowel_hours": advisory.setting_time["time_to_trowel_hours"],
        },
        "slump": advisory.slump_recommendation,
        "recommendations": {
            "grade": advisory.grade,
            "admixture": advisory.admixture,
        },
        "warnings": advisory.warnings,
        "tips": advisory.tips,
    }
