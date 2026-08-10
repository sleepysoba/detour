"""Validated repair proposals and explicit transactional application."""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from typing import Any

from psycopg2.extras import Json

from detour.db import LakebaseError, get_connection, run_query
from detour.models import TripRepository
from detour.resilience import TripResilienceService, evaluate_items
from detour.retrieval import AttractionRepository
from detour.scenarios import normalize_scenario
from detour.tracing import TraceService
from detour.wikimedia import filter_attraction_candidates

logger = logging.getLogger(__name__)

MAX_REPAIR_ACTIONS = 3
MIN_RESILIENCE_IMPROVEMENT = 3
MIN_CONDITION_SCORE_IMPROVEMENT = 10
MATERIAL_CANDIDATE_ADVANTAGE = 8
ACTION_TYPES = {"MOVE", "REPLACE"}
PERSISTED_ITEM_FIELDS = (
    "attraction_id",
    "day_date",
    "start_time",
    "end_time",
    "title",
    "category",
    "indoor_outdoor",
    "weather_sensitivity",
    "notes",
    "sort_order",
    "activity_level",
)


class RepairValidationError(ValueError):
    """Raised when a proposal is unsafe or stale."""


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise RepairValidationError("Repair dates must use YYYY-MM-DD format.") from exc


def _time_text(value: Any) -> str:
    if isinstance(value, time):
        return value.isoformat(timespec="minutes")
    try:
        return time.fromisoformat(str(value)).isoformat(timespec="minutes")
    except ValueError as exc:
        raise RepairValidationError("Repair times must use HH:MM format.") from exc


def item_state(item: dict[str, Any]) -> dict[str, Any]:
    """Return the safe persisted fields needed to preview and stale-check an action."""
    state = {field: item.get(field) for field in PERSISTED_ITEM_FIELDS}
    state["id"] = int(item["id"])
    state["attraction_id"] = int(item["attraction_id"])
    state["day_date"] = _date_text(item["day_date"])
    state["start_time"] = _time_text(item["start_time"])
    state["end_time"] = _time_text(item["end_time"]) if item.get("end_time") else None
    state["weather_sensitivity"] = float(item.get("weather_sensitivity") or 0.5)
    state["sort_order"] = int(item.get("sort_order") or 0)
    return state


def _after_from_attraction(before: dict[str, Any], attraction: dict[str, Any]) -> dict[str, Any]:
    after = deepcopy(before)
    after.update(
        {
            "attraction_id": int(attraction["id"]),
            "title": attraction["name"],
            "category": attraction.get("category"),
            "indoor_outdoor": attraction.get("indoor_outdoor") or "mixed",
            "weather_sensitivity": float(attraction.get("weather_sensitivity") or 0.5),
            "notes": attraction.get("traveler_summary"),
        }
    )
    duration = int(attraction.get("estimated_duration_minutes") or 90)
    start_at = datetime.combine(
        date.fromisoformat(after["day_date"]), time.fromisoformat(after["start_time"])
    )
    after["end_time"] = (start_at + timedelta(minutes=duration)).time().isoformat(timespec="minutes")
    return after


def validate_and_project_actions(
    *,
    trip: dict[str, Any],
    itinerary: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    attractions: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate an entire plan and return normalized actions plus proposed state."""
    if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_REPAIR_ACTIONS:
        raise RepairValidationError("A repair proposal must contain 1 to 3 actions.")
    current = {int(item["id"]): item_state(item) for item in itinerary}
    proposed = deepcopy(current)
    seen_item_ids: set[int] = set()
    normalized_actions: list[dict[str, Any]] = []
    trip_start = date.fromisoformat(_date_text(trip["start_date"]))
    trip_end = date.fromisoformat(_date_text(trip["end_date"]))

    for order, raw in enumerate(actions):
        if not isinstance(raw, dict):
            raise RepairValidationError("Each repair action must be an object.")
        action_type = str(raw.get("action_type") or "").upper()
        if action_type not in ACTION_TYPES:
            raise RepairValidationError("Repair actions must be MOVE or REPLACE.")
        try:
            item_id = int(raw.get("itinerary_item_id"))
        except (TypeError, ValueError) as exc:
            raise RepairValidationError("Each repair action needs a valid itinerary item ID.") from exc
        if item_id not in current:
            raise RepairValidationError(f"Unknown itinerary item ID {item_id}.")
        if item_id in seen_item_ids:
            raise RepairValidationError("A proposal cannot change one itinerary item twice.")
        seen_item_ids.add(item_id)

        reason = str(raw.get("reason") or "").strip()
        if not reason or len(reason) > 500:
            raise RepairValidationError("Each repair action needs a concise reason.")
        before = current[item_id]
        after = deepcopy(before)

        if action_type == "REPLACE":
            try:
                attraction_id = int(raw.get("new_attraction_id"))
            except (TypeError, ValueError) as exc:
                raise RepairValidationError("REPLACE requires a valid new attraction ID.") from exc
            attraction = attractions.get(attraction_id)
            if attraction is None or int(attraction["destination_id"]) != int(trip["destination_id"]):
                raise RepairValidationError(
                    f"Replacement attraction ID {attraction_id} does not belong to this destination."
                )
            if attraction_id == before["attraction_id"]:
                raise RepairValidationError("A replacement must select a different attraction.")
            after = _after_from_attraction(after, attraction)
        else:
            if raw.get("new_attraction_id") not in {None, before["attraction_id"]}:
                raise RepairValidationError("MOVE cannot change the attraction ID.")

        if raw.get("new_day_date") is not None:
            after["day_date"] = _date_text(raw["new_day_date"])
        if raw.get("new_start_time") is not None:
            after["start_time"] = _time_text(raw["new_start_time"])
        moved_day = date.fromisoformat(after["day_date"])
        if not trip_start <= moved_day <= trip_end:
            raise RepairValidationError("Repair dates must remain inside the trip.")
        if action_type == "MOVE" and (
            after["day_date"], after["start_time"]
        ) == (before["day_date"], before["start_time"]):
            raise RepairValidationError("MOVE must change the activity date or time.")

        if (after["day_date"], after["start_time"]) != (
            before["day_date"], before["start_time"]
        ):
            duration_minutes = 90
            if before.get("end_time"):
                start_at = datetime.combine(date.min, time.fromisoformat(before["start_time"]))
                end_at = datetime.combine(date.min, time.fromisoformat(before["end_time"]))
                duration_minutes = max(30, int((end_at - start_at).total_seconds() / 60))
            new_start = datetime.combine(moved_day, time.fromisoformat(after["start_time"]))
            after["end_time"] = (new_start + timedelta(minutes=duration_minutes)).time().isoformat(
                timespec="minutes"
            )

        proposed[item_id] = after
        normalized_actions.append(
            {
                "action_type": action_type,
                "itinerary_item_id": item_id,
                "before_state": before,
                "after_state": after,
                "reason": reason,
                "sort_order": order,
            }
        )

    proposed_items = list(proposed.values())
    attraction_ids = [item["attraction_id"] for item in proposed_items]
    if len(attraction_ids) != len(set(attraction_ids)):
        raise RepairValidationError("The proposed itinerary would contain duplicate attractions.")
    slots = [(item["day_date"], item["start_time"]) for item in proposed_items]
    if len(slots) != len(set(slots)):
        raise RepairValidationError("The proposed itinerary would schedule two activities in one slot.")
    proposed_items.sort(key=lambda item: (item["day_date"], item["start_time"], item["id"]))
    return normalized_actions, proposed_items


def _preference_relevance(attraction: dict[str, Any], preferences: list[str]) -> int:
    """Small deterministic tie-breaker after condition score."""
    preference_terms = {str(value).casefold() for value in preferences}
    evidence = {
        str(attraction.get("category") or "").casefold(),
        str(attraction.get("indoor_outdoor") or "").casefold(),
        str(attraction.get("activity_level") or "").casefold(),
        *(str(tag).casefold().replace("-", " ") for tag in attraction.get("tags") or []),
    }
    return len(preference_terms & evidence)


def _risk_order(evaluation: dict[str, Any]) -> tuple[int, int]:
    return (0 if evaluation["status"] == "AT_RISK" else 1, evaluation["condition_score"])


def factual_repair_rationale(
    actions: list[dict[str, Any]],
    *,
    before_result: dict[str, Any],
    projected_result: dict[str, Any],
    scenario: str | None,
) -> str:
    """Build factual user text only from persisted states and deterministic scores."""
    before_by_id = {
        int(item["itinerary_item_id"]): item for item in before_result["item_evaluations"]
    }
    after_by_id = {
        int(item["itinerary_item_id"]): item for item in projected_result["item_evaluations"]
    }
    condition_label = (
        f"the simulated {normalize_scenario(scenario).replace('_', ' ').title()} conditions"
        if normalize_scenario(scenario)
        else "live conditions"
    )
    statements: list[str] = []
    for action in actions:
        item_id = int(action["itinerary_item_id"])
        before = action["before_state"]
        after = action["after_state"]
        before_score = before_by_id[item_id]["condition_score"]
        after_score = after_by_id[item_id]["condition_score"]
        if action["action_type"] == "REPLACE":
            setting = str(after.get("indoor_outdoor") or "mixed")
            statements.append(
                f"Replaced {before['title']} with {after['title']} at "
                f"{after['day_date']} {after['start_time']}; the stored {setting} replacement "
                f"scores {after_score} versus {before_score} under {condition_label}."
            )
        else:
            statements.append(
                f"Moved {before['title']} from {before['day_date']} {before['start_time']} to "
                f"{after['day_date']} {after['start_time']}; its score improves from "
                f"{before_score} to {after_score} under {condition_label}."
            )
    return " ".join(statements)


class RepairService:
    """Create immutable previews, then apply a pending proposal only on explicit request."""

    def __init__(
        self,
        *,
        trips: TripRepository,
        attractions: AttractionRepository,
        resilience: TripResilienceService,
        traces: TraceService | None = None,
        connection_options: dict[str, Any] | None = None,
    ):
        self.trips = trips
        self.attractions = attractions
        self.resilience = resilience
        self.traces = traces
        self.connection_options = connection_options or trips.connection_options

    def _record(self, *, trace_id: str, trip_id: int, repair_run_id: int | None = None, **event: Any) -> None:
        if self.traces:
            self.traces.record_safe(
                trace_id=trace_id,
                trip_id=trip_id,
                repair_run_id=repair_run_id,
                **event,
            )

    def _attraction_map(self, trip: dict[str, Any], actions: list[dict[str, Any]]) -> dict[int, dict]:
        result: dict[int, dict] = {}
        for action in actions:
            raw_id = action.get("new_attraction_id")
            if raw_id is None:
                continue
            try:
                attraction_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            attraction = self.attractions.get_attraction(
                attraction_id, destination_id=int(trip["destination_id"])
            )
            if attraction:
                result[attraction_id] = attraction
        return result

    def analyze_repair_options(
        self,
        *,
        trip_id: int,
        scenario: str | None,
        trip: dict[str, Any] | None = None,
        itinerary: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Rank real unused replacements for vulnerable items, worst risk first."""
        normalized_scenario = normalize_scenario(scenario)
        trip = trip or self.trips.get_trip(trip_id)
        if trip is None:
            raise RepairValidationError("Trip was not found.")
        itinerary = itinerary or self.trips.get_itinerary(trip_id)
        snapshot = self.trips.get_latest_weather_snapshot(trip_id)
        if snapshot is None:
            raise RepairValidationError("No live weather snapshot is available for this trip.")
        current = evaluate_items(
            itinerary,
            forecast=snapshot["forecast_json"],
            air_quality=snapshot.get("air_quality_json"),
            scenario=normalized_scenario,
        )
        current_by_id = {
            int(item["itinerary_item_id"]): item for item in current["item_evaluations"]
        }
        scheduled_ids = {int(item["attraction_id"]) for item in itinerary}
        candidates = filter_attraction_candidates(
            self.attractions.list_attractions(int(trip["destination_id"]), limit=100), limit=100
        )
        vulnerable = sorted(
            (item for item in current["item_evaluations"] if item["vulnerable"]),
            key=_risk_order,
        )
        options_by_item: dict[int, list[dict[str, Any]]] = {}
        itinerary_by_id = {int(item["id"]): item for item in itinerary}
        for evaluation in vulnerable:
            item_id = int(evaluation["itinerary_item_id"])
            before = item_state(itinerary_by_id[item_id])
            options: list[dict[str, Any]] = []
            for attraction in candidates:
                attraction_id = int(attraction["id"])
                if attraction_id in scheduled_ids or attraction_id == before["attraction_id"]:
                    continue
                after = _after_from_attraction(before, attraction)
                candidate_result = evaluate_items(
                    [after],
                    forecast=snapshot["forecast_json"],
                    air_quality=snapshot.get("air_quality_json"),
                    scenario=normalized_scenario,
                )["item_evaluations"][0]
                improvement = candidate_result["condition_score"] - evaluation["condition_score"]
                options.append(
                    {
                        "itinerary_item_id": item_id,
                        "current_score": evaluation["condition_score"],
                        "current_status": evaluation["status"],
                        "attraction": attraction,
                        "candidate_score": candidate_result["condition_score"],
                        "candidate_status": candidate_result["status"],
                        "improvement": improvement,
                        "preference_relevance": _preference_relevance(
                            attraction, list(trip.get("preferences") or [])
                        ),
                    }
                )
            options.sort(
                key=lambda option: (
                    option["candidate_score"],
                    option["preference_relevance"],
                    -int(option["attraction"]["id"]),
                ),
                reverse=True,
            )
            options_by_item[item_id] = options
        return {
            "trip": trip,
            "itinerary": itinerary,
            "snapshot": snapshot,
            "current_result": current,
            "current_by_id": current_by_id,
            "vulnerable": vulnerable,
            "options_by_item": options_by_item,
        }

    @staticmethod
    def _best_meaningful_option(analysis: dict[str, Any], item_id: int) -> dict[str, Any] | None:
        return next(
            (
                option
                for option in analysis["options_by_item"].get(item_id, [])
                if option["improvement"] >= MIN_CONDITION_SCORE_IMPROVEMENT
            ),
            None,
        )

    def _validate_proposal_quality(
        self,
        *,
        analysis: dict[str, Any],
        normalized_actions: list[dict[str, Any]],
        projected: dict[str, Any],
    ) -> None:
        """Reject proposals that skip a repairable worse risk or choose a clearly weaker fix."""
        affected_ids = {int(action["itinerary_item_id"]) for action in normalized_actions}
        worst_repairable: tuple[dict[str, Any], dict[str, Any]] | None = None
        for vulnerable in analysis["vulnerable"]:
            item_id = int(vulnerable["itinerary_item_id"])
            option = self._best_meaningful_option(analysis, item_id)
            if option:
                worst_repairable = vulnerable, option
                break
        if worst_repairable:
            worst, option = worst_repairable
            worst_id = int(worst["itinerary_item_id"])
            if worst_id not in affected_ids:
                raise RepairValidationError(
                    f"worst risk not addressed: {worst['title']} scores {worst['condition_score']}; "
                    f"candidate {option['attraction']['name']} scores {option['candidate_score']}"
                )

        projected_by_id = {
            int(item["itinerary_item_id"]): item for item in projected["item_evaluations"]
        }
        proposed_attractions_by_item = {
            int(action["itinerary_item_id"]): int(action["after_state"]["attraction_id"])
            for action in normalized_actions
        }
        for action in normalized_actions:
            item_id = int(action["itinerary_item_id"])
            before = analysis["current_by_id"][item_id]
            after = projected_by_id[item_id]
            actual_improvement = after["condition_score"] - before["condition_score"]
            used_by_other_actions = {
                attraction_id
                for other_item_id, attraction_id in proposed_attractions_by_item.items()
                if other_item_id != item_id
            }
            best = next(
                (
                    option
                    for option in analysis["options_by_item"].get(item_id, [])
                    if option["improvement"] >= MIN_CONDITION_SCORE_IMPROVEMENT
                    and int(option["attraction"]["id"]) not in used_by_other_actions
                ),
                None,
            )
            if best and actual_improvement < MIN_CONDITION_SCORE_IMPROVEMENT:
                raise RepairValidationError(
                    f"weak change for {before['title']}: improves {actual_improvement} points; "
                    f"candidate {best['attraction']['name']} improves {best['improvement']}"
                )
            if best and best["candidate_score"] - after["condition_score"] >= MATERIAL_CANDIDATE_ADVANTAGE:
                raise RepairValidationError(
                    f"stronger candidate available for {before['title']}: "
                    f"{best['attraction']['name']} scores {best['candidate_score']} versus "
                    f"proposed {after['condition_score']}"
                )

    def build_guarded_fallback_actions(
        self, *, trip_id: int, scenario: str | None
    ) -> list[dict[str, Any]]:
        """Choose up to three unused replacements in deterministic worst-risk order."""
        analysis = self.analyze_repair_options(trip_id=trip_id, scenario=scenario)
        chosen_ids: set[int] = set()
        actions: list[dict[str, Any]] = []
        for vulnerable in analysis["vulnerable"]:
            if len(actions) >= MAX_REPAIR_ACTIONS:
                break
            item_id = int(vulnerable["itinerary_item_id"])
            best = next(
                (
                    option
                    for option in analysis["options_by_item"].get(item_id, [])
                    if option["improvement"] >= MIN_CONDITION_SCORE_IMPROVEMENT
                    and int(option["attraction"]["id"]) not in chosen_ids
                ),
                None,
            )
            if best is None:
                continue
            attraction_id = int(best["attraction"]["id"])
            chosen_ids.add(attraction_id)
            actions.append(
                {
                    "action_type": "REPLACE",
                    "itinerary_item_id": item_id,
                    "new_attraction_id": attraction_id,
                    "reason": (
                        f"Deterministic guardrail: scores {best['candidate_score']} versus "
                        f"{best['current_score']} under {normalize_scenario(scenario) or 'LIVE'} conditions."
                    ),
                }
            )
        if not actions:
            raise RepairValidationError("No unused attraction provides a meaningful risk improvement.")
        return actions

    def save_guarded_fallback(
        self, *, trip_id: int, scenario: str | None, trace_id: str
    ) -> dict[str, Any]:
        actions = self.build_guarded_fallback_actions(trip_id=trip_id, scenario=scenario)
        return self.save_proposal(
            trip_id=trip_id,
            scenario=scenario,
            actions=actions,
            rationale="Deterministic risk guardrail selected the strongest available repairs.",
            trace_id=trace_id,
        )

    def save_proposal(
        self,
        *,
        trip_id: int,
        scenario: str | None,
        actions: list[dict[str, Any]],
        rationale: str,
        trace_id: str,
    ) -> dict[str, Any]:
        normalized_scenario = normalize_scenario(scenario)
        user_rationale = str(rationale or "").strip()
        if not user_rationale or len(user_rationale) > 1200:
            raise RepairValidationError("A concise user-facing repair rationale is required.")
        trip = self.trips.get_trip(trip_id)
        if trip is None:
            raise RepairValidationError("Trip was not found.")
        itinerary = self.trips.get_itinerary(trip_id)
        analysis = self.analyze_repair_options(
            trip_id=trip_id,
            scenario=normalized_scenario,
            trip=trip,
            itinerary=itinerary,
        )
        normalized, proposed = validate_and_project_actions(
            trip=trip,
            itinerary=itinerary,
            actions=actions,
            attractions=self._attraction_map(trip, actions),
        )
        before = analysis["current_result"]
        projected = evaluate_items(
            proposed,
            forecast=analysis["snapshot"]["forecast_json"],
            air_quality=analysis["snapshot"].get("air_quality_json"),
            scenario=normalized_scenario,
        )
        self._validate_proposal_quality(
            analysis=analysis,
            normalized_actions=normalized,
            projected=projected,
        )
        if (
            projected["score"] - before["score"] < MIN_RESILIENCE_IMPROVEMENT
            and projected["vulnerable_activity_count"] >= before["vulnerable_activity_count"]
        ):
            raise RepairValidationError("The proposed repair must improve resilience or reduce vulnerability.")
        grounded_rationale = factual_repair_rationale(
            normalized,
            before_result=before,
            projected_result=projected,
            scenario=normalized_scenario,
        )
        before_by_id = {
            int(item["itinerary_item_id"]): item for item in before["item_evaluations"]
        }
        projected_by_id = {
            int(item["itinerary_item_id"]): item for item in projected["item_evaluations"]
        }
        for action in normalized:
            item_id = int(action["itinerary_item_id"])
            action["reason"] = (
                f"Condition score {before_by_id[item_id]['condition_score']} -> "
                f"{projected_by_id[item_id]['condition_score']} under "
                f"{normalized_scenario or 'LIVE'} conditions."
            )

        try:
            with get_connection(**self.connection_options) as connection, connection.cursor() as cursor:
                try:
                    cursor.execute(
                        """
                        INSERT INTO repair_runs (
                            trip_id, trace_id, scenario_type, status,
                            before_resilience, projected_resilience, rationale
                        ) VALUES (%s, %s, %s, 'pending', %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            trip_id,
                            trace_id,
                            normalized_scenario or "live",
                            before["score"],
                            projected["score"],
                            grounded_rationale,
                        ),
                    )
                    repair_row = cursor.fetchone()
                    if not repair_row:
                        raise LakebaseError("Repair proposal insert returned no identifier.")
                    repair_id = int(repair_row["id"])
                    for action in normalized:
                        cursor.execute(
                            """
                            INSERT INTO repair_actions (
                                repair_run_id, action_type, itinerary_item_id,
                                before_state, after_state, reason, sort_order
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                repair_id,
                                action["action_type"],
                                action["itinerary_item_id"],
                                Json(action["before_state"]),
                                Json(action["after_state"]),
                                action["reason"],
                                action["sort_order"],
                            ),
                        )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except (LakebaseError, RepairValidationError):
            raise
        except Exception as exc:
            raise LakebaseError("Could not persist the repair proposal.") from exc

        self._record(
            trace_id=trace_id,
            trip_id=trip_id,
            repair_run_id=repair_id,
            event_type="REPAIR_PROPOSED",
            status="ok",
            output_summary={
                "repair_id": repair_id,
                "action_count": len(normalized),
                "resilience_before": before["score"],
                "resilience_projected": projected["score"],
            },
        )
        return self.get_preview(repair_id)

    def get_preview(self, repair_id: int) -> dict[str, Any]:
        rows = run_query(
            """
            SELECT r.id, r.trip_id, r.trace_id, r.scenario_type, r.status,
                   r.before_resilience, r.projected_resilience, r.rationale, r.created_at,
                   a.id AS action_id, a.action_type, a.itinerary_item_id,
                   a.before_state, a.after_state, a.reason, a.sort_order
            FROM repair_runs AS r
            LEFT JOIN repair_actions AS a ON a.repair_run_id = r.id
            WHERE r.id = %s
            ORDER BY a.sort_order, a.id
            """,
            (repair_id,),
            **self.connection_options,
        )
        if not rows:
            raise RepairValidationError("Repair proposal was not found.")
        first = rows[0]
        actions = [
            {
                "id": int(row["action_id"]),
                "action_type": row["action_type"],
                "itinerary_item_id": int(row["itinerary_item_id"]),
                "before": row["before_state"],
                "after": row["after_state"],
                "reason": row["reason"],
            }
            for row in rows
            if row.get("action_id") is not None
        ]
        current_items = [item_state(item) for item in self.trips.get_itinerary(int(first["trip_id"]))]
        current_by_id = {int(item["id"]): item for item in current_items}
        before_by_id = deepcopy(current_by_id)
        proposed_by_id = deepcopy(current_by_id)
        for action in actions:
            item_id = action["itinerary_item_id"]
            before_by_id[item_id] = action["before"]
            proposed_by_id[item_id] = action["after"]
        before_itinerary = sorted(
            before_by_id.values(), key=lambda item: (item["day_date"], item["start_time"], item["id"])
        )
        proposed_itinerary = sorted(
            proposed_by_id.values(), key=lambda item: (item["day_date"], item["start_time"], item["id"])
        )
        return {
            "repair_id": int(first["id"]),
            "trip_id": int(first["trip_id"]),
            "trace_id": first["trace_id"],
            "scenario": str(first["scenario_type"]).upper(),
            "status": str(first["status"]).upper(),
            "resilience_before": first["before_resilience"],
            "resilience_projected": first["projected_resilience"],
            "rationale": first["rationale"],
            "actions": actions,
            "before_itinerary": before_itinerary,
            "proposed_itinerary": proposed_itinerary,
            "created_at": first["created_at"],
        }

    def apply_repair(self, repair_id: int) -> dict[str, Any]:
        preview = self.get_preview(repair_id)
        trace_id = preview["trace_id"]
        trip_id = preview["trip_id"]
        self._record(
            trace_id=trace_id,
            trip_id=trip_id,
            repair_run_id=repair_id,
            event_type="REPAIR_APPLY_STARTED",
            status="started",
            input_summary={"repair_id": repair_id, "action_count": len(preview["actions"])},
        )
        applied_actions: list[dict[str, Any]] = []
        try:
            with get_connection(**self.connection_options) as connection, connection.cursor() as cursor:
                try:
                    cursor.execute("SELECT * FROM repair_runs WHERE id = %s FOR UPDATE", (repair_id,))
                    repair = cursor.fetchone()
                    if not repair:
                        raise RepairValidationError("Repair proposal was not found.")
                    if repair["status"] == "applied":
                        raise RepairValidationError("Repair has already been applied.")
                    if repair["status"] != "pending":
                        raise RepairValidationError("Only a pending repair can be applied.")
                    cursor.execute(
                        """
                        SELECT t.*, d.display_name AS destination_name, d.latitude, d.longitude, d.timezone
                        FROM trips AS t JOIN destinations AS d ON d.id = t.destination_id
                        WHERE t.id = %s
                        """,
                        (trip_id,),
                    )
                    trip = dict(cursor.fetchone() or {})
                    if not trip:
                        raise RepairValidationError("Trip was not found.")
                    cursor.execute(
                        """
                        SELECT id, trip_id, attraction_id, day_date, start_time, end_time,
                               title, category, indoor_outdoor, weather_sensitivity,
                               suitability_score, risk_state, risk_reasons, notes, sort_order
                        FROM itinerary_items WHERE trip_id = %s
                        ORDER BY day_date, sort_order, start_time, id FOR UPDATE
                        """,
                        (trip_id,),
                    )
                    itinerary = [dict(row) for row in cursor.fetchall()]
                    cursor.execute(
                        """
                        SELECT action_type, itinerary_item_id, before_state, after_state, reason, sort_order
                        FROM repair_actions WHERE repair_run_id = %s ORDER BY sort_order, id
                        """,
                        (repair_id,),
                    )
                    stored_actions = [dict(row) for row in cursor.fetchall()]

                    current_by_id = {int(item["id"]): item_state(item) for item in itinerary}
                    for action in stored_actions:
                        item_id = int(action["itinerary_item_id"])
                        before = action["before_state"]
                        current = current_by_id.get(item_id)
                        if current is None or any(
                            current[key] != before[key]
                            for key in ("attraction_id", "day_date", "start_time")
                        ):
                            raise RepairValidationError(
                                "The itinerary changed after this proposal was created; create a new repair."
                            )

                    proposed_by_id = deepcopy(current_by_id)
                    for action in stored_actions:
                        proposed_by_id[int(action["itinerary_item_id"])] = action["after_state"]
                    proposed = list(proposed_by_id.values())
                    trip_start = date.fromisoformat(_date_text(trip["start_date"]))
                    trip_end = date.fromisoformat(_date_text(trip["end_date"]))
                    if any(
                        not trip_start <= date.fromisoformat(_date_text(item["day_date"])) <= trip_end
                        for item in proposed
                    ):
                        raise RepairValidationError("A repair date no longer falls inside the trip.")
                    attraction_ids = [int(item["attraction_id"]) for item in proposed]
                    if len(attraction_ids) != len(set(attraction_ids)):
                        raise RepairValidationError("The repair would create duplicate attractions.")
                    slots = [(item["day_date"], item["start_time"]) for item in proposed]
                    if len(slots) != len(set(slots)):
                        raise RepairValidationError("The repair would create conflicting itinerary slots.")
                    cursor.execute(
                        """
                        SELECT id, destination_id FROM attractions
                        WHERE id = ANY(%s) AND destination_id = %s
                        """,
                        (attraction_ids, trip["destination_id"]),
                    )
                    valid_ids = {int(row["id"]) for row in cursor.fetchall()}
                    if valid_ids != set(attraction_ids):
                        raise RepairValidationError("A repair attraction no longer belongs to this destination.")
                    cursor.execute(
                        """
                        SELECT id, forecast_json, air_quality_json FROM weather_snapshots
                        WHERE trip_id = %s ORDER BY fetched_at DESC, id DESC LIMIT 1
                        """,
                        (trip_id,),
                    )
                    snapshot = cursor.fetchone()
                    if not snapshot:
                        raise RepairValidationError("No live weather snapshot is available for this trip.")
                    live_result = evaluate_items(
                        proposed,
                        forecast=snapshot["forecast_json"],
                        air_quality=snapshot.get("air_quality_json"),
                    )
                    scenario_name = None if repair["scenario_type"] == "live" else repair["scenario_type"]
                    scenario_result = evaluate_items(
                        proposed,
                        forecast=snapshot["forecast_json"],
                        air_quality=snapshot.get("air_quality_json"),
                        scenario=scenario_name,
                    )
                    scenario_before = evaluate_items(
                        list(current_by_id.values()),
                        forecast=snapshot["forecast_json"],
                        air_quality=snapshot.get("air_quality_json"),
                        scenario=scenario_name,
                    )
                    if (
                        scenario_result["score"] - scenario_before["score"]
                        < MIN_RESILIENCE_IMPROVEMENT
                        and scenario_result["vulnerable_activity_count"]
                        >= scenario_before["vulnerable_activity_count"]
                    ):
                        raise RepairValidationError(
                            "Current conditions no longer support this repair; create a new proposal."
                        )
                    live_by_id = {
                        int(item["itinerary_item_id"]): item for item in live_result["item_evaluations"]
                    }
                    for action in stored_actions:
                        after = action["after_state"]
                        item_id = int(action["itinerary_item_id"])
                        evaluation = live_by_id[item_id]
                        cursor.execute(
                            """
                            UPDATE itinerary_items SET
                                attraction_id = %s, day_date = %s, start_time = %s, end_time = %s,
                                title = %s, category = %s, indoor_outdoor = %s,
                                weather_sensitivity = %s, suitability_score = %s,
                                risk_state = %s, risk_reasons = %s, notes = %s,
                                sort_order = %s, updated_at = NOW()
                            WHERE id = %s AND trip_id = %s
                            """,
                            (
                                after["attraction_id"], after["day_date"], after["start_time"],
                                after.get("end_time"), after["title"], after.get("category"),
                                after.get("indoor_outdoor"), after["weather_sensitivity"],
                                evaluation["condition_score"], evaluation["status"],
                                Json(evaluation["primary_risk_factors"]), after.get("notes"),
                                after.get("sort_order", 0), item_id, trip_id,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise RepairValidationError("A repair action referenced a missing itinerary item.")
                        applied_actions.append(
                            {"action_type": action["action_type"], "itinerary_item_id": item_id}
                        )
                    cursor.execute(
                        "UPDATE trips SET live_resilience_score = %s, updated_at = NOW() WHERE id = %s",
                        (live_result["score"], trip_id),
                    )
                    cursor.execute(
                        """
                        UPDATE repair_runs SET status = 'applied', applied_at = NOW()
                        WHERE id = %s AND status = 'pending'
                        """,
                        (repair_id,),
                    )
                    if cursor.rowcount != 1:
                        raise RepairValidationError("Repair status changed before it could be applied.")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except Exception as exc:
            self._record(
                trace_id=trace_id,
                trip_id=trip_id,
                repair_run_id=repair_id,
                event_type="REPAIR_APPLY_ERROR",
                status="error",
                output_summary={"error_type": type(exc).__name__},
            )
            raise

        for action in applied_actions:
            self._record(
                trace_id=trace_id,
                trip_id=trip_id,
                repair_run_id=repair_id,
                event_type="REPAIR_ACTION_APPLIED",
                status="ok",
                output_summary=action,
            )
        self._record(
            trace_id=trace_id,
            trip_id=trip_id,
            repair_run_id=repair_id,
            event_type="REPAIR_APPLIED",
            status="ok",
            output_summary={
                "repair_id": repair_id,
                "action_count": len(applied_actions),
                "scenario_resilience": scenario_result["score"],
                "live_resilience": live_result["score"],
            },
        )
        return {
            "repair": self.get_preview(repair_id),
            "itinerary": self.trips.get_itinerary(trip_id),
            "resilience": scenario_result,
            "live_resilience": live_result,
        }
