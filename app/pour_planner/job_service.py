"""
Pour Planner Job Service — Links pour planning to jobs.

This service CALLS the SACRED service.py functions.
DO NOT modify app/pour_planner/service.py.

Enhanced with:
- Sika admixture recommendations
- Finishing timelines with clock times
- Exposed aggregate wash windows
- Hourly conditions tracking
- Job address-based weather
"""

from datetime import date, time, datetime
from typing import Optional, List, Dict, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request

from app.models import Quote, PourPlan, PourResult, ActivityLog
from app.core.dates import sydney_now

# Import from SACRED service - DO NOT MODIFY service.py
from app.pour_planner.service import (
    calculate_pour_conditions,
    get_weather_forecast,
    calculate_evaporation_rate,
    get_evaporation_risk,
    calculate_setting_time,
    recommend_order_slump,
    generate_pour_advisory,
)

# Import new enhanced modules
from app.pour_planner.sika_admixtures import (
    get_sika_recommendation,
    calculate_enhanced_set_time,
    get_cement_content,
)
from app.pour_planner.finishing_timeline import (
    calculate_finishing_timeline,
    calculate_exposed_aggregate_schedule,
    generate_timeline_text,
)


# Default coordinates (Albury-Wodonga)
DEFAULT_LAT = -36.0737
DEFAULT_LNG = 146.9135


def get_job_coordinates(quote: Quote) -> tuple[float, float]:
    """Get coordinates from quote or fall back to Albury defaults."""
    lat = float(quote.job_address_lat) if quote.job_address_lat else DEFAULT_LAT
    lng = float(quote.job_address_lng) if quote.job_address_lng else DEFAULT_LNG
    return lat, lng


def format_hour_ampm(hour: int) -> str:
    """Format hour to AM/PM display."""
    if hour == 0:
        return "12 AM"
    elif hour < 12:
        return f"{hour} AM"
    elif hour == 12:
        return "12 PM"
    else:
        return f"{hour - 12} PM"


async def get_hourly_conditions(
    quote: Quote,
    pour_date: date,
    pour_start_hour: int = 7,
    estimated_duration_hours: int = 8,
) -> List[Dict[str, Any]]:
    """
    Get hourly weather and evaporation for the pour day.

    Fetches hourly forecast from Open-Meteo and calculates
    evaporation rate for each hour from pour start to finish.

    Args:
        quote: Quote with job details (for address coordinates)
        pour_date: Date of the pour
        pour_start_hour: Start hour (24hr format)
        estimated_duration_hours: How long the pour will take

    Returns:
        List of hourly condition dicts with temp, humidity, wind, evap rate, risk
    """
    # Get coordinates from job or default to Albury
    lat, lng = get_job_coordinates(quote)

    # Fetch hourly weather
    weather_data = await get_weather_forecast(
        latitude=lat,
        longitude=lng,
        date=pour_date.strftime("%Y-%m-%d"),
    )

    if not weather_data:
        return []

    hourly_conditions = []
    for hour_offset in range(estimated_duration_hours + 2):
        target_hour = pour_start_hour + hour_offset
        if target_hour >= 24:
            break

        # Find weather for this hour
        for w in weather_data:
            if w.datetime.hour == target_hour:
                # Calculate evap rate for this hour
                # Concrete temp is ~8°C above air temp in sun
                concrete_temp = w.temperature + 8
                evap_rate = calculate_evaporation_rate(
                    air_temp=w.temperature,
                    concrete_temp=concrete_temp,
                    humidity=w.humidity,
                    wind_speed=w.wind_speed,
                )
                evap_risk = get_evaporation_risk(evap_rate)

                hourly_conditions.append({
                    "hour": f"{target_hour:02d}:00",
                    "hour_display": format_hour_ampm(target_hour),
                    "temp": round(w.temperature),
                    "humidity": round(w.humidity),
                    "wind": round(w.wind_speed),
                    "conditions": w.conditions,
                    "rain_prob": w.rain_probability,
                    "evap_rate": round(evap_rate, 2),
                    "evap_risk": evap_risk["level"],
                    "evap_color": evap_risk["color"],
                })
                break

    return hourly_conditions


async def create_pour_plan(
    db: AsyncSession,
    quote_id: int,
    planned_date: date,
    planned_time: Optional[str],
    request: Request,
) -> PourPlan:
    """
    Create a pour plan for a job.

    Fetches weather forecast and runs calculations from SACRED service.py.
    Enhanced with Sika recommendations and finishing timeline.

    Args:
        db: Database session
        quote_id: Quote/job ID
        planned_date: Planned pour date
        planned_time: Planned start time (HH:MM)
        request: HTTP request for logging

    Returns:
        Created PourPlan record

    Raises:
        ValueError: If quote not found or plan already exists
    """
    # Validate quote exists
    quote = await db.get(Quote, quote_id)
    if not quote:
        raise ValueError("Quote not found")

    # Check for existing plan
    existing = await get_pour_plan(db, quote_id)
    if existing:
        raise ValueError("Pour plan already exists for this quote. Use update instead.")

    # Get coordinates from job address or default to Albury
    lat, lng = get_job_coordinates(quote)

    # Get weather forecast for the planned date
    weather_data = await get_weather_forecast(
        latitude=lat,
        longitude=lng,
        date=planned_date.strftime("%Y-%m-%d"),
    )

    # Extract weather for planned time or default to 7am
    time_str = planned_time or "07:00"
    hour = int(time_str.split(":")[0])

    weather_snapshot = None
    air_temp = 25.0
    humidity = 50.0
    wind_speed = 10.0

    if weather_data:
        # Find hourly data closest to planned time
        for w in weather_data:
            if w.datetime.hour == hour:
                air_temp = w.temperature
                humidity = w.humidity
                wind_speed = w.wind_speed
                weather_snapshot = {
                    "time": w.datetime.strftime("%H:%M"),
                    "temperature": w.temperature,
                    "humidity": w.humidity,
                    "wind_speed": w.wind_speed,
                    "rain_probability": w.rain_probability,
                    "conditions": w.conditions,
                    "fetched_at": sydney_now().isoformat(),
                    "location": {
                        "lat": lat,
                        "lng": lng,
                        "source": "job_address" if quote.job_address_lat else "default_albury",
                    },
                }
                break

    # Get concrete grade from quote calculator input
    concrete_grade = "N25"
    is_exposed = False
    if quote.calculator_input:
        concrete_grade = quote.calculator_input.get("concrete_grade", "N25")
        is_exposed = quote.calculator_input.get("exposed_aggregate", False)

    # Calculate effective temperature (sun exposure assumed half for planning)
    sun_add = 5  # "half" sun exposure
    effective_temp = air_temp + sun_add

    # Calculate pour conditions using SACRED functions
    result = calculate_pour_conditions({
        "date": planned_date.strftime("%Y-%m-%d"),
        "time": time_str,
        "air_temp": air_temp,
        "humidity": humidity,
        "wind_speed": wind_speed,
        "sun_exposure": "half",
        "concrete_grade": concrete_grade,
        "is_exposed": is_exposed,
        "travel_time_min": 30,
    })

    # Get volume from calculator (default 1m³)
    volume_m3 = 1.0
    if quote.calculator_input:
        volume_m3 = quote.calculator_input.get("total_concrete_m3", 1.0)

    # Get Sika admixture recommendation
    sika_rec = get_sika_recommendation(effective_temp, concrete_grade, volume_m3)

    # Get enhanced set time with Sika adjustments
    base_initial = result.get("setting_time", {}).get("initial_hours", 5.0)
    base_final = result.get("setting_time", {}).get("final_hours", 9.0)
    enhanced_set = calculate_enhanced_set_time(base_initial, base_final, sika_rec)

    # Calculate finishing timeline with clock times
    timeline = calculate_finishing_timeline(
        pour_start_time=time_str,
        initial_set_hours=enhanced_set["adjusted_initial_hours"],
        final_set_hours=enhanced_set["adjusted_final_hours"],
    )

    # Calculate exposed aggregate schedule if applicable
    exposed_schedule = None
    if is_exposed:
        exposed_schedule = calculate_exposed_aggregate_schedule(
            pour_start_time=time_str,
            effective_temp=effective_temp,
        )

    # Extract evaporation data
    evap_rate = result.get("evaporation", {}).get("rate", 0)
    evap_risk_level = result.get("evaporation", {}).get("risk_level", "low")

    # Build comprehensive recommendations list
    recommendations = {
        "evaporation_actions": result.get("evaporation", {}).get("actions", []),
        "tips": result.get("tips", []),
        "sika_recommendation": sika_rec,
        "enhanced_set_time": enhanced_set,
        "timeline": timeline,
        "exposed_schedule": exposed_schedule,
        "finishing_window": timeline.get("finishing_window"),
    }

    # Parse time string to time object
    planned_time_obj = None
    if planned_time:
        try:
            h, m = planned_time.split(":")
            planned_time_obj = time(int(h), int(m))
        except (ValueError, TypeError):
            pass

    # Create pour plan
    pour_plan = PourPlan(
        quote_id=quote_id,
        planned_date=planned_date,
        planned_start_time=planned_time_obj,
        weather_snapshot=weather_snapshot,
        evaporation_rate=evap_rate,
        risk_level=evap_risk_level,
        recommendations=recommendations,
        created_at=sydney_now(),
        updated_at=sydney_now(),
    )
    db.add(pour_plan)
    await db.flush()

    # Log activity
    activity = ActivityLog(
        action="pour_plan_created",
        description=f"Pour plan created for quote {quote.quote_number}",
        entity_type="quote",
        entity_id=quote_id,
        ip_address=request.client.host if request.client else None,
        extra_data={
            "planned_date": planned_date.isoformat(),
            "evaporation_rate": evap_rate,
            "risk_level": evap_risk_level,
            "sika_product": sika_rec.get("product"),
            "sika_dose": sika_rec.get("dose_per_100kg"),
        },
    )
    db.add(activity)

    return pour_plan


async def get_pour_plan(db: AsyncSession, quote_id: int) -> Optional[PourPlan]:
    """Get pour plan for a quote."""
    result = await db.execute(
        select(PourPlan).where(PourPlan.quote_id == quote_id)
    )
    return result.scalar_one_or_none()


async def update_pour_plan(
    db: AsyncSession,
    pour_plan: PourPlan,
    planned_date: Optional[date],
    planned_time: Optional[str],
    request: Request,
) -> PourPlan:
    """
    Update a pour plan and refresh weather/calculations.

    Args:
        db: Database session
        pour_plan: Existing PourPlan to update
        planned_date: New planned date (or None to keep existing)
        planned_time: New planned time (or None to keep existing)
        request: HTTP request for logging

    Returns:
        Updated PourPlan
    """
    quote = await db.get(Quote, pour_plan.quote_id)

    # Update date/time if provided
    if planned_date:
        pour_plan.planned_date = planned_date
    if planned_time:
        try:
            h, m = planned_time.split(":")
            pour_plan.planned_start_time = time(int(h), int(m))
        except (ValueError, TypeError):
            pass

    # Get coordinates from job address or default to Albury
    lat, lng = get_job_coordinates(quote) if quote else (DEFAULT_LAT, DEFAULT_LNG)

    # Refresh weather and calculations
    time_str = pour_plan.planned_start_time.strftime("%H:%M") if pour_plan.planned_start_time else "07:00"
    hour = int(time_str.split(":")[0])

    weather_data = await get_weather_forecast(
        latitude=lat,
        longitude=lng,
        date=pour_plan.planned_date.strftime("%Y-%m-%d"),
    )

    air_temp = 25.0
    humidity = 50.0
    wind_speed = 10.0

    if weather_data:
        for w in weather_data:
            if w.datetime.hour == hour:
                air_temp = w.temperature
                humidity = w.humidity
                wind_speed = w.wind_speed
                pour_plan.weather_snapshot = {
                    "time": w.datetime.strftime("%H:%M"),
                    "temperature": w.temperature,
                    "humidity": w.humidity,
                    "wind_speed": w.wind_speed,
                    "rain_probability": w.rain_probability,
                    "conditions": w.conditions,
                    "fetched_at": sydney_now().isoformat(),
                    "location": {
                        "lat": lat,
                        "lng": lng,
                        "source": "job_address" if (quote and quote.job_address_lat) else "default_albury",
                    },
                }
                break

    # Get concrete grade from quote
    concrete_grade = "N25"
    is_exposed = False
    volume_m3 = 1.0
    if quote and quote.calculator_input:
        concrete_grade = quote.calculator_input.get("concrete_grade", "N25")
        is_exposed = quote.calculator_input.get("exposed_aggregate", False)
        volume_m3 = quote.calculator_input.get("total_concrete_m3", 1.0)

    # Calculate effective temperature
    sun_add = 5  # "half" sun exposure
    effective_temp = air_temp + sun_add

    # Recalculate using SACRED functions
    result = calculate_pour_conditions({
        "date": pour_plan.planned_date.strftime("%Y-%m-%d"),
        "time": time_str,
        "air_temp": air_temp,
        "humidity": humidity,
        "wind_speed": wind_speed,
        "sun_exposure": "half",
        "concrete_grade": concrete_grade,
        "is_exposed": is_exposed,
        "travel_time_min": 30,
    })

    # Get Sika recommendation
    sika_rec = get_sika_recommendation(effective_temp, concrete_grade, volume_m3)

    # Get enhanced set time
    base_initial = result.get("setting_time", {}).get("initial_hours", 5.0)
    base_final = result.get("setting_time", {}).get("final_hours", 9.0)
    enhanced_set = calculate_enhanced_set_time(base_initial, base_final, sika_rec)

    # Calculate finishing timeline
    timeline = calculate_finishing_timeline(
        pour_start_time=time_str,
        initial_set_hours=enhanced_set["adjusted_initial_hours"],
        final_set_hours=enhanced_set["adjusted_final_hours"],
    )

    # Calculate exposed aggregate schedule if applicable
    exposed_schedule = None
    if is_exposed:
        exposed_schedule = calculate_exposed_aggregate_schedule(
            pour_start_time=time_str,
            effective_temp=effective_temp,
        )

    pour_plan.evaporation_rate = result.get("evaporation", {}).get("rate", 0)
    pour_plan.risk_level = result.get("evaporation", {}).get("risk_level", "low")

    # Update recommendations
    pour_plan.recommendations = {
        "evaporation_actions": result.get("evaporation", {}).get("actions", []),
        "tips": result.get("tips", []),
        "sika_recommendation": sika_rec,
        "enhanced_set_time": enhanced_set,
        "timeline": timeline,
        "exposed_schedule": exposed_schedule,
        "finishing_window": timeline.get("finishing_window"),
    }

    pour_plan.updated_at = sydney_now()

    # Log activity
    activity = ActivityLog(
        action="pour_plan_updated",
        description=f"Pour plan updated for quote {quote.quote_number if quote else pour_plan.quote_id}",
        entity_type="quote",
        entity_id=pour_plan.quote_id,
        ip_address=request.client.host if request.client else None,
        extra_data={
            "planned_date": pour_plan.planned_date.isoformat(),
            "evaporation_rate": pour_plan.evaporation_rate,
            "risk_level": pour_plan.risk_level,
            "sika_product": sika_rec.get("product"),
        },
    )
    db.add(activity)

    return pour_plan


async def refresh_pour_plan_weather(
    db: AsyncSession,
    pour_plan: PourPlan,
    request: Request,
) -> PourPlan:
    """
    Refresh weather data and recalculate conditions for an existing pour plan.

    Args:
        db: Database session
        pour_plan: PourPlan to refresh
        request: HTTP request for logging

    Returns:
        Updated PourPlan with fresh weather data
    """
    return await update_pour_plan(db, pour_plan, None, None, request)


async def get_pour_conditions(
    db: AsyncSession,
    quote: Quote,
) -> dict:
    """
    Get current pour conditions for a job.

    Uses SACRED service.py functions for calculations.
    Enhanced with Sika recommendations and finishing timeline.

    Args:
        db: Database session
        quote: Quote with job details

    Returns:
        Dict with weather, evaporation, setting time, slump, recommendations
    """
    # Get existing pour plan if any
    pour_plan = await get_pour_plan(db, quote.id)

    # Determine the date to use
    if pour_plan:
        pour_date = pour_plan.planned_date
    elif quote.confirmed_start_date:
        pour_date = quote.confirmed_start_date
    else:
        pour_date = None

    if not pour_date:
        return {
            "quote_id": quote.id,
            "planned_date": None,
            "message": "No pour date set. Confirm booking or create a pour plan first.",
        }

    # Get concrete details from calculator input
    concrete_grade = "N25"
    is_exposed = False
    volume_m3 = 1.0
    if quote.calculator_input:
        concrete_grade = quote.calculator_input.get("concrete_grade", "N25")
        is_exposed = quote.calculator_input.get("exposed_aggregate", False)
        volume_m3 = quote.calculator_input.get("total_concrete_m3", 1.0)

    # Determine time
    time_str = "07:00"
    if pour_plan and pour_plan.planned_start_time:
        time_str = pour_plan.planned_start_time.strftime("%H:%M")

    # Get coordinates from job address or default
    lat, lng = get_job_coordinates(quote)

    # Fetch fresh weather
    weather_data = await get_weather_forecast(
        latitude=lat,
        longitude=lng,
        date=pour_date.strftime("%Y-%m-%d"),
    )

    hour = int(time_str.split(":")[0])
    air_temp = 25.0
    humidity = 50.0
    wind_speed = 10.0
    weather_info = None

    if weather_data:
        for w in weather_data:
            if w.datetime.hour == hour:
                air_temp = w.temperature
                humidity = w.humidity
                wind_speed = w.wind_speed
                weather_info = {
                    "time": w.datetime.strftime("%H:%M"),
                    "temperature": w.temperature,
                    "humidity": w.humidity,
                    "wind_speed": w.wind_speed,
                    "rain_probability": w.rain_probability,
                    "conditions": w.conditions,
                }
                break

    # Calculate effective temperature
    sun_add = 5  # "half" sun exposure
    effective_temp = air_temp + sun_add

    # Calculate conditions using SACRED functions
    result = calculate_pour_conditions({
        "date": pour_date.strftime("%Y-%m-%d"),
        "time": time_str,
        "air_temp": air_temp,
        "humidity": humidity,
        "wind_speed": wind_speed,
        "sun_exposure": "half",
        "concrete_grade": concrete_grade,
        "is_exposed": is_exposed,
        "travel_time_min": 30,
    })

    # Get Sika recommendation
    sika_rec = get_sika_recommendation(effective_temp, concrete_grade, volume_m3)

    # Get enhanced set time
    base_initial = result.get("setting_time", {}).get("initial_hours", 5.0)
    base_final = result.get("setting_time", {}).get("final_hours", 9.0)
    enhanced_set = calculate_enhanced_set_time(base_initial, base_final, sika_rec)

    # Calculate finishing timeline
    timeline = calculate_finishing_timeline(
        pour_start_time=time_str,
        initial_set_hours=enhanced_set["adjusted_initial_hours"],
        final_set_hours=enhanced_set["adjusted_final_hours"],
    )

    # Calculate exposed aggregate schedule if applicable
    exposed_schedule = None
    if is_exposed:
        exposed_schedule = calculate_exposed_aggregate_schedule(
            pour_start_time=time_str,
            effective_temp=effective_temp,
        )

    # Get hourly conditions
    hourly = await get_hourly_conditions(
        quote=quote,
        pour_date=pour_date,
        pour_start_hour=hour,
    )

    return {
        "quote_id": quote.id,
        "planned_date": pour_date.isoformat(),
        "planned_time": time_str,
        "weather": weather_info or result.get("weather"),
        "effective_temp": effective_temp,
        "temp_zone": result.get("temp_zone"),
        "evaporation": result.get("evaporation"),
        "setting_time": result.get("setting_time"),
        "enhanced_set_time": enhanced_set,
        "slump": result.get("slump"),
        "recommendations": result.get("recommendations"),
        "sika_recommendation": sika_rec,
        "timeline": timeline,
        "finishing_window": timeline.get("finishing_window"),
        "exposed_schedule": exposed_schedule,
        "hourly": hourly,
        "warnings": result.get("warnings", []),
        "tips": result.get("tips", []),
        "location": {
            "lat": lat,
            "lng": lng,
            "source": "job_address" if quote.job_address_lat else "default_albury",
        },
    }


async def delete_pour_plan(
    db: AsyncSession,
    pour_plan: PourPlan,
    request: Request,
) -> None:
    """Delete a pour plan."""
    quote = await db.get(Quote, pour_plan.quote_id)
    quote_number = quote.quote_number if quote else f"#{pour_plan.quote_id}"

    # Log activity
    activity = ActivityLog(
        action="pour_plan_deleted",
        description=f"Pour plan deleted for quote {quote_number}",
        entity_type="quote",
        entity_id=pour_plan.quote_id,
        ip_address=request.client.host if request.client else None,
    )
    db.add(activity)

    await db.delete(pour_plan)


# =============================================================================
# POUR RESULT LOGGING
# =============================================================================

async def create_pour_result(
    db: AsyncSession,
    pour_plan_id: int,
    data: Dict[str, Any],
    request: Request,
) -> PourResult:
    """
    Log actual pour results for calibration.

    Args:
        db: Database session
        pour_plan_id: ID of the pour plan
        data: Dict with actual results
        request: HTTP request for logging

    Returns:
        Created PourResult record
    """
    # Get pour plan to capture predictions
    pour_plan = await db.get(PourPlan, pour_plan_id)
    if not pour_plan:
        raise ValueError("Pour plan not found")

    # Extract predictions from pour plan recommendations
    recs = pour_plan.recommendations or {}
    sika_rec = recs.get("sika_recommendation", {})
    enhanced_set = recs.get("enhanced_set_time", {})
    finishing_window = recs.get("finishing_window", {})

    pour_result = PourResult(
        pour_plan_id=pour_plan_id,

        # Predictions
        predicted_initial_set_hours=enhanced_set.get("adjusted_initial_hours"),
        predicted_finish_window_start=finishing_window.get("opens"),
        predicted_finish_window_end=finishing_window.get("closes"),
        recommended_admixture=sika_rec.get("product"),
        recommended_dose_ml=sika_rec.get("total_dose_ml"),

        # Actuals from input
        actual_admixture_used=data.get("actual_admixture_used"),
        actual_dose_ml=data.get("actual_dose_ml"),
        actual_initial_set_hours=data.get("actual_initial_set_hours"),
        actual_finish_time=data.get("actual_finish_time"),
        actual_conditions_notes=data.get("actual_conditions_notes"),

        # Assessment
        prediction_accuracy=data.get("prediction_accuracy"),

        created_at=sydney_now(),
    )
    db.add(pour_result)
    await db.flush()

    # Log activity
    activity = ActivityLog(
        action="pour_result_logged",
        description=f"Pour result logged for plan {pour_plan_id}",
        entity_type="pour_plan",
        entity_id=pour_plan_id,
        ip_address=request.client.host if request.client else None,
        extra_data={
            "prediction_accuracy": data.get("prediction_accuracy"),
            "actual_admixture": data.get("actual_admixture_used"),
        },
    )
    db.add(activity)

    return pour_result


async def get_pour_result(db: AsyncSession, pour_plan_id: int) -> Optional[PourResult]:
    """Get pour result for a pour plan."""
    result = await db.execute(
        select(PourResult).where(PourResult.pour_plan_id == pour_plan_id)
    )
    return result.scalar_one_or_none()


async def get_all_pour_results(
    db: AsyncSession,
    limit: int = 50,
) -> List[PourResult]:
    """Get all pour results for analysis."""
    result = await db.execute(
        select(PourResult)
        .order_by(PourResult.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
