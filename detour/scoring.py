"""Explainable Phase 2 activity-condition scoring (not a learned model)."""

from __future__ import annotations

from typing import Any

POLICY_VERSION = "detour-phase2-v1"


def _number(conditions: dict, key: str, default: float) -> float:
    value = conditions.get(key)
    return default if value is None else float(value)


def _rain_penalty(probability: float) -> tuple[int, str | None]:
    if probability <= 20:
        return 0, None
    if probability <= 40:
        return 8, "Some rain risk"
    if probability <= 60:
        return 18, "Moderate rain probability"
    if probability <= 80:
        return 30, "High rain probability"
    return 42, "Very high rain probability"


def _temperature_penalty(temperature_f: float) -> tuple[int, str | None]:
    if 45 <= temperature_f <= 88:
        return 0, None
    if 35 <= temperature_f < 45:
        return 10, "Cold temperature"
    if 88 < temperature_f <= 95:
        return 12, "High temperature"
    if temperature_f < 35:
        return 25, "Very cold temperature"
    return 28, "Extreme heat"


def _wind_penalty(wind_mph: float) -> tuple[int, str | None]:
    if wind_mph < 15:
        return 0, None
    if wind_mph < 25:
        return 8, "Breezy conditions"
    if wind_mph < 35:
        return 18, "Strong wind"
    return 30, "Very strong wind"


def _aqi_penalty(aqi: float) -> tuple[int, str | None]:
    if aqi <= 50:
        return 0, None
    if aqi <= 100:
        return 6, "Moderate AQI"
    if aqi <= 150:
        return 20, "Poor AQI for outdoor activity"
    return 38, "Very poor AQI for outdoor activity"


def _uv_penalty(uv_index: float) -> tuple[int, str | None]:
    if uv_index < 6:
        return 0, None
    if uv_index < 8:
        return 6, "High UV exposure"
    if uv_index < 11:
        return 16, "Very high UV exposure"
    return 24, "Extreme UV exposure"


def evaluate_activity_conditions(activity: dict, conditions: dict) -> dict[str, Any]:
    """Score an activity from deterministic weather/AQI facts and stored metadata."""
    environment = str(activity.get("indoor_outdoor") or "mixed").casefold()
    if environment not in {"indoor", "outdoor", "mixed"}:
        environment = "mixed"
    try:
        sensitivity = min(1.0, max(0.0, float(activity.get("weather_sensitivity", 0.5))))
    except (TypeError, ValueError):
        sensitivity = 0.5
    exposure = {"indoor": 0.15, "mixed": 0.6, "outdoor": 1.0}[environment]
    activity_factor = 1.2 if activity.get("activity_level") == "high" else 1.0

    rain = _number(conditions, "precipitation_probability_pct", 0)
    temperature = max(
        _number(conditions, "temperature_f", 70),
        _number(conditions, "apparent_temperature_f", 70),
    )
    wind = _number(conditions, "wind_speed_mph", 0)
    uv_index = _number(conditions, "uv_index", 0)
    aqi_value = conditions.get("us_aqi")

    rain_penalty, rain_reason = _rain_penalty(rain)
    temperature_penalty, temperature_reason = _temperature_penalty(temperature)
    wind_penalty, wind_reason = _wind_penalty(wind)
    aqi_penalty, aqi_reason = _aqi_penalty(float(aqi_value)) if aqi_value is not None else (0, None)
    uv_penalty, uv_reason = _uv_penalty(uv_index)

    penalties = {
        "rain": round(rain_penalty * sensitivity * exposure),
        "temperature": round(temperature_penalty * exposure),
        "wind": round(wind_penalty * sensitivity * exposure),
        "aqi": round(aqi_penalty * exposure * activity_factor),
        "uv": round(uv_penalty * exposure),
    }
    score = max(0, min(100, 100 - sum(penalties.values())))
    state = "GO" if score >= 80 else "CAUTION" if score >= 55 else "AT_RISK"

    reasons: list[str] = []
    for penalty, reason in (
        (penalties["rain"], rain_reason),
        (penalties["aqi"], aqi_reason),
        (penalties["temperature"], temperature_reason),
        (penalties["wind"], wind_reason),
        (penalties["uv"], uv_reason),
    ):
        if penalty > 0 and reason and reason not in reasons:
            reasons.append(reason)
    if not reasons:
        reasons.append(
            "Indoor activity is weather resilient"
            if environment == "indoor"
            else "Conditions look favorable"
        )

    return {
        "score": score,
        "state": state,
        "reasons": reasons[:3],
        "signals": {
            "precipitation_probability_pct": round(rain),
            "temperature_f": round(temperature, 1),
            "wind_speed_mph": round(wind, 1),
            "us_aqi": round(float(aqi_value)) if aqi_value is not None else None,
            "uv_index": round(uv_index, 1),
        },
        "penalties": penalties,
        "policy_version": POLICY_VERSION,
    }
