from unittest.mock import Mock

import pytest

from detour.repairs import RepairValidationError
from detour.tools import REPAIR_TOOL_DEFINITIONS, RepairToolbox


def test_repair_tool_contract_is_narrow_and_has_no_apply_or_sql_tool():
    names = {tool["function"]["name"] for tool in REPAIR_TOOL_DEFINITIONS}
    assert names == {"get_trip_state", "get_trip_resilience", "search_attractions", "evaluate_candidate", "save_repair_proposal"}
    assert not any("sql" in name or "apply" in name for name in names)
    save_tool = next(
        tool for tool in REPAIR_TOOL_DEFINITIONS
        if tool["function"]["name"] == "save_repair_proposal"
    )
    variants = save_tool["function"]["parameters"]["properties"]["actions"]["items"]["oneOf"]
    required_by_type = {
        variant["properties"]["action_type"]["enum"][0]: set(variant["required"])
        for variant in variants
    }
    assert "new_attraction_id" in required_by_type["REPLACE"]
    assert {"new_day_date", "new_start_time"} <= required_by_type["MOVE"]


def test_candidate_evaluation_enforces_destination_scope():
    trips = Mock()
    trips.get_trip.return_value = {"id": 1, "destination_id": 7, "start_date": "2026-08-10", "end_date": "2026-08-11"}
    attractions = Mock()
    attractions.get_attraction.return_value = None
    toolbox = RepairToolbox(trips=trips, attractions=attractions, search=Mock(), resilience=Mock(), repairs=Mock())
    with pytest.raises(RepairValidationError, match="does not belong"):
        toolbox.evaluate_candidate(1, 999, "2026-08-10T13:00", "RAINSTORM")
    attractions.get_attraction.assert_called_once_with(999, destination_id=7)
