"""
Pour Planner routes — Weather-based concrete recommendations.

Enhanced with:
- Sika admixture recommendations
- Finishing timelines with clock times
- Exposed aggregate wash windows
- Hourly conditions tracking
- Pour result logging for calibration
"""

from datetime import date, datetime
from typing import Optional
from fastapi import APIRouter, Depends, Request, Query, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Quote, PourPlan
from app.core.auth import require_login, verify_csrf
from app.core.templates import templates
from app.pour_planner.service import (
    calculate_pour_conditions,
    get_weather_forecast,
    calculate_evaporation_rate,
    get_evaporation_risk,
    calculate_setting_time,
    recommend_order_slump,
)
from app.pour_planner import job_service
from app.pour_planner.sika_admixtures import (
    get_sika_recommendation,
    calculate_enhanced_set_time,
)
from app.pour_planner.finishing_timeline import (
    calculate_finishing_timeline,
    calculate_exposed_aggregate_schedule,
)

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


@router.get("", name="pour_planner:page")
async def pour_planner_page(request: Request):
    """Pour planner page with weather inputs."""
    return templates.TemplateResponse("pour_planner/index.html", {
        "request": request,
    })


@router.get("/calibration", name="pour_planner:calibration")
async def calibration_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Pour calibration dashboard — prediction accuracy analysis."""
    from app.pour_planner.calibration import get_calibration_stats
    stats = await get_calibration_stats(db)
    return templates.TemplateResponse("pour_planner/calibration.html", {
        "request": request,
        "stats": stats,
    })


@router.post("/api/calculate")
async def api_calculate(data: dict):
    """
    Calculate pour conditions and recommendations.

    Accepts:
        - date: YYYY-MM-DD
        - time: HH:MM
        - air_temp: °C
        - humidity: %
        - wind_speed: km/h
        - sun_exposure: shade|half|full
        - concrete_grade: N20|N25|N32|etc
        - is_exposed: bool
        - travel_time_min: minutes
        - volume_m3: concrete volume (optional, default 1.0)

    Returns complete advisory with:
        - evaporation rate and risk
        - setting time (base and enhanced with Sika)
        - slump recommendation
        - Sika admixture recommendation
        - finishing timeline with clock times
        - exposed aggregate wash window (if applicable)
    """
    # Get base calculations from SACRED service
    result = calculate_pour_conditions(data)

    # Use weighted effective temp for admixture decisions — this accounts for
    # temperature ramping throughout the setting period, not just pour-start snapshot.
    # e.g. 12°C at 7:30 AM but 25°C+ by midday = no accelerator needed!
    weighted_eff_temp = result.get("weather", {}).get("weighted_eff_temp", 25)
    effective_temp = result.get("weather", {}).get("effective_temp", 25)

    # Get Sika recommendation using weighted temp (matches set time calculation)
    concrete_grade = data.get("concrete_grade", "N25")
    volume_m3 = data.get("volume_m3", 1.0)
    sika_rec = get_sika_recommendation(weighted_eff_temp, concrete_grade, volume_m3)

    # Get enhanced set time with Sika adjustments
    base_initial = result.get("setting_time", {}).get("initial_hours", 5.0)
    base_final = result.get("setting_time", {}).get("final_hours", 9.0)
    enhanced_set = calculate_enhanced_set_time(base_initial, base_final, sika_rec)

    # Calculate finishing timeline
    pour_time = data.get("time", "07:00")
    timeline = calculate_finishing_timeline(
        pour_start_time=pour_time,
        initial_set_hours=enhanced_set["adjusted_initial_hours"],
        final_set_hours=enhanced_set["adjusted_final_hours"],
    )

    # Calculate exposed aggregate schedule if applicable
    exposed_schedule = None
    is_exposed = data.get("is_exposed", False)
    if is_exposed:
        exposed_schedule = calculate_exposed_aggregate_schedule(
            pour_start_time=pour_time,
            effective_temp=effective_temp,
        )

    # Enhance the result with new data
    result["effective_temp"] = effective_temp
    result["sika_recommendation"] = sika_rec
    result["enhanced_set_time"] = enhanced_set
    result["timeline"] = timeline
    result["finishing_window"] = timeline.get("finishing_window")
    result["exposed_schedule"] = exposed_schedule

    # Update recommendations to use Sika-specific advice
    if sika_rec.get("recommended"):
        result["recommendations"]["admixture"] = sika_rec.get("tell_plant", "")
        result["recommendations"]["admixture_detail"] = sika_rec
    else:
        result["recommendations"]["admixture"] = "None required"
        result["recommendations"]["admixture_detail"] = None

    return result


@router.get("/api/weather")
async def api_weather(
    date: Optional[str] = None,
    lat: float = Query(-36.0737, description="Latitude"),
    lon: float = Query(146.9135, description="Longitude"),
):
    """
    Fetch weather forecast from Open-Meteo.

    Returns hourly forecast for the specified date.
    """
    try:
        forecast = await get_weather_forecast(
            latitude=lat,
            longitude=lon,
            date=date,
        )

        return {
            "success": True,
            "data": [
                {
                    "time": w.datetime.strftime("%H:%M"),
                    "temperature": w.temperature,
                    "humidity": w.humidity,
                    "wind_speed": w.wind_speed,
                    "rain_probability": w.rain_probability,
                    "conditions": w.conditions,
                }
                for w in forecast
            ],
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/api/evaporation")
async def api_evaporation(data: dict):
    """
    Calculate evaporation rate only.

    Quick endpoint for real-time updates.
    """
    evap_rate = calculate_evaporation_rate(
        air_temp=data.get("air_temp", 25),
        concrete_temp=data.get("concrete_temp", data.get("air_temp", 25) + 8),
        humidity=data.get("humidity", 50),
        wind_speed=data.get("wind_speed", 10),
    )

    risk = get_evaporation_risk(evap_rate)

    return {
        "evap_rate": round(evap_rate, 2),
        "evap_display": f"{evap_rate:.2f} kg/m²/hr",
        "risk": risk,
    }


# =============================================================================
# JOB-SPECIFIC ENDPOINTS
# =============================================================================

@router.get("/job/{quote_id}", name="pour_planner:job")
async def pour_planner_job_page(
    request: Request,
    quote_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Pour planner page pre-filled with job details."""
    quote = await db.get(Quote, quote_id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    # Get existing pour plan if any
    pour_plan = await job_service.get_pour_plan(db, quote_id)

    # Get pour result if exists
    pour_result = None
    if pour_plan:
        pour_result = await job_service.get_pour_result(db, pour_plan.id)

    return templates.TemplateResponse("pour_planner/index.html", {
        "request": request,
        "quote": quote,
        "pour_plan": pour_plan,
        "pour_result": pour_result,
    })


@router.get("/api/job/{quote_id}/conditions")
async def api_job_conditions(
    quote_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get current pour conditions for a specific job.

    Returns weather, evaporation risk, setting time, slump recommendations,
    Sika admixture recommendation, finishing timeline, and hourly conditions.
    """
    quote = await db.get(Quote, quote_id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    try:
        conditions = await job_service.get_pour_conditions(db, quote)
        return {"success": True, **conditions}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/api/job/{quote_id}/hourly")
async def api_job_hourly_conditions(
    quote_id: int,
    date: Optional[str] = None,
    start_hour: int = Query(7, ge=0, le=23),
    duration_hours: int = Query(8, ge=1, le=16),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get hourly conditions for a pour day.

    Returns temperature, humidity, wind, and evaporation rate for each hour.
    """
    quote = await db.get(Quote, quote_id)
    if not quote:
        raise HTTPException(404, "Quote not found")

    # Determine date
    pour_plan = await job_service.get_pour_plan(db, quote_id)

    if date:
        pour_date = datetime.strptime(date, "%Y-%m-%d").date()
    elif pour_plan:
        pour_date = pour_plan.planned_date
    elif quote.confirmed_start_date:
        pour_date = quote.confirmed_start_date
    else:
        raise HTTPException(400, "No pour date specified")

    hourly = await job_service.get_hourly_conditions(
        quote=quote,
        pour_date=pour_date,
        pour_start_hour=start_hour,
        estimated_duration_hours=duration_hours,
    )

    return {
        "success": True,
        "date": pour_date.isoformat(),
        "hourly": hourly,
    }


@router.get("/api/job/{quote_id}/plan")
async def api_get_pour_plan(
    quote_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get pour plan for a job."""
    pour_plan = await job_service.get_pour_plan(db, quote_id)

    if not pour_plan:
        return {
            "success": True,
            "has_plan": False,
            "plan": None,
        }

    return {
        "success": True,
        "has_plan": True,
        "plan": {
            "id": pour_plan.id,
            "quote_id": pour_plan.quote_id,
            "planned_date": pour_plan.planned_date.isoformat() if pour_plan.planned_date else None,
            "planned_start_time": pour_plan.planned_start_time.strftime("%H:%M") if pour_plan.planned_start_time else None,
            "weather_snapshot": pour_plan.weather_snapshot,
            "evaporation_rate": pour_plan.evaporation_rate,
            "risk_level": pour_plan.risk_level,
            "recommendations": pour_plan.recommendations,
            "created_at": pour_plan.created_at.isoformat() if pour_plan.created_at else None,
            "updated_at": pour_plan.updated_at.isoformat() if pour_plan.updated_at else None,
        },
    }


@router.post("/api/job/{quote_id}/plan")
async def api_create_pour_plan(
    request: Request,
    quote_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Create a pour plan for a job.

    Accepts:
    - planned_date: YYYY-MM-DD
    - planned_start_time: HH:MM (optional)
    """
    try:
        # Parse date
        planned_date_str = data.get("planned_date")
        if not planned_date_str:
            raise ValueError("planned_date is required")

        from datetime import datetime as dt
        planned_date = dt.strptime(planned_date_str, "%Y-%m-%d").date()

        pour_plan = await job_service.create_pour_plan(
            db=db,
            quote_id=quote_id,
            planned_date=planned_date,
            planned_time=data.get("planned_start_time"),
            request=request,
        )
        await db.commit()
        await db.refresh(pour_plan)

        return {
            "success": True,
            "plan": {
                "id": pour_plan.id,
                "quote_id": pour_plan.quote_id,
                "planned_date": pour_plan.planned_date.isoformat(),
                "planned_start_time": pour_plan.planned_start_time.strftime("%H:%M") if pour_plan.planned_start_time else None,
                "evaporation_rate": pour_plan.evaporation_rate,
                "risk_level": pour_plan.risk_level,
                "recommendations": pour_plan.recommendations,
            },
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/api/job/{quote_id}/plan")
async def api_update_pour_plan(
    request: Request,
    quote_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Update a pour plan for a job.

    Accepts:
    - planned_date: YYYY-MM-DD (optional)
    - planned_start_time: HH:MM (optional)
    """
    pour_plan = await job_service.get_pour_plan(db, quote_id)
    if not pour_plan:
        raise HTTPException(404, "Pour plan not found")

    try:
        # Parse date if provided
        planned_date = None
        if data.get("planned_date"):
            from datetime import datetime as dt
            planned_date = dt.strptime(data["planned_date"], "%Y-%m-%d").date()

        pour_plan = await job_service.update_pour_plan(
            db=db,
            pour_plan=pour_plan,
            planned_date=planned_date,
            planned_time=data.get("planned_start_time"),
            request=request,
        )
        await db.commit()
        await db.refresh(pour_plan)

        return {
            "success": True,
            "plan": {
                "id": pour_plan.id,
                "quote_id": pour_plan.quote_id,
                "planned_date": pour_plan.planned_date.isoformat(),
                "planned_start_time": pour_plan.planned_start_time.strftime("%H:%M") if pour_plan.planned_start_time else None,
                "evaporation_rate": pour_plan.evaporation_rate,
                "risk_level": pour_plan.risk_level,
                "recommendations": pour_plan.recommendations,
                "weather_snapshot": pour_plan.weather_snapshot,
            },
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/job/{quote_id}/plan/refresh")
async def api_refresh_pour_plan(
    request: Request,
    quote_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Refresh weather data for a pour plan."""
    pour_plan = await job_service.get_pour_plan(db, quote_id)
    if not pour_plan:
        raise HTTPException(404, "Pour plan not found")

    pour_plan = await job_service.refresh_pour_plan_weather(db, pour_plan, request)
    await db.commit()
    await db.refresh(pour_plan)

    return {
        "success": True,
        "plan": {
            "id": pour_plan.id,
            "weather_snapshot": pour_plan.weather_snapshot,
            "evaporation_rate": pour_plan.evaporation_rate,
            "risk_level": pour_plan.risk_level,
            "recommendations": pour_plan.recommendations,
            "updated_at": pour_plan.updated_at.isoformat() if pour_plan.updated_at else None,
        },
    }


@router.delete("/api/job/{quote_id}/plan")
async def api_delete_pour_plan(
    request: Request,
    quote_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete a pour plan."""
    pour_plan = await job_service.get_pour_plan(db, quote_id)
    if not pour_plan:
        raise HTTPException(404, "Pour plan not found")

    await job_service.delete_pour_plan(db, pour_plan, request)
    await db.commit()

    return {"success": True, "message": "Pour plan deleted"}


# =============================================================================
# POUR RESULT LOGGING
# =============================================================================

@router.post("/api/job/{quote_id}/result")
async def api_log_pour_result(
    request: Request,
    quote_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Log actual pour results for calibration.

    Accepts:
    - actual_admixture_used: str (e.g., "Sika Retarder N", "SikaRapid", "None")
    - actual_dose_ml: int
    - actual_initial_set_hours: float (when footprint test passed)
    - actual_finish_time: str (e.g., "11:30 AM")
    - actual_conditions_notes: str (e.g., "Windier than forecast")
    - prediction_accuracy: "spot_on" | "close" | "way_off"
    """
    # Get pour plan
    pour_plan = await job_service.get_pour_plan(db, quote_id)
    if not pour_plan:
        raise HTTPException(404, "Pour plan not found. Create a plan before logging results.")

    # Check for existing result
    existing = await job_service.get_pour_result(db, pour_plan.id)
    if existing:
        raise HTTPException(400, "Result already logged for this pour. Update not supported yet.")

    try:
        pour_result = await job_service.create_pour_result(
            db=db,
            pour_plan_id=pour_plan.id,
            data=data,
            request=request,
        )
        await db.commit()
        await db.refresh(pour_result)

        return {
            "success": True,
            "result": {
                "id": pour_result.id,
                "pour_plan_id": pour_result.pour_plan_id,
                "prediction_accuracy": pour_result.prediction_accuracy,
                "created_at": pour_result.created_at.isoformat() if pour_result.created_at else None,
            },
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/job/{quote_id}/result")
async def api_get_pour_result(
    quote_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get pour result for a job."""
    pour_plan = await job_service.get_pour_plan(db, quote_id)
    if not pour_plan:
        return {"success": True, "has_result": False, "result": None}

    pour_result = await job_service.get_pour_result(db, pour_plan.id)
    if not pour_result:
        return {"success": True, "has_result": False, "result": None}

    return {
        "success": True,
        "has_result": True,
        "result": {
            "id": pour_result.id,
            "pour_plan_id": pour_result.pour_plan_id,
            "predicted_initial_set_hours": pour_result.predicted_initial_set_hours,
            "predicted_finish_window_start": pour_result.predicted_finish_window_start,
            "predicted_finish_window_end": pour_result.predicted_finish_window_end,
            "recommended_admixture": pour_result.recommended_admixture,
            "recommended_dose_ml": pour_result.recommended_dose_ml,
            "actual_admixture_used": pour_result.actual_admixture_used,
            "actual_dose_ml": pour_result.actual_dose_ml,
            "actual_initial_set_hours": pour_result.actual_initial_set_hours,
            "actual_finish_time": pour_result.actual_finish_time,
            "actual_conditions_notes": pour_result.actual_conditions_notes,
            "prediction_accuracy": pour_result.prediction_accuracy,
            "created_at": pour_result.created_at.isoformat() if pour_result.created_at else None,
        },
    }


@router.get("/api/results")
async def api_get_all_results(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, le=200),
) -> dict:
    """Get all pour results for calibration analysis."""
    results = await job_service.get_all_pour_results(db, limit=limit)

    return {
        "success": True,
        "count": len(results),
        "results": [
            {
                "id": r.id,
                "pour_plan_id": r.pour_plan_id,
                "predicted_initial_set_hours": r.predicted_initial_set_hours,
                "actual_initial_set_hours": r.actual_initial_set_hours,
                "recommended_admixture": r.recommended_admixture,
                "actual_admixture_used": r.actual_admixture_used,
                "prediction_accuracy": r.prediction_accuracy,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in results
        ],
    }
