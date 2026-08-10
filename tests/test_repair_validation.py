from copy import deepcopy

import pytest

from detour.repairs import RepairValidationError, validate_and_project_actions


TRIP = {"id": 1, "destination_id": 7, "start_date": "2026-08-10", "end_date": "2026-08-11"}
ITEMS = [
    {"id": 101, "attraction_id": 201, "day_date": "2026-08-10", "start_time": "13:00", "end_time": "15:00", "title": "Trail", "category": "trail", "indoor_outdoor": "outdoor", "weather_sensitivity": 0.95, "notes": "A trail.", "sort_order": 0},
    {"id": 102, "attraction_id": 202, "day_date": "2026-08-11", "start_time": "09:00", "end_time": "11:00", "title": "Gallery", "category": "museum", "indoor_outdoor": "indoor", "weather_sensitivity": 0.1, "notes": "A gallery.", "sort_order": 1},
]
ATTRACTIONS = {203: {"id": 203, "destination_id": 7, "name": "Museum", "category": "museum", "indoor_outdoor": "indoor", "weather_sensitivity": 0.1, "estimated_duration_minutes": 90, "traveler_summary": "Indoor museum."}}


def _replace(new_id=203):
    return {"action_type": "REPLACE", "itinerary_item_id": 101, "new_attraction_id": new_id, "reason": "Move the rainy slot indoors."}


def test_repair_validation_projects_real_replacement_without_mutating_itinerary():
    original = deepcopy(ITEMS)
    actions, proposed = validate_and_project_actions(
        trip=TRIP, itinerary=ITEMS, actions=[_replace()], attractions=ATTRACTIONS
    )
    assert ITEMS == original
    assert actions[0]["before_state"]["attraction_id"] == 201
    assert actions[0]["after_state"]["attraction_id"] == 203
    assert proposed[0]["title"] == "Museum"


def test_invalid_or_cross_destination_replacement_is_rejected():
    with pytest.raises(RepairValidationError, match="does not belong"):
        validate_and_project_actions(trip=TRIP, itinerary=ITEMS, actions=[_replace(999)], attractions=ATTRACTIONS)
    cross_destination = {203: {**ATTRACTIONS[203], "destination_id": 99}}
    with pytest.raises(RepairValidationError, match="does not belong"):
        validate_and_project_actions(trip=TRIP, itinerary=ITEMS, actions=[_replace()], attractions=cross_destination)


def test_duplicate_result_and_too_many_actions_are_rejected():
    duplicate = {202: {**ATTRACTIONS[203], "id": 202}}
    with pytest.raises(RepairValidationError, match="duplicate"):
        validate_and_project_actions(trip=TRIP, itinerary=ITEMS, actions=[_replace(202)], attractions=duplicate)
    with pytest.raises(RepairValidationError, match="1 to 3"):
        validate_and_project_actions(trip=TRIP, itinerary=ITEMS, actions=[_replace()] * 4, attractions=ATTRACTIONS)


def test_two_moves_can_express_a_valid_swap():
    actions = [
        {"action_type": "MOVE", "itinerary_item_id": 101, "new_day_date": "2026-08-11", "new_start_time": "09:00", "reason": "Move trail to safer morning."},
        {"action_type": "MOVE", "itinerary_item_id": 102, "new_day_date": "2026-08-10", "new_start_time": "13:00", "reason": "Place indoor gallery in exposed slot."},
    ]
    normalized, proposed = validate_and_project_actions(trip=TRIP, itinerary=ITEMS, actions=actions, attractions={})
    assert len(normalized) == 2
    assert {(row["day_date"], row["start_time"]) for row in proposed} == {("2026-08-10", "13:00"), ("2026-08-11", "09:00")}
