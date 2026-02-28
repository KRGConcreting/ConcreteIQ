"""
Finishing Timeline Calculator.

Calculates actual clock times for concrete finishing milestones based on
pour start time and set time predictions.

Also handles exposed aggregate wash window calculations.
"""

from datetime import datetime, time, timedelta
from typing import Dict, Any, List, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

SYDNEY_TZ = ZoneInfo("Australia/Sydney")


# =============================================================================
# FINISHING MILESTONES (as percentage of initial set time)
# =============================================================================

MILESTONES = [
    {"name": "Pour & Screed", "percent": 0, "notes": "Begin placing and screeding"},
    {"name": "Bullfloat", "offset_minutes": 30, "notes": "After screeding, before bleed water"},
    {"name": "Spray Aliphatic Alcohol", "percent": 40, "notes": "Apply evaporation retarder"},
    {"name": "Bleed Water Gone", "percent": 55, "notes": "Surface water evaporated"},
    {"name": "Ready to Float", "percent": 65, "notes": "Footprint test - slight impression", "window_start": True},
    {"name": "Steel Trowel", "percent": 75, "notes": "First trowel pass"},
    {"name": "Sponge Finish", "percent": 90, "notes": "Final finish opportunity", "window_end": True},
    {"name": "Initial Set", "percent": 100, "notes": "Can't work surface anymore"},
    {"name": "Final Set", "is_final": True, "notes": "Concrete fully set"},
]


# =============================================================================
# EXPOSED AGGREGATE WASH WINDOWS
# =============================================================================

# Temperature ranges and corresponding wash windows in hours
WASH_WINDOWS = {
    "extreme_cold": {  # <10°C
        "min_hours": 20,
        "max_hours": 28,
        "recommendation": "next_morning",
        "tip": "Very cold - wash next morning 6-8 AM. May need pressure washer.",
    },
    "cold": {  # 10-15°C
        "min_hours": 18,
        "max_hours": 24,
        "recommendation": "next_morning",
        "tip": "Cold conditions - wash next morning between 6-8 AM",
    },
    "cool": {  # 15-23°C
        "min_hours": 12,
        "max_hours": 18,
        "recommendation": "overnight",
        "tip": "Moderate temp - wash 12-18 hours after pour (early morning)",
    },
    "warm": {  # 23-30°C
        "min_hours": 6,
        "max_hours": 12,
        "recommendation": "same_evening",
        "tip": "Warm conditions - test at 6 hours, may wash same evening",
    },
    "hot": {  # >30°C
        "min_hours": 4,
        "max_hours": 8,
        "recommendation": "same_day",
        "tip": "Hot conditions - monitor closely from 4 hours. Window is short!",
    },
}


def format_time_12hr(dt: datetime) -> str:
    """Format datetime to '7:30 AM' style."""
    hour = dt.hour
    minute = dt.minute
    am_pm = "AM" if hour < 12 else "PM"

    if hour == 0:
        hour = 12
    elif hour > 12:
        hour -= 12

    if minute == 0:
        return f"{hour}:{minute:02d} {am_pm}"
    else:
        return f"{hour}:{minute:02d} {am_pm}"


def format_time_short(dt: datetime) -> str:
    """Format datetime to '7:30 AM' style, dropping minutes if zero."""
    hour = dt.hour
    minute = dt.minute
    am_pm = "AM" if hour < 12 else "PM"

    if hour == 0:
        hour = 12
    elif hour > 12:
        hour -= 12

    if minute == 0:
        return f"{hour} {am_pm}"
    else:
        return f"{hour}:{minute:02d} {am_pm}"


def parse_time_string(time_str: str) -> time:
    """Parse HH:MM string to time object."""
    parts = time_str.split(":")
    return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


def calculate_finishing_timeline(
    pour_start_time: str,  # "07:00"
    initial_set_hours: float,
    final_set_hours: float,
    pour_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Calculate actual clock times for finishing milestones.

    Milestones (as % of initial set):
    - Pour & screed: start time (0%)
    - Bull float: +30 min from start
    - Bleed water disappears: 55% of initial set
    - Ready to float: 65% of initial set
    - Power trowel opens: 70% of initial set
    - Power trowel closes: 95% of initial set
    - Initial set: 100%
    - Final set: based on final_set_hours

    Args:
        pour_start_time: Pour start time in HH:MM format
        initial_set_hours: Predicted initial set time in hours
        final_set_hours: Predicted final set time in hours
        pour_date: Optional date for the pour (defaults to today)

    Returns:
        Dict with all milestone times and formatted output
    """
    # Parse start time
    start_time = parse_time_string(pour_start_time)

    # Use today if no date provided
    if pour_date is None:
        pour_date = datetime.now(SYDNEY_TZ)

    # Create start datetime
    start_dt = pour_date.replace(
        hour=start_time.hour,
        minute=start_time.minute,
        second=0,
        microsecond=0,
    )
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=SYDNEY_TZ)

    # Calculate initial set datetime
    initial_set_minutes = int(initial_set_hours * 60)
    initial_set_dt = start_dt + timedelta(minutes=initial_set_minutes)

    # Calculate final set datetime
    final_set_minutes = int(final_set_hours * 60)
    final_set_dt = start_dt + timedelta(minutes=final_set_minutes)

    # Build milestone list
    milestones = []
    timeline = {}

    for milestone in MILESTONES:
        name = milestone["name"]
        notes = milestone.get("notes", "")

        if "offset_minutes" in milestone:
            # Fixed offset from start
            offset = milestone["offset_minutes"]
            milestone_dt = start_dt + timedelta(minutes=offset)
        elif milestone.get("is_final"):
            # Final set
            milestone_dt = final_set_dt
        elif "percent" in milestone:
            # Percentage of initial set
            percent = milestone["percent"]
            offset_minutes = int(initial_set_minutes * percent / 100)
            milestone_dt = start_dt + timedelta(minutes=offset_minutes)
        else:
            continue

        time_str = format_time_12hr(milestone_dt)

        milestones.append({
            "name": name,
            "time": time_str,
            "datetime": milestone_dt.isoformat(),
            "notes": notes,
        })

        # Add to flat timeline dict for easy access
        key = name.lower().replace(" ", "_").replace("&", "and")
        timeline[key] = time_str

    # Calculate finishing window
    # Ready to float (65%) to Sponge Finish (90%)
    float_ready_minutes = int(initial_set_minutes * 0.65)
    sponge_finish_minutes = int(initial_set_minutes * 0.90)
    window_duration_hours = (sponge_finish_minutes - float_ready_minutes) / 60

    float_ready_dt = start_dt + timedelta(minutes=float_ready_minutes)
    sponge_finish_dt = start_dt + timedelta(minutes=sponge_finish_minutes)

    return {
        "pour_start": format_time_12hr(start_dt),
        "initial_set": format_time_12hr(initial_set_dt),
        "final_set": format_time_12hr(final_set_dt),
        "milestones": milestones,
        "timeline": timeline,
        "finishing_window": {
            "opens": format_time_12hr(float_ready_dt),
            "closes": format_time_12hr(sponge_finish_dt),
            "duration_hours": round(window_duration_hours, 1),
            "summary": f"FINISHING WINDOW: {format_time_short(float_ready_dt)} - {format_time_short(sponge_finish_dt)} ({window_duration_hours:.1f} hours)",
        },
        "initial_set_hours": initial_set_hours,
        "final_set_hours": final_set_hours,
    }


def get_wash_window_category(effective_temp: float) -> str:
    """Get wash window category based on temperature."""
    if effective_temp < 10:
        return "extreme_cold"
    elif effective_temp < 15:
        return "cold"
    elif effective_temp < 23:
        return "cool"
    elif effective_temp < 30:
        return "warm"
    else:
        return "hot"


def calculate_wash_window(effective_temp: float) -> Dict[str, Any]:
    """
    Calculate exposed aggregate wash window based on temperature.

    Windows:
    - <10°C: 20-28 hours (next morning, may need pressure washer)
    - 10-15°C: 18-24 hours (next morning)
    - 15-23°C: 12-18 hours (early morning)
    - 23-30°C: 6-12 hours (same evening possible)
    - >30°C: 4-8 hours (same day, monitor closely)

    Args:
        effective_temp: Effective temperature in °C

    Returns:
        Dict with wash window timing and recommendations
    """
    category = get_wash_window_category(effective_temp)
    window = WASH_WINDOWS[category]

    return {
        "category": category,
        "min_hours": window["min_hours"],
        "max_hours": window["max_hours"],
        "recommendation": window["recommendation"],
        "tip": window["tip"],
        "effective_temp": effective_temp,
    }


def calculate_wash_times(
    pour_start_time: str,
    wash_window: Dict[str, Any],
    pour_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Calculate actual wash times based on pour start and window.

    Args:
        pour_start_time: Pour start time in HH:MM format
        wash_window: Output from calculate_wash_window()
        pour_date: Optional date for the pour (defaults to today)

    Returns:
        Dict with earliest, recommended, and latest wash times
    """
    # Parse start time
    start_time = parse_time_string(pour_start_time)

    # Use today if no date provided
    if pour_date is None:
        pour_date = datetime.now(SYDNEY_TZ)

    # Create start datetime
    start_dt = pour_date.replace(
        hour=start_time.hour,
        minute=start_time.minute,
        second=0,
        microsecond=0,
    )
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=SYDNEY_TZ)

    min_hours = wash_window["min_hours"]
    max_hours = wash_window["max_hours"]

    earliest_dt = start_dt + timedelta(hours=min_hours)
    latest_dt = start_dt + timedelta(hours=max_hours)

    # Calculate recommended time (middle of window, but adjusted for practicality)
    # If earliest is after midnight but before 6am, recommend 6am
    # If latest is before midnight, recommend middle of window
    mid_hours = (min_hours + max_hours) / 2
    recommended_dt = start_dt + timedelta(hours=mid_hours)

    # Adjust recommended time for practicality
    if recommended_dt.hour < 6 and recommended_dt.date() > start_dt.date():
        # Early morning next day - recommend 6am
        recommended_dt = recommended_dt.replace(hour=6, minute=0)
    elif earliest_dt.hour >= 16 and earliest_dt.date() == start_dt.date():
        # Same evening possible - recommend earliest practical time
        recommended_dt = earliest_dt

    # Format for display
    def format_with_day(dt: datetime, start: datetime) -> str:
        time_str = format_time_12hr(dt)
        if dt.date() > start.date():
            return f"{time_str} tomorrow"
        else:
            return f"{time_str} today"

    return {
        "earliest": format_with_day(earliest_dt, start_dt),
        "earliest_datetime": earliest_dt.isoformat(),
        "recommended": format_with_day(recommended_dt, start_dt),
        "recommended_datetime": recommended_dt.isoformat(),
        "latest": format_with_day(latest_dt, start_dt),
        "latest_datetime": latest_dt.isoformat(),
        "apply_retarder": "After final bull float (before surface dries)",
        "test_instruction": "Always do scratch test before full wash",
        "tip": wash_window["tip"],
    }


def calculate_exposed_aggregate_schedule(
    pour_start_time: str,
    effective_temp: float,
    pour_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Calculate complete exposed aggregate schedule.

    Combines wash window and wash times into a single output.

    Args:
        pour_start_time: Pour start time in HH:MM format
        effective_temp: Effective temperature in °C
        pour_date: Optional date for the pour

    Returns:
        Complete exposed aggregate schedule with all times and recommendations
    """
    wash_window = calculate_wash_window(effective_temp)
    wash_times = calculate_wash_times(pour_start_time, wash_window, pour_date)

    return {
        "window": wash_window,
        "times": wash_times,
        "summary": {
            "apply_retarder": "After final bull float",
            "earliest_wash_test": wash_times["earliest"],
            "recommended_wash": wash_times["recommended"],
            "latest_wash": wash_times["latest"],
        },
    }


def generate_timeline_text(
    pour_start_time: str,
    initial_set_hours: float,
    final_set_hours: float,
    is_exposed: bool = False,
    effective_temp: float = 25.0,
    sika_recommendation: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Generate text-based timeline output (for CLI or simple display).

    Args:
        pour_start_time: Pour start time in HH:MM format
        initial_set_hours: Predicted initial set time in hours
        final_set_hours: Predicted final set time in hours
        is_exposed: Whether this is exposed aggregate
        effective_temp: Effective temperature in °C
        sika_recommendation: Optional Sika admixture recommendation

    Returns:
        Formatted text timeline
    """
    timeline = calculate_finishing_timeline(
        pour_start_time, initial_set_hours, final_set_hours
    )

    lines = []
    lines.append(f"Pour Start: {timeline['pour_start']}")
    lines.append(f"Effective Temp: {effective_temp:.0f}°C")

    if sika_recommendation and sika_recommendation.get("recommended"):
        product = sika_recommendation.get("product")
        dose = sika_recommendation.get("dose_per_100kg")
        lines.append(f"Admixture: {product} @ {dose}mL/100kg cement")
    else:
        lines.append("Admixture: None required")

    lines.append("")
    lines.append("TIMELINE:")

    for milestone in timeline["milestones"]:
        lines.append(f"├─ {milestone['time']}  - {milestone['name']}")

    lines.append("")
    lines.append(timeline["finishing_window"]["summary"])

    if is_exposed:
        exposed = calculate_exposed_aggregate_schedule(
            pour_start_time, effective_temp
        )
        lines.append("")
        lines.append("EXPOSED AGGREGATE:")
        lines.append(f"Apply surface retarder: {exposed['summary']['apply_retarder']}")
        lines.append(f"Earliest wash test: {exposed['summary']['earliest_wash_test']}")
        lines.append(f"Recommended wash: {exposed['summary']['recommended_wash']}")
        lines.append(f"Latest wash: {exposed['summary']['latest_wash']}")

    return "\n".join(lines)
