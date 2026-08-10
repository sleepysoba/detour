import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from detour.agent import MAX_SAVE_ATTEMPTS, RepairAgent
from detour.repairs import (
    RepairService,
    RepairValidationError,
    factual_repair_rationale,
    item_state,
)


TRIP = {
    "id": 1,
    "destination_id": 7,
    "destination_name": "Boulder",
    "start_date": "2026-08-10",
    "end_date": "2026-08-11",
    "preferences": ["culture"],
}
ITEMS = [
    {
        "id": 101, "attraction_id": 201, "day_date": "2026-08-10",
        "start_time": "13:00", "end_time": "15:00", "title": "Worst Trail",
        "category": "trail", "indoor_outdoor": "outdoor", "weather_sensitivity": 0.95,
        "activity_level": "high", "notes": "Trail", "sort_order": 0,
    },
    {
        "id": 102, "attraction_id": 202, "day_date": "2026-08-11",
        "start_time": "09:00", "end_time": "11:00", "title": "Exposed Garden",
        "category": "garden", "indoor_outdoor": "outdoor", "weather_sensitivity": 0.7,
        "activity_level": "moderate", "notes": "Garden", "sort_order": 1,
    },
]
CANDIDATES = [
    {
        "id": 301, "destination_id": 7, "name": "History Museum", "description": "Museum",
        "source_url": "https://example.invalid/301", "category": "museum",
        "indoor_outdoor": "indoor", "weather_sensitivity": 0.1, "activity_level": "low",
        "estimated_duration_minutes": 90, "tags": ["culture"], "traveler_summary": "Museum",
    },
    {
        "id": 302, "destination_id": 7, "name": "Covered Market", "description": "Market",
        "source_url": "https://example.invalid/302", "category": "market",
        "indoor_outdoor": "mixed", "weather_sensitivity": 0.3, "activity_level": "low",
        "estimated_duration_minutes": 90, "tags": ["culture"], "traveler_summary": "Market",
    },
]
SCORES = {201: 42, 202: 64, 301: 90, 302: 82}
SNAPSHOT = {"forecast_json": {"hourly": [], "daily": []}, "air_quality_json": None}


def _fake_evaluate(items, **_kwargs):
    evaluations = []
    for item in items:
        score = SCORES[int(item["attraction_id"])]
        status = "GO" if score >= 80 else "CAUTION" if score >= 55 else "AT_RISK"
        evaluations.append(
            {
                "itinerary_item_id": int(item["id"]),
                "attraction_id": int(item["attraction_id"]),
                "title": item["title"],
                "condition_score": score,
                "status": status,
                "weather_sensitivity": float(item.get("weather_sensitivity") or 0.5),
                "vulnerable": status == "AT_RISK" or score < 65,
                "primary_risk_factors": ["Simulated rain"],
                "indoor_outdoor": item.get("indoor_outdoor"),
            }
        )
    return {
        "score": round(sum(row["condition_score"] for row in evaluations) / len(evaluations)),
        "vulnerable_activity_count": sum(row["vulnerable"] for row in evaluations),
        "item_evaluations": evaluations,
    }


def _repair_service():
    trips = Mock()
    trips.connection_options = {}
    trips.get_trip.return_value = deepcopy(TRIP)
    trips.get_itinerary.return_value = deepcopy(ITEMS)
    trips.get_latest_weather_snapshot.return_value = deepcopy(SNAPSHOT)
    attractions = Mock()
    attractions.list_attractions.return_value = deepcopy(CANDIDATES)
    service = RepairService(
        trips=trips, attractions=attractions, resilience=Mock(), traces=Mock(), connection_options={}
    )
    return service


def test_guarded_fallback_prioritizes_worst_risk_and_avoids_duplicate_candidates():
    service = _repair_service()
    with patch("detour.repairs.evaluate_items", side_effect=_fake_evaluate), patch(
        "detour.repairs.filter_attraction_candidates", side_effect=lambda rows, limit: rows[:limit]
    ):
        actions = service.build_guarded_fallback_actions(trip_id=1, scenario="RAINSTORM")

    assert [action["itinerary_item_id"] for action in actions] == [101, 102]
    assert actions[0]["new_attraction_id"] == 301
    assert len({action["new_attraction_id"] for action in actions}) == len(actions)


def test_quality_guard_rejects_skipping_worst_risk_and_negligible_change():
    service = _repair_service()
    with patch("detour.repairs.evaluate_items", side_effect=_fake_evaluate), patch(
        "detour.repairs.filter_attraction_candidates", side_effect=lambda rows, limit: rows[:limit]
    ):
        analysis = service.analyze_repair_options(trip_id=1, scenario="RAINSTORM")

    weak_projected = _fake_evaluate([ITEMS[0], {**ITEMS[1], "attraction_id": 302, "title": "Covered Market"}])
    weak_action = [{
        "action_type": "REPLACE", "itinerary_item_id": 102,
        "before_state": item_state(ITEMS[1]),
        "after_state": {**item_state(ITEMS[1]), "attraction_id": 302, "title": "Covered Market"},
    }]
    with pytest.raises(RepairValidationError, match="worst risk not addressed"):
        service._validate_proposal_quality(
            analysis=analysis, normalized_actions=weak_action, projected=weak_projected
        )

    negligible_projected = deepcopy(analysis["current_result"])
    negligible_projected["item_evaluations"][0] = {
        **negligible_projected["item_evaluations"][0], "condition_score": 43
    }
    negligible_action = [{
        "action_type": "REPLACE", "itinerary_item_id": 101,
        "before_state": item_state(ITEMS[0]),
        "after_state": {**item_state(ITEMS[0]), "attraction_id": 302, "title": "Covered Market"},
    }]
    with pytest.raises(RepairValidationError, match="weak change"):
        service._validate_proposal_quality(
            analysis=analysis, normalized_actions=negligible_action, projected=negligible_projected
        )


def test_factual_rationale_uses_stored_setting_identity_time_and_scores():
    before_state = item_state(ITEMS[0])
    after_state = {
        **before_state,
        "attraction_id": 302,
        "title": "Covered Market",
        "indoor_outdoor": "mixed",
    }
    rationale = factual_repair_rationale(
        [{
            "action_type": "REPLACE", "itinerary_item_id": 101,
            "before_state": before_state, "after_state": after_state,
        }],
        before_result=_fake_evaluate([ITEMS[0]]),
        projected_result=_fake_evaluate([{**ITEMS[0], "attraction_id": 302, "title": "Covered Market"}]),
        scenario="RAINSTORM",
    )

    assert "stored mixed replacement" in rationale
    assert "scores 82 versus 42" in rationale
    assert "2026-08-10 13:00" in rationale
    assert "stored indoor" not in rationale


def _tool_message(name, arguments, call_id):
    call = SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )
    return SimpleNamespace(content=None, tool_calls=[call])


class FakeLLM:
    model = "test-model"

    def __init__(self, messages):
        self.messages = list(messages)
        self.calls = 0

    def create_message(self, *_args, **_kwargs):
        message = self.messages[self.calls]
        self.calls += 1
        return message, 1


def _agent_messages(save_count=1):
    base = [
        _tool_message("get_trip_state", {"trip_id": 1}, "1"),
        _tool_message("get_trip_resilience", {"trip_id": 1, "scenario": "RAINSTORM"}, "2"),
        _tool_message("search_attractions", {"trip_id": 1, "query": "rain", "limit": 3}, "3"),
        _tool_message("evaluate_candidate", {"trip_id": 1, "attraction_id": 301, "proposed_datetime": "2026-08-10T13:00", "scenario": "RAINSTORM"}, "4"),
    ]
    for index in range(save_count):
        base.append(_tool_message("save_repair_proposal", {
            "trip_id": 1, "scenario": "RAINSTORM", "rationale": "repair",
            "actions": [{"action_type": "REPLACE", "itinerary_item_id": 101, "new_attraction_id": 301, "reason": "rain"}],
        }, str(5 + index)))
    return base


def _toolbox_with_save_behavior(*, fail_saves):
    toolbox = Mock()
    attempts = {"count": 0}

    def dispatch(name, _arguments, **_kwargs):
        if name == "save_repair_proposal":
            attempts["count"] += 1
            if attempts["count"] <= fail_saves:
                raise RepairValidationError("worst risk not addressed")
            return {"repair_id": 8, "actions": [{"action_type": "REPLACE"}]}
        return {"ok": True}

    toolbox.dispatch.side_effect = dispatch
    toolbox.save_guarded_fallback.return_value = {
        "repair_id": 9, "actions": [{"action_type": "REPLACE"}]
    }
    return toolbox


def test_two_invalid_saves_use_fallback_after_only_one_model_correction():
    llm = FakeLLM(_agent_messages(save_count=2))
    toolbox = _toolbox_with_save_behavior(fail_saves=2)
    result = RepairAgent(llm=llm, toolbox=toolbox, traces=Mock()).run(1, "RAINSTORM")

    assert MAX_SAVE_ATTEMPTS == 2
    assert result["save_attempt_count"] == 2
    assert result["model_call_count"] == 6
    assert result["used_deterministic_fallback"] is True
    assert result["validation_failures"] == ["worst risk not addressed"] * 2
    toolbox.save_guarded_fallback.assert_called_once()


def test_successful_strong_agent_proposal_does_not_trigger_fallback():
    llm = FakeLLM(_agent_messages(save_count=1))
    toolbox = _toolbox_with_save_behavior(fail_saves=0)
    result = RepairAgent(llm=llm, toolbox=toolbox, traces=Mock()).run(1, "RAINSTORM")

    assert result["model_call_count"] == 5
    assert result["used_deterministic_fallback"] is False
    assert result["validation_failures"] == []
    toolbox.save_guarded_fallback.assert_not_called()
