"""Explainable trip-level resilience over persisted live conditions."""

from __future__ import annotations

from datetime import date, time
from typing import Any

from detour.itinerary import conditions_for_slot
from detour.models import TripRepository
from detour.scenarios import apply_scenario, normalize_scenario
from detour.scoring import evaluate_activity_conditions

RESILIENCE_POLICY_VERSION = "detour-resilience-v1"


class ResilienceError(ValueError):
    """Raised when persisted trip inputs cannot be evaluated."""


def _iso(value: Any) -> str:
    if isinstance(value, (date, time)):
        return value.isoformat(timespec="minutes") if isinstance(value, time) else value.isoformat()
    return str(value)


def aggregate_resilience(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate deterministic item scores, weighting exposed low scores more heavily."""
    if not evaluations:
        raise ResilienceError("A trip must contain itinerary items before resilience can be calculated.")

    weighted_risks: list[float] = []
    vulnerable_count = 0
    for item in evaluations:
        score = int(item["condition_score"])
        sensitivity = min(1.0, max(0.0, float(item.get("weather_sensitivity") or 0.5)))
        state = item["status"]
        state_penalty = 10 if state == "AT_RISK" else 3 if state == "CAUTION" else 0
        weighted_risks.append((100 - score) * max(sensitivity, 0.25) + state_penalty)
        if item["vulnerable"]:
            vulnerable_count += 1

    score = round(max(0.0, min(100.0, 100 - sum(weighted_risks) / len(weighted_risks))))
    label = "RESILIENT" if score >= 80 else "WATCH" if score >= 60 else "VULNERABLE"
    if vulnerable_count:
        summary = (
            f"{vulnerable_count} of {len(evaluations)} activities are vulnerable; "
            "the listed condition factors drive the score."
        )
    elif label == "WATCH":
        summary = "No activity is currently at risk, but exposed plans warrant attention."
    else:
        summary = "The itinerary is well matched to the evaluated conditions."
    return {
        "score": score,
        "label": label,
        "vulnerable_activity_count": vulnerable_count,
        "summary": summary,
        "policy_version": RESILIENCE_POLICY_VERSION,
    }


def evaluate_items(
    items: list[dict[str, Any]],
    *,
    forecast: dict[str, Any],
    air_quality: dict[str, Any] | None,
    scenario: str | None = None,
) -> dict[str, Any]:
    """Evaluate an itinerary state against one immutable live snapshot."""
    normalized_scenario = normalize_scenario(scenario)
    evaluations: list[dict[str, Any]] = []
    for item in items:
        day_value = _iso(item["day_date"])
        start_value = _iso(item["start_time"])
        live_conditions = conditions_for_slot(
            forecast,
            air_quality,
            day_date=day_value,
            start_time=start_value,
        )
        conditions = apply_scenario(live_conditions, normalized_scenario)
        activity = evaluate_activity_conditions(item, conditions)
        sensitivity = float(item.get("weather_sensitivity") or 0.5)
        vulnerable = activity["state"] == "AT_RISK" or (
            activity["score"] < 65 and sensitivity >= 0.6
        )
        evaluations.append(
            {
                "itinerary_item_id": int(item["id"]),
                "attraction_id": int(item["attraction_id"]),
                "title": item["title"],
                "day_date": day_value,
                "start_time": start_value,
                "condition_score": activity["score"],
                "status": activity["state"],
                "primary_risk_factors": activity["reasons"],
                "weather_sensitivity": sensitivity,
                "indoor_outdoor": item.get("indoor_outdoor") or "mixed",
                "vulnerable": vulnerable,
                "signals": activity["signals"],
                "penalties": activity["penalties"],
            }
        )
    aggregate = aggregate_resilience(evaluations)
    return {
        **aggregate,
        "scenario": normalized_scenario or "LIVE",
        "simulated": normalized_scenario is not None,
        "item_evaluations": evaluations,
    }


class TripResilienceService:
    """Load current persisted trip state and evaluate it without calling upstream APIs."""

    def __init__(self, trips: TripRepository):
        self.trips = trips

    def evaluate_trip_resilience(self, trip_id: int, scenario: str | None = None) -> dict[str, Any]:
        trip = self.trips.get_trip(trip_id)
        if trip is None:
            raise ResilienceError("Trip was not found.")
        items = self.trips.get_itinerary(trip_id)
        snapshot = self.trips.get_latest_weather_snapshot(trip_id)
        if snapshot is None:
            raise ResilienceError("No live weather snapshot is available for this trip.")
        result = evaluate_items(
            items,
            forecast=snapshot["forecast_json"],
            air_quality=snapshot.get("air_quality_json"),
            scenario=scenario,
        )
        result.update(
            {
                "trip_id": trip_id,
                "destination": trip["destination_name"],
                "weather_snapshot_id": int(snapshot["id"]),
            }
        )
        if not result["simulated"]:
            self.trips.update_live_resilience(trip_id, result["score"])
        return result

    def evaluate_state(
        self,
        trip_id: int,
        items: list[dict[str, Any]],
        scenario: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.trips.get_latest_weather_snapshot(trip_id)
        if snapshot is None:
            raise ResilienceError("No live weather snapshot is available for this trip.")
        return evaluate_items(
            items,
            forecast=snapshot["forecast_json"],
            air_quality=snapshot.get("air_quality_json"),
            scenario=scenario,
        )
