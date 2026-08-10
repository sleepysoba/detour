"""Presentation helpers for weather, itinerary, repair, and trace responses."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from typing import Any


def json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat(timespec="minutes")
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    return value


def weather_icon(code: int | None) -> str:
    if code is None:
        return "◌"
    if code == 0:
        return "☀"
    if code in {1, 2}:
        return "◑"
    if code == 3 or 45 <= code <= 48:
        return "☁"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "☂"
    if 71 <= code <= 77 or 85 <= code <= 86:
        return "❄"
    if 95 <= code <= 99:
        return "ϟ"
    return "◌"


def weather_days(trip: dict[str, Any], snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not snapshot:
        return []
    start = str(trip["start_date"])
    end = str(trip["end_date"])
    forecast = snapshot.get("forecast_json") or {}
    air = snapshot.get("air_quality_json") or {}
    aqi_by_day: dict[str, list[float]] = defaultdict(list)
    for row in air.get("hourly", []):
        value = row.get("us_aqi")
        if value is not None:
            aqi_by_day[str(row.get("time", ""))[:10]].append(float(value))
    result = []
    for row in forecast.get("daily", []):
        day = str(row.get("date") or "")
        if not start <= day <= end:
            continue
        aqi_values = aqi_by_day.get(day, [])
        result.append(
            {
                "date": day,
                "day_label": date.fromisoformat(day).strftime("%a"),
                "date_label": date.fromisoformat(day).strftime("%b %d").replace(" 0", " "),
                "icon": weather_icon(int(row["weather_code"]) if row.get("weather_code") is not None else None),
                "high": round(float(row["high_temperature_f"])) if row.get("high_temperature_f") is not None else None,
                "low": round(float(row["low_temperature_f"])) if row.get("low_temperature_f") is not None else None,
                "rain": round(float(row["precipitation_probability_pct"])) if row.get("precipitation_probability_pct") is not None else None,
                "aqi": round(sum(aqi_values) / len(aqi_values)) if aqi_values else None,
            }
        )
    return result


def group_itinerary(
    items: list[dict[str, Any]], resilience: dict[str, Any] | None
) -> list[dict[str, Any]]:
    evaluations = {
        int(item["itinerary_item_id"]): item
        for item in (resilience or {}).get("item_evaluations", [])
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        item_id = int(item["id"])
        evaluation = evaluations.get(item_id, {})
        row = json_ready(item)
        row["start_time"] = str(row.get("start_time") or "")[:5]
        row["end_time"] = str(row.get("end_time") or "")[:5]
        row["evaluation"] = json_ready(evaluation)
        grouped[str(row["day_date"])].append(row)
    return [
        {
            "date": day,
            "weekday": date.fromisoformat(day).strftime("%A"),
            "date_label": date.fromisoformat(day).strftime("%B %d").replace(" 0", " "),
            "items": rows,
        }
        for day, rows in sorted(grouped.items())
    ]


def decorate_repair_preview(preview: dict[str, Any], resilience_service: Any) -> dict[str, Any]:
    result = json_ready(preview)
    scenario = preview.get("scenario")
    before = resilience_service.evaluate_state(preview["trip_id"], preview["before_itinerary"], scenario)
    projected = resilience_service.evaluate_state(
        preview["trip_id"], preview["proposed_itinerary"], scenario
    )
    before_by_id = {int(row["itinerary_item_id"]): row for row in before["item_evaluations"]}
    after_by_id = {int(row["itinerary_item_id"]): row for row in projected["item_evaluations"]}
    for action in result["actions"]:
        item_id = int(action["itinerary_item_id"])
        action["before_evaluation"] = json_ready(before_by_id[item_id])
        action["after_evaluation"] = json_ready(after_by_id[item_id])
    result["impact"] = {
        "before_vulnerabilities": before["vulnerable_activity_count"],
        "projected_vulnerabilities": projected["vulnerable_activity_count"],
        "risks_resolved": max(
            0, before["vulnerable_activity_count"] - projected["vulnerable_activity_count"]
        ),
        "risks_remaining": projected["vulnerable_activity_count"],
    }
    return result


def trace_payload(trace_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    safe_events = json_ready(events)
    return {
        "trace_id": trace_id,
        "summary": {
            "tool_calls": sum(row.get("event_type") == "TOOL_CALLED" for row in events),
            "model_calls": sum(row.get("event_type") == "MODEL_REQUEST" for row in events),
            "observed_duration_ms": sum(int(row.get("duration_ms") or 0) for row in events),
        },
        "events": safe_events,
    }


__all__ = [
    "decorate_repair_preview",
    "group_itinerary",
    "json_ready",
    "trace_payload",
    "weather_days",
]
