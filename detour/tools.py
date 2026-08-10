"""Narrow, JSON-serializable production tools for the repair agent."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from detour.destination import AttractionSearchService
from detour.itinerary import conditions_for_slot
from detour.models import TripRepository
from detour.repairs import RepairService, RepairValidationError
from detour.resilience import TripResilienceService
from detour.retrieval import AttractionRepository
from detour.scenarios import apply_scenario, normalize_scenario
from detour.scoring import evaluate_activity_conditions

SCENARIO_ENUM = ["LIVE", "RAINSTORM", "HEATWAVE", "POOR_AQI"]


REPAIR_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_trip_state",
            "description": "Load trip preferences and its current persisted itinerary.",
            "parameters": {
                "type": "object",
                "properties": {"trip_id": {"type": "integer"}},
                "required": ["trip_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trip_resilience",
            "description": "Deterministically evaluate current itinerary risk under live or simulated conditions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {"type": "integer"},
                    "scenario": {"type": "string", "enum": SCENARIO_ENUM},
                },
                "required": ["trip_id", "scenario"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_attractions",
            "description": "Semantically retrieve real alternatives scoped to the trip destination.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {"type": "integer"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 8},
                },
                "required": ["trip_id", "query", "limit"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_candidate",
            "description": "Score one real destination attraction at a proposed trip date/time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {"type": "integer"},
                    "attraction_id": {"type": "integer"},
                    "proposed_datetime": {
                        "type": "string",
                        "description": "Trip-local YYYY-MM-DDTHH:MM value.",
                    },
                    "scenario": {"type": "string", "enum": SCENARIO_ENUM},
                },
                "required": ["trip_id", "attraction_id", "proposed_datetime", "scenario"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_repair_proposal",
            "description": (
                "Validate and save a pending proposal only. This never applies or mutates the itinerary. "
                "Use the exact REPLACE or MOVE shape. REPLACE requires new_attraction_id and must target "
                "the worst repairable AT_RISK item first. MOVE requires both new_day_date and "
                "new_start_time. Use two MOVE actions for a swap. Stored facts and scores generate the "
                "final rationale; rationale here is only a short planning summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_id": {"type": "integer"},
                    "scenario": {"type": "string", "enum": SCENARIO_ENUM},
                    "rationale": {"type": "string"},
                    "actions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 3,
                        "items": {
                            "oneOf": [
                                {
                                    "type": "object",
                                    "properties": {
                                        "action_type": {"type": "string", "enum": ["REPLACE"]},
                                        "itinerary_item_id": {"type": "integer"},
                                        "new_attraction_id": {"type": "integer"},
                                        "reason": {"type": "string"},
                                    },
                                    "required": [
                                        "action_type", "itinerary_item_id", "new_attraction_id", "reason"
                                    ],
                                    "additionalProperties": False,
                                },
                                {
                                    "type": "object",
                                    "properties": {
                                        "action_type": {"type": "string", "enum": ["MOVE"]},
                                        "itinerary_item_id": {"type": "integer"},
                                        "new_day_date": {"type": "string"},
                                        "new_start_time": {"type": "string"},
                                        "reason": {"type": "string"},
                                    },
                                    "required": [
                                        "action_type", "itinerary_item_id", "new_day_date",
                                        "new_start_time", "reason"
                                    ],
                                    "additionalProperties": False,
                                },
                            ]
                        },
                    },
                },
                "required": ["trip_id", "scenario", "rationale", "actions"],
                "additionalProperties": False,
            },
        },
    },
]


def json_safe(value: Any) -> Any:
    """Convert database date/time objects into bounded tool-result primitives."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat(timespec="minutes")
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


class RepairToolbox:
    """Validated dispatch layer; tools have no arbitrary SQL or apply capability."""

    def __init__(
        self,
        *,
        trips: TripRepository,
        attractions: AttractionRepository,
        search: AttractionSearchService,
        resilience: TripResilienceService,
        repairs: RepairService,
    ):
        self.trips = trips
        self.attractions = attractions
        self.search = search
        self.resilience = resilience
        self.repairs = repairs

    def dispatch(self, name: str, arguments: dict[str, Any], *, trace_id: str) -> dict[str, Any]:
        if name == "get_trip_state":
            result = self.get_trip_state(int(arguments["trip_id"]))
        elif name == "get_trip_resilience":
            result = self.get_trip_resilience(
                int(arguments["trip_id"]), arguments.get("scenario")
            )
        elif name == "search_attractions":
            result = self.search_attractions(
                int(arguments["trip_id"]),
                str(arguments["query"]),
                int(arguments.get("limit", 8)),
                trace_id=trace_id,
            )
        elif name == "evaluate_candidate":
            result = self.evaluate_candidate(
                int(arguments["trip_id"]),
                int(arguments["attraction_id"]),
                str(arguments["proposed_datetime"]),
                arguments.get("scenario"),
            )
        elif name == "save_repair_proposal":
            result = self.repairs.save_proposal(
                trip_id=int(arguments["trip_id"]),
                scenario=arguments.get("scenario"),
                actions=arguments.get("actions"),
                rationale=arguments.get("rationale"),
                trace_id=trace_id,
            )
        else:
            raise RepairValidationError(f"Unknown repair tool: {name}.")
        return json_safe(result)

    def save_guarded_fallback(
        self, *, trip_id: int, scenario: str | None, trace_id: str
    ) -> dict[str, Any]:
        """Invoke the non-model safety optimizer after bounded agent correction fails."""
        return json_safe(
            self.repairs.save_guarded_fallback(
                trip_id=trip_id, scenario=scenario, trace_id=trace_id
            )
        )

    def get_trip_state(self, trip_id: int) -> dict[str, Any]:
        trip = self.trips.get_trip(trip_id)
        if trip is None:
            raise RepairValidationError("Trip was not found.")
        itinerary = self.trips.get_itinerary(trip_id)
        return json_safe(
            {
                "trip": {
                    "id": trip["id"],
                    "destination_id": trip["destination_id"],
                    "destination": trip["destination_name"],
                    "start_date": trip["start_date"],
                    "end_date": trip["end_date"],
                    "preferences": trip.get("preferences") or [],
                    "pace": trip["pace"],
                },
                "itinerary": itinerary,
            }
        )

    def get_trip_resilience(self, trip_id: int, scenario: str | None) -> dict[str, Any]:
        return json_safe(self.resilience.evaluate_trip_resilience(trip_id, scenario))

    def search_attractions(
        self,
        trip_id: int,
        query: str,
        limit: int,
        *,
        trace_id: str,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 8:
            raise RepairValidationError("Attraction search limit must be from 1 to 8.")
        trip = self.trips.get_trip(trip_id)
        if trip is None:
            raise RepairValidationError("Trip was not found.")
        current_ids = {int(item["attraction_id"]) for item in self.trips.get_itinerary(trip_id)}
        results = self.search.search(
            destination_id=int(trip["destination_id"]),
            query=query,
            limit=limit,
            trace_id=trace_id,
            trip_id=trip_id,
        )
        compact = [
            {
                "id": item["id"],
                "name": item["name"],
                "category": item.get("category"),
                "indoor_outdoor": item.get("indoor_outdoor"),
                "weather_sensitivity": item.get("weather_sensitivity"),
                "activity_level": item.get("activity_level"),
                "tags": item.get("tags") or [],
                "summary": item.get("traveler_summary") or item.get("description", "")[:240],
                "similarity": item.get("similarity"),
                "already_scheduled": int(item["id"]) in current_ids,
            }
            for item in results
        ]
        return {"count": len(compact), "attractions": compact}

    def evaluate_candidate(
        self,
        trip_id: int,
        attraction_id: int,
        proposed_datetime: str,
        scenario: str | None,
    ) -> dict[str, Any]:
        trip = self.trips.get_trip(trip_id)
        if trip is None:
            raise RepairValidationError("Trip was not found.")
        try:
            proposed = datetime.fromisoformat(proposed_datetime)
        except ValueError as exc:
            raise RepairValidationError("proposed_datetime must use YYYY-MM-DDTHH:MM format.") from exc
        trip_start = date.fromisoformat(str(trip["start_date"]))
        trip_end = date.fromisoformat(str(trip["end_date"]))
        if not trip_start <= proposed.date() <= trip_end:
            raise RepairValidationError("Candidate evaluation must use a datetime inside the trip.")
        attraction = self.attractions.get_attraction(
            attraction_id, destination_id=int(trip["destination_id"])
        )
        if attraction is None:
            raise RepairValidationError("Candidate attraction does not belong to this destination.")
        snapshot = self.trips.get_latest_weather_snapshot(trip_id)
        if snapshot is None:
            raise RepairValidationError("No live weather snapshot is available for this trip.")
        live_conditions = conditions_for_slot(
            snapshot["forecast_json"],
            snapshot.get("air_quality_json"),
            day_date=proposed.date().isoformat(),
            start_time=proposed.time().isoformat(timespec="minutes"),
        )
        conditions = apply_scenario(live_conditions, normalize_scenario(scenario))
        evaluation = evaluate_activity_conditions(attraction, conditions)
        return {
            "attraction_id": attraction_id,
            "name": attraction["name"],
            "proposed_datetime": proposed.isoformat(timespec="minutes"),
            "scenario": normalize_scenario(scenario) or "LIVE",
            "simulated": normalize_scenario(scenario) is not None,
            "score": evaluation["score"],
            "status": evaluation["state"],
            "risk_factors": evaluation["reasons"],
            "weather_sensitivity": attraction.get("weather_sensitivity"),
        }
