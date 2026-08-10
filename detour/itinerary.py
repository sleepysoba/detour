"""Initial real-attraction itinerary generation for Phase 2."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Any, Callable
from uuid import uuid4

from detour.destination import AttractionSearchService
from detour.llm import LLMService
from detour.models import TripRepository
from detour.retrieval import AttractionRepository
from detour.scoring import evaluate_activity_conditions
from detour.tracing import TraceService
from detour.trips import live_conditions_available
from detour.weather import OpenMeteoError, OpenMeteoService
from detour.wikimedia import filter_attraction_candidates

logger = logging.getLogger(__name__)

PACE_COUNTS = {"relaxed": 2, "balanced": 3, "packed": 4}
PACE_TIMES = {
    "relaxed": ("09:30", "14:00"),
    "balanced": ("09:00", "13:00", "17:00"),
    "packed": ("09:00", "12:00", "15:00", "18:00"),
}


class ItineraryValidationError(ValueError):
    """Raised when model output violates supplied attraction or schedule constraints."""


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _json_object(content: str) -> dict:
    cleaned = (content or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ItineraryValidationError("Itinerary response was not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ItineraryValidationError("Itinerary response must be a JSON object.")
    return payload


def build_schedule_slots(
    *, start_date: date, end_date: date, pace: str, candidate_count: int
) -> list[dict[str, str]]:
    """Distribute available unique attractions evenly across trip days."""
    days = [start_date + timedelta(days=offset) for offset in range((end_date - start_date).days + 1)]
    possible = [(day, slot_time) for slot_time in PACE_TIMES[pace] for day in days]
    selected = possible[: min(len(possible), candidate_count)]
    return [
        {"day_date": day.isoformat(), "start_time": start_time}
        for day, start_time in sorted(selected, key=lambda value: (value[0], value[1]))
    ]


def validate_itinerary_selection(
    payload: dict,
    *,
    candidates: list[dict],
    slots: list[dict[str, str]],
) -> list[dict]:
    """Reject invented IDs, duplicate attractions, missing slots, and bad times."""
    rows = payload.get("items")
    if not isinstance(rows, list) or len(rows) != len(slots):
        raise ItineraryValidationError(f"Itinerary must contain exactly {len(slots)} items.")
    candidate_ids = {int(candidate["id"]) for candidate in candidates}
    required_slots = {(slot["day_date"], slot["start_time"]) for slot in slots}
    seen_ids: set[int] = set()
    seen_slots: set[tuple[str, str]] = set()
    validated: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ItineraryValidationError("Each itinerary item must be an object.")
        raw_id = row.get("attraction_id")
        if isinstance(raw_id, bool):
            raise ItineraryValidationError("Attraction identifiers must be integers.")
        try:
            attraction_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise ItineraryValidationError("Attraction identifiers must be integers.") from exc
        if attraction_id not in candidate_ids:
            raise ItineraryValidationError(f"Unknown attraction ID {attraction_id}.")
        if attraction_id in seen_ids:
            raise ItineraryValidationError("An attraction cannot appear twice in one trip.")
        day_value = str(row.get("day_date") or "")
        time_value = str(row.get("start_time") or "")
        try:
            parsed_time = time.fromisoformat(time_value)
        except ValueError as exc:
            raise ItineraryValidationError("Itinerary times must use HH:MM format.") from exc
        normalized_time = parsed_time.strftime("%H:%M")
        slot = (day_value, normalized_time)
        if slot not in required_slots or slot in seen_slots:
            raise ItineraryValidationError("Itinerary output did not preserve the required schedule slots.")
        seen_ids.add(attraction_id)
        seen_slots.add(slot)
        validated.append(
            {"attraction_id": attraction_id, "day_date": day_value, "start_time": normalized_time}
        )
    if seen_slots != required_slots:
        raise ItineraryValidationError("Itinerary output omitted a required schedule slot.")
    return sorted(validated, key=lambda row: (row["day_date"], row["start_time"]))


def conditions_for_slot(
    forecast: dict,
    air_quality: dict | None,
    *,
    day_date: str,
    start_time: str,
) -> dict:
    """Select the closest normalized hourly conditions, with daily weather fallback."""
    target_hour = int(start_time.split(":", 1)[0])
    hourly = [row for row in forecast.get("hourly", []) if str(row.get("time", "")).startswith(day_date)]
    weather_row = min(
        hourly,
        key=lambda row: abs(int(str(row["time"])[11:13]) - target_hour),
        default=None,
    )
    if weather_row:
        conditions = {
            "temperature_f": weather_row.get("temperature_f"),
            "precipitation_probability_pct": weather_row.get("precipitation_probability_pct"),
            "wind_speed_mph": weather_row.get("wind_speed_mph"),
            "weather_code": weather_row.get("weather_code"),
        }
    else:
        daily = next((row for row in forecast.get("daily", []) if row.get("date") == day_date), {})
        high = daily.get("high_temperature_f")
        low = daily.get("low_temperature_f")
        conditions = {
            "temperature_f": (float(high) + float(low)) / 2 if high is not None and low is not None else None,
            "precipitation_probability_pct": daily.get("precipitation_probability_pct"),
            "wind_speed_mph": daily.get("max_wind_speed_mph"),
            "weather_code": daily.get("weather_code"),
        }

    aqi_rows = [
        row for row in (air_quality or {}).get("hourly", []) if str(row.get("time", "")).startswith(day_date)
    ]
    aqi_row = min(
        aqi_rows,
        key=lambda row: abs(int(str(row["time"])[11:13]) - target_hour),
        default=None,
    )
    conditions["us_aqi"] = aqi_row.get("us_aqi") if aqi_row else None
    return conditions


class ItineraryService:
    """Retrieve, constrain, ask Llama, validate, score, and persist an initial itinerary."""

    def __init__(
        self,
        *,
        trips: TripRepository,
        attractions: AttractionRepository,
        search: AttractionSearchService,
        weather: OpenMeteoService,
        llm: LLMService,
        traces: TraceService | None = None,
        today_provider: Callable[[], date] = date.today,
    ):
        self.trips = trips
        self.attractions = attractions
        self.search = search
        self.weather = weather
        self.llm = llm
        self.traces = traces
        self.today_provider = today_provider

    def _record(self, *, trace_id: str, trip_id: int, **event: Any) -> None:
        if self.traces:
            self.traces.record_safe(
                trace_id=trace_id,
                trip_id=trip_id,
                model=self.llm.model,
                **event,
            )

    def _model_selection(
        self,
        *,
        trip: dict,
        candidates: list[dict],
        slots: list[dict[str, str]],
        slot_scores: dict[tuple[int, str, str], dict] | None,
        trace_id: str,
    ) -> tuple[list[dict], bool]:
        candidate_payload = []
        for candidate in candidates:
            candidate_row = {
                    "id": candidate["id"],
                    "name": candidate["name"],
                    "category": candidate.get("category"),
                    "indoor_outdoor": candidate.get("indoor_outdoor"),
                    "activity_level": candidate.get("activity_level"),
                    "tags": candidate.get("tags") or [],
                    "summary": candidate.get("traveler_summary") or candidate.get("description", "")[:240],
                }
            if slot_scores is not None:
                candidate_row["slot_condition_scores"] = {
                        f"{slot['day_date']} {slot['start_time']}": slot_scores[
                            (candidate["id"], slot["day_date"], slot["start_time"])
                        ]["score"]
                        for slot in slots
                    }
            candidate_payload.append(candidate_row)
        messages = [
            {
                "role": "system",
                "content": (
                    "Build an itinerary using only supplied attraction IDs and every supplied schedule slot. "
                    "Use each attraction at most once. Preserve slot dates/times exactly. Prefer the traveler's "
                    "preferences and variety. When deterministic condition scores are supplied, prefer higher "
                    "scores. Never invent or rename "
                    "an attraction. Return JSON only as {\"items\":[{\"attraction_id\":1," 
                    "\"day_date\":\"YYYY-MM-DD\",\"start_time\":\"HH:MM\"}]}."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "destination": trip["destination_name"],
                        "preferences": trip.get("preferences") or [],
                        "pace": trip["pace"],
                        "schedule_slots": slots,
                        "candidates": candidate_payload,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        for attempt in range(2):
            self._record(
                trace_id=trace_id,
                trip_id=trip["id"],
                event_type="MODEL_REQUEST",
                status="started",
                input_summary={"operation": "initial_itinerary", "attempt": attempt + 1, "candidate_count": len(candidates)},
            )
            try:
                message, duration_ms = self.llm.create_message(messages)
                content = getattr(message, "content", None)
                selection = validate_itinerary_selection(
                    _json_object(content), candidates=candidates, slots=slots
                )
                self._record(
                    trace_id=trace_id,
                    trip_id=trip["id"],
                    event_type="MODEL_RESPONSE",
                    status="ok",
                    duration_ms=duration_ms,
                    output_summary={"attempt": attempt + 1, "validated_item_count": len(selection)},
                )
                return selection, False
            except Exception as exc:
                logger.warning("itinerary_model_output_invalid attempt=%d error_type=%s", attempt + 1, type(exc).__name__)
                self._record(
                    trace_id=trace_id,
                    trip_id=trip["id"],
                    event_type="MODEL_RESPONSE",
                    status="error",
                    output_summary={"attempt": attempt + 1, "error_type": type(exc).__name__},
                )
                messages.append(
                    {
                        "role": "user",
                        "content": "The prior response violated the ID or slot contract. Return corrected JSON only.",
                    }
                )
        return self._fallback_selection(candidates, slots, slot_scores), True

    @staticmethod
    def _fallback_selection(
        candidates: list[dict],
        slots: list[dict[str, str]],
        slot_scores: dict[tuple[int, str, str], dict] | None,
    ) -> list[dict]:
        unused = {int(candidate["id"]): candidate for candidate in candidates}
        used_categories: dict[str, int] = {}
        selection: list[dict] = []
        for slot in slots:
            def rank(candidate: dict) -> tuple[float, float]:
                score = (
                    slot_scores[(candidate["id"], slot["day_date"], slot["start_time"])]["score"]
                    if slot_scores is not None
                    else 0
                )
                category_penalty = used_categories.get(str(candidate.get("category")), 0) * 5
                similarity = float(candidate.get("similarity") or 0)
                return score - category_penalty, similarity

            chosen = max(unused.values(), key=rank)
            unused.pop(int(chosen["id"]))
            category = str(chosen.get("category"))
            used_categories[category] = used_categories.get(category, 0) + 1
            selection.append({"attraction_id": chosen["id"], **slot})
        return selection

    def generate(self, trip_id: int) -> dict:
        trace_id = str(uuid4())
        trip = self.trips.get_trip(trip_id)
        if trip is None:
            raise ItineraryValidationError("Trip was not found.")
        self._record(
            trace_id=trace_id,
            trip_id=trip_id,
            event_type="WORKFLOW_STARTED",
            status="started",
            input_summary={"workflow": "initial_itinerary", "pace": trip["pace"]},
        )
        try:
            query = " ".join(trip.get("preferences") or [])
            query = f"{query} real visitor attractions with varied indoor and outdoor activities".strip()
            candidates = self.search.search(
                destination_id=trip["destination_id"],
                query=query,
                limit=30,
                trace_id=trace_id,
                trip_id=trip_id,
            )
            stored = filter_attraction_candidates(
                self.attractions.list_attractions(trip["destination_id"], limit=100),
                limit=100,
            )
            by_id = {row["id"]: row for row in candidates}
            for row in stored:
                by_id.setdefault(row["id"], row)
            candidates = list(by_id.values())[:30]
            if not candidates:
                raise ItineraryValidationError("No real attractions are available for this trip.")

            start = _as_date(trip["start_date"])
            end = _as_date(trip["end_date"])
            slots = build_schedule_slots(
                start_date=start,
                end_date=end,
                pace=trip["pace"],
                candidate_count=len(candidates),
            )
            today = self.today_provider()
            has_live_conditions = live_conditions_available(start, end, today=today)
            snapshot_id = None
            slot_scores = None
            if has_live_conditions:
                forecast_days = (end - today).days + 1
                forecast = self.weather.get_forecast(
                    trip["latitude"], trip["longitude"], forecast_days=forecast_days
                )
                try:
                    air_quality = self.weather.get_air_quality(
                        trip["latitude"], trip["longitude"], forecast_days=forecast_days
                    )
                except OpenMeteoError as exc:
                    logger.warning("air_quality_unavailable trip_id=%d code=%s", trip_id, exc.code)
                    air_quality = None
                snapshot_id = self.trips.save_weather_snapshot(
                    trip_id=trip_id, forecast=forecast, air_quality=air_quality
                )
                slot_conditions = {
                    (slot["day_date"], slot["start_time"]): conditions_for_slot(
                        forecast,
                        air_quality,
                        day_date=slot["day_date"],
                        start_time=slot["start_time"],
                    )
                    for slot in slots
                }
                slot_scores = {
                    (candidate["id"], slot["day_date"], slot["start_time"]): evaluate_activity_conditions(
                        candidate, slot_conditions[(slot["day_date"], slot["start_time"])]
                    )
                    for candidate in candidates
                    for slot in slots
                }
            selection, used_fallback = self._model_selection(
                trip=trip,
                candidates=candidates,
                slots=slots,
                slot_scores=slot_scores,
                trace_id=trace_id,
            )
            candidates_by_id = {candidate["id"]: candidate for candidate in candidates}
            items: list[dict] = []
            for sort_order, selected in enumerate(selection):
                candidate = candidates_by_id[selected["attraction_id"]]
                score = (
                    slot_scores[(candidate["id"], selected["day_date"], selected["start_time"])]
                    if slot_scores is not None
                    else None
                )
                start_at = datetime.combine(
                    date.fromisoformat(selected["day_date"]),
                    time.fromisoformat(selected["start_time"]),
                )
                duration = int(candidate.get("estimated_duration_minutes") or 90)
                end_at = start_at + timedelta(minutes=duration)
                items.append(
                    {
                        "attraction_id": candidate["id"],
                        "day_date": selected["day_date"],
                        "start_time": selected["start_time"],
                        "end_time": end_at.strftime("%H:%M"),
                        "title": candidate["name"],
                        "category": candidate.get("category"),
                        "indoor_outdoor": candidate.get("indoor_outdoor") or "mixed",
                        "weather_sensitivity": float(candidate.get("weather_sensitivity") or 0.5),
                        "suitability_score": score["score"] if score else None,
                        "risk_state": score["state"] if score else None,
                        "risk_reasons": score["reasons"] if score else [],
                        "notes": candidate.get("traveler_summary"),
                        "sort_order": sort_order,
                        "condition_signals": score["signals"] if score else None,
                    }
                )
            item_ids = self.trips.replace_itinerary(trip_id=trip_id, items=items)
            for item_id, item in zip(item_ids, items, strict=True):
                item["id"] = item_id
            self._record(
                trace_id=trace_id,
                trip_id=trip_id,
                event_type="ITINERARY_GENERATED",
                status="ok",
                output_summary={
                    "item_count": len(items),
                    "deterministic_fallback": used_fallback,
                    "snapshot_id": snapshot_id,
                    "live_conditions_available": has_live_conditions,
                },
            )
            self._record(
                trace_id=trace_id,
                trip_id=trip_id,
                event_type="WORKFLOW_COMPLETED",
                status="ok",
                output_summary={"item_count": len(items)},
            )
            return {
                "trace_id": trace_id,
                "trip": trip,
                "items": items,
                "weather_snapshot_id": snapshot_id,
                "live_conditions_available": has_live_conditions,
                "used_deterministic_fallback": used_fallback,
            }
        except Exception as exc:
            self._record(
                trace_id=trace_id,
                trip_id=trip_id,
                event_type="WORKFLOW_ERROR",
                status="error",
                output_summary={"error_type": type(exc).__name__},
            )
            raise
