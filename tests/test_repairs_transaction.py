from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import Mock, patch

import pytest

from detour.repairs import RepairService, RepairValidationError, item_state


TRIP = {
    "id": 1,
    "destination_id": 7,
    "destination_name": "Boulder",
    "start_date": "2026-08-10",
    "end_date": "2026-08-11",
}
ITEMS = [
    {
        "id": 101,
        "trip_id": 1,
        "attraction_id": 201,
        "day_date": "2026-08-10",
        "start_time": "13:00",
        "end_time": "15:00",
        "title": "Trail",
        "category": "trail",
        "indoor_outdoor": "outdoor",
        "weather_sensitivity": 0.95,
        "suitability_score": 95,
        "risk_state": "GO",
        "risk_reasons": [],
        "notes": "A trail.",
        "sort_order": 0,
    },
    {
        "id": 102,
        "trip_id": 1,
        "attraction_id": 202,
        "day_date": "2026-08-11",
        "start_time": "09:00",
        "end_time": "11:00",
        "title": "Gallery",
        "category": "museum",
        "indoor_outdoor": "indoor",
        "weather_sensitivity": 0.1,
        "suitability_score": 98,
        "risk_state": "GO",
        "risk_reasons": [],
        "notes": "A gallery.",
        "sort_order": 1,
    },
]
BEFORE = item_state(ITEMS[0])
AFTER = {
    **BEFORE,
    "attraction_id": 203,
    "title": "Indoor Museum",
    "category": "museum",
    "indoor_outdoor": "indoor",
    "weather_sensitivity": 0.1,
    "notes": "A museum.",
}
STORED_ACTIONS = [
    {
        "action_type": "REPLACE",
        "itinerary_item_id": 101,
        "before_state": BEFORE,
        "after_state": AFTER,
        "reason": "Use an indoor alternative.",
        "sort_order": 0,
    }
]
SNAPSHOT = {
    "id": 5,
    "forecast_json": {
        "hourly": [
            {"time": "2026-08-10T13:00", "temperature_f": 72, "precipitation_probability_pct": 5, "wind_speed_mph": 3},
            {"time": "2026-08-11T09:00", "temperature_f": 68, "precipitation_probability_pct": 5, "wind_speed_mph": 3},
        ],
        "daily": [],
    },
    "air_quality_json": {"hourly": [
        {"time": "2026-08-10T13:00", "us_aqi": 25},
        {"time": "2026-08-11T09:00", "us_aqi": 25},
    ]},
}


class FakeCursor:
    def __init__(self, *, repair_status="pending", fail_item_update=False):
        self.repair_status = repair_status
        self.fail_item_update = fail_item_update
        self.rows = []
        self.rowcount = 0
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.statements.append((normalized, params))
        self.rowcount = 0
        if normalized.startswith("SELECT * FROM repair_runs"):
            self.rows = [{"id": 9, "trip_id": 1, "status": self.repair_status, "scenario_type": "rainstorm"}]
        elif "FROM trips AS t JOIN destinations" in normalized:
            self.rows = [deepcopy(TRIP)]
        elif "FROM itinerary_items WHERE trip_id" in normalized:
            self.rows = deepcopy(ITEMS)
        elif "FROM repair_actions WHERE repair_run_id" in normalized:
            self.rows = deepcopy(STORED_ACTIONS)
        elif "SELECT id, destination_id FROM attractions" in normalized:
            self.rows = [{"id": 202, "destination_id": 7}, {"id": 203, "destination_id": 7}]
        elif "FROM weather_snapshots" in normalized:
            self.rows = [deepcopy(SNAPSHOT)]
        elif normalized.startswith("UPDATE itinerary_items"):
            self.rows = []
            self.rowcount = 0 if self.fail_item_update else 1
        elif normalized.startswith("UPDATE trips") or normalized.startswith("UPDATE repair_runs"):
            self.rows = []
            self.rowcount = 1

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, **cursor_options):
        self.fake_cursor = FakeCursor(**cursor_options)
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _service(preview_status="PENDING"):
    trips = Mock()
    trips.connection_options = {}
    trips.get_itinerary.return_value = deepcopy(ITEMS)
    service = RepairService(
        trips=trips,
        attractions=Mock(),
        resilience=Mock(),
        traces=Mock(),
        connection_options={},
    )
    preview = {
        "repair_id": 9,
        "trip_id": 1,
        "trace_id": "trace-9",
        "scenario": "RAINSTORM",
        "status": preview_status,
        "actions": [{"action_type": "REPLACE"}],
    }
    applied = {**preview, "status": "APPLIED"}
    service.get_preview = Mock(side_effect=[preview, applied])
    return service


def _connection_patch(connection):
    @contextmanager
    def fake_get_connection(**_options):
        yield connection

    return patch("detour.repairs.get_connection", fake_get_connection)


def test_apply_repair_commits_all_mutations_and_marks_applied():
    connection = FakeConnection()
    service = _service()
    with _connection_patch(connection):
        result = service.apply_repair(9)

    assert connection.committed is True
    assert connection.rolled_back is False
    assert result["repair"]["status"] == "APPLIED"
    sql = [statement for statement, _ in connection.fake_cursor.statements]
    assert any(statement.startswith("UPDATE itinerary_items") for statement in sql)
    assert any(statement.startswith("UPDATE repair_runs SET status = 'applied'") for statement in sql)


def test_apply_repair_rolls_back_everything_when_one_action_fails():
    connection = FakeConnection(fail_item_update=True)
    service = _service()
    with _connection_patch(connection), pytest.raises(RepairValidationError, match="missing itinerary item"):
        service.apply_repair(9)

    assert connection.committed is False
    assert connection.rolled_back is True


def test_applying_same_repair_twice_fails_without_itinerary_updates():
    connection = FakeConnection(repair_status="applied")
    service = _service(preview_status="APPLIED")
    with _connection_patch(connection), pytest.raises(RepairValidationError, match="already been applied"):
        service.apply_repair(9)

    assert connection.committed is False
    assert connection.rolled_back is True
    assert not any(
        statement.startswith("UPDATE itinerary_items")
        for statement, _ in connection.fake_cursor.statements
    )
