"""
Calibration analysis for pour planner predictions.

Compares predicted vs actual pour results to show accuracy trends,
bias detection, and admixture effectiveness.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PourResult, PourPlan


async def get_calibration_stats(db: AsyncSession) -> dict:
    """
    Build calibration dashboard statistics.

    Returns:
        Dict with summary stats, accuracy breakdown, and timeseries data.
    """
    # Get all results with their plans
    result = await db.execute(
        select(PourResult, PourPlan)
        .join(PourPlan, PourResult.pour_plan_id == PourPlan.id)
        .order_by(PourResult.created_at.desc())
    )
    rows = result.all()

    if not rows:
        return {
            "total_pours": 0,
            "accuracy_breakdown": {"spot_on": 0, "close": 0, "way_off": 0},
            "avg_set_time_error_hours": 0,
            "set_time_bias": "none",
            "timeseries": [],
            "admixture_usage": [],
        }

    total = len(rows)

    # Accuracy breakdown
    accuracy_counts = {"spot_on": 0, "close": 0, "way_off": 0}
    set_time_errors = []
    timeseries = []
    admixture_map = {}

    for pour_result, pour_plan in rows:
        # Count accuracy ratings
        rating = pour_result.prediction_accuracy or "close"
        if rating in accuracy_counts:
            accuracy_counts[rating] += 1

        # Calculate set time error (actual - predicted)
        if (
            pour_result.actual_initial_set_hours is not None
            and pour_result.predicted_initial_set_hours is not None
        ):
            error = pour_result.actual_initial_set_hours - pour_result.predicted_initial_set_hours
            set_time_errors.append(error)

            timeseries.append({
                "date": pour_result.created_at.strftime("%Y-%m-%d") if pour_result.created_at else None,
                "predicted": round(pour_result.predicted_initial_set_hours, 2),
                "actual": round(pour_result.actual_initial_set_hours, 2),
                "error": round(error, 2),
                "accuracy": rating,
                "risk_level": pour_plan.risk_level,
            })

        # Admixture tracking
        admix = pour_result.actual_admixture_used or "None"
        if admix not in admixture_map:
            admixture_map[admix] = {"count": 0, "spot_on": 0, "close": 0, "way_off": 0}
        admixture_map[admix]["count"] += 1
        if rating in admixture_map[admix]:
            admixture_map[admix][rating] += 1

    # Calculate average error and bias
    avg_error = sum(set_time_errors) / len(set_time_errors) if set_time_errors else 0
    if avg_error > 0.3:
        bias = "under-predicting"  # Actual takes longer than predicted
    elif avg_error < -0.3:
        bias = "over-predicting"   # Actual sets faster than predicted
    else:
        bias = "well-calibrated"

    # Admixture list
    admixture_usage = [
        {"name": name, **stats}
        for name, stats in sorted(admixture_map.items(), key=lambda x: -x[1]["count"])
    ]

    return {
        "total_pours": total,
        "accuracy_breakdown": accuracy_counts,
        "accuracy_pct": {
            k: round(v / total * 100) if total > 0 else 0
            for k, v in accuracy_counts.items()
        },
        "avg_set_time_error_hours": round(avg_error, 2),
        "set_time_bias": bias,
        "timeseries": timeseries[-30:],  # Last 30 for chart
        "admixture_usage": admixture_usage,
    }
