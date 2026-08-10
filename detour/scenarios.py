"""Deterministic, non-persistent condition stress tests for Detour."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

SCENARIOS = {"RAINSTORM", "HEATWAVE", "POOR_AQI"}


class ScenarioValidationError(ValueError):
    """Raised when a caller requests an unsupported simulation."""


def normalize_scenario(scenario: str | None) -> str | None:
    """Normalize public scenario names while treating live/empty as no simulation."""
    if scenario is None:
        return None
    normalized = str(scenario).strip().replace("-", "_").replace(" ", "_").upper()
    if normalized in {"", "LIVE", "NONE"}:
        return None
    if normalized not in SCENARIOS:
        raise ScenarioValidationError(
            f"Scenario must be one of: {', '.join(sorted(SCENARIOS))}."
        )
    return normalized


def apply_scenario(conditions: dict[str, Any], scenario: str | None) -> dict[str, Any]:
    """Return a transformed copy of slot conditions; never mutate live input."""
    simulated = deepcopy(conditions)
    normalized = normalize_scenario(scenario)
    if normalized is None:
        return simulated

    if normalized == "RAINSTORM":
        simulated["precipitation_probability_pct"] = max(
            95.0, float(simulated.get("precipitation_probability_pct") or 0)
        )
        simulated["precipitation_inches"] = max(
            0.35, float(simulated.get("precipitation_inches") or 0)
        )
        simulated["wind_speed_mph"] = max(
            24.0, float(simulated.get("wind_speed_mph") or 0)
        )
        simulated["weather_code"] = 65
    elif normalized == "HEATWAVE":
        simulated["temperature_f"] = max(102.0, float(simulated.get("temperature_f") or 70))
        simulated["apparent_temperature_f"] = max(
            108.0, float(simulated.get("apparent_temperature_f") or 70)
        )
        simulated["uv_index"] = max(9.0, float(simulated.get("uv_index") or 0))
    elif normalized == "POOR_AQI":
        simulated["us_aqi"] = max(175.0, float(simulated.get("us_aqi") or 0))
        simulated["pm2_5"] = max(75.0, float(simulated.get("pm2_5") or 0))

    return simulated
