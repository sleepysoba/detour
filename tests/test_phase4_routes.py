from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time
from html.parser import HTMLParser
import json
from types import SimpleNamespace

import pytest


class TripFormContractParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_trip_form = False
        self.action = None
        self.method = None
        self.field_names = []
        self.presets = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form" and "data-trip-form" in attributes:
            self.in_trip_form = True
            self.action = attributes.get("action")
            self.method = attributes.get("method")
        elif self.in_trip_form and tag in {"input", "select", "textarea"}:
            if attributes.get("name"):
                self.field_names.append(attributes["name"])
        if tag == "button" and "data-preset" in attributes:
            self.presets.append(attributes)

    def handle_endtag(self, tag):
        if tag == "form" and self.in_trip_form:
            self.in_trip_form = False


def _trip():
    return {
        "id": 42,
        "destination_id": 7,
        "destination_name": "Boulder, Colorado, United States",
        "start_date": date(2026, 8, 10),
        "end_date": date(2026, 8, 11),
        "preferences": ["outdoors", "culture"],
        "pace": "balanced",
        "live_resilience_score": 88,
    }


def _items():
    return [
        {
            "id": 11,
            "trip_id": 42,
            "attraction_id": 101,
            "day_date": date(2026, 8, 10),
            "start_time": time(13, 0),
            "end_time": time(14, 30),
            "title": "Pearl Street Mall",
            "category": "district",
            "indoor_outdoor": "outdoor",
            "weather_sensitivity": 0.85,
            "suitability_score": 88,
            "risk_state": "GO",
            "risk_reasons": ["Conditions look favorable"],
            "notes": "A walkable downtown landmark.",
            "sort_order": 0,
            "activity_level": "moderate",
        }
    ]


def _evaluation(*, storm=False, repaired=False):
    score = 74 if repaired else 38 if storm else 88
    status = "GO" if score >= 80 else "CAUTION" if score >= 55 else "AT_RISK"
    vulnerable = status == "AT_RISK"
    return {
        "score": 86 if repaired else 61 if storm else 88,
        "label": "RESILIENT" if repaired or not storm else "WATCH",
        "vulnerable_activity_count": int(vulnerable),
        "summary": "One activity needs attention." if vulnerable else "The itinerary fits the conditions.",
        "policy_version": "test-v1",
        "scenario": "RAINSTORM" if storm else "LIVE",
        "simulated": storm,
        "item_evaluations": [
            {
                "itinerary_item_id": 11,
                "attraction_id": 202 if repaired else 101,
                "title": "Museum of Boulder" if repaired else "Pearl Street Mall",
                "day_date": "2026-08-10",
                "start_time": "13:00",
                "condition_score": score,
                "status": status,
                "primary_risk_factors": ["Very high rain probability"] if storm and not repaired else ["Indoor activity is weather resilient"],
                "weather_sensitivity": 0.15 if repaired else 0.85,
                "indoor_outdoor": "indoor" if repaired else "outdoor",
                "vulnerable": vulnerable,
                "signals": {},
                "penalties": {},
            }
        ],
    }


class FakeTrips:
    def __init__(self):
        self.trip = _trip()
        self.items = _items()
        self.snapshot_available = True

    def get_trip(self, trip_id):
        return deepcopy(self.trip) if trip_id == 42 else None

    def get_itinerary(self, trip_id):
        return deepcopy(self.items) if trip_id == 42 else []

    def get_latest_weather_snapshot(self, trip_id):
        if not self.snapshot_available:
            return None
        return {
            "id": 9,
            "forecast_json": {
                "daily": [
                    {
                        "date": "2026-08-10",
                        "weather_code": 1,
                        "high_temperature_f": 83,
                        "low_temperature_f": 59,
                        "precipitation_probability_pct": 12,
                    }
                ]
            },
            "air_quality_json": {"hourly": [{"time": "2026-08-10T13:00", "us_aqi": 42}]},
        }


class FakeResilience:
    def __init__(self, trips):
        self.trips = trips

    def evaluate_trip_resilience(self, trip_id, scenario=None):
        repaired = self.trips.items[0]["title"] == "Museum of Boulder"
        return deepcopy(_evaluation(storm=scenario == "RAINSTORM", repaired=repaired))

    def evaluate_state(self, trip_id, items, scenario=None):
        repaired = items[0]["title"] == "Museum of Boulder"
        return deepcopy(_evaluation(storm=scenario == "RAINSTORM", repaired=repaired))


class FakeRepairs:
    def __init__(self, trips):
        self.trips = trips

    def preview(self):
        before = deepcopy(_items()[0])
        before.update({"day_date": "2026-08-10", "start_time": "13:00", "end_time": "14:30"})
        after = deepcopy(before)
        after.update(
            {
                "attraction_id": 202,
                "title": "Museum of Boulder",
                "category": "museum",
                "indoor_outdoor": "indoor",
                "weather_sensitivity": 0.15,
                "notes": "Local stories in a weather-resilient setting.",
            }
        )
        return {
            "repair_id": 55,
            "trip_id": 42,
            "trace_id": "dbd09c89-test",
            "scenario": "RAINSTORM",
            "status": "PENDING",
            "resilience_before": 61,
            "resilience_projected": 86,
            "rationale": "A stored indoor replacement scores better under simulated rain.",
            "actions": [
                {
                    "id": 1,
                    "action_type": "REPLACE",
                    "itinerary_item_id": 11,
                    "before": before,
                    "after": after,
                    "reason": "Condition score 38 -> 74 under RAINSTORM conditions.",
                }
            ],
            "before_itinerary": [before],
            "proposed_itinerary": [after],
            "created_at": datetime(2026, 8, 9, 12, 0),
        }

    def get_preview(self, repair_id):
        if repair_id != 55:
            raise ValueError("Repair proposal was not found.")
        return self.preview()

    def apply_repair(self, repair_id):
        preview = self.get_preview(repair_id)
        self.trips.items[0].update(
            {
                "attraction_id": 202,
                "title": "Museum of Boulder",
                "category": "museum",
                "indoor_outdoor": "indoor",
                "weather_sensitivity": 0.15,
            }
        )
        preview["status"] = "APPLIED"
        return {
            "repair": preview,
            "itinerary": self.trips.get_itinerary(42),
            "resilience": _evaluation(storm=True, repaired=True),
            "live_resilience": _evaluation(repaired=True),
        }


@pytest.fixture()
def phase4_services():
    trips = FakeTrips()
    resilience = FakeResilience(trips)
    repairs = FakeRepairs(trips)
    creator_calls = []

    def create(**values):
        creator_calls.append(values)
        return {"trip": _trip(), "destination_ingestion": {"cached": False}}

    services = SimpleNamespace(
        trips=trips,
        resilience=resilience,
        repairs=repairs,
        trip_creator=SimpleNamespace(create=create),
        itineraries=SimpleNamespace(generate=lambda trip_id: {"trip": _trip(), "items": _items()}),
        agent=SimpleNamespace(
            run=lambda trip_id, scenario: {
                "trace_id": "dbd09c89-test",
                "proposal": repairs.preview(),
                "tool_call_count": 5,
                "model_call_count": 5,
                "used_deterministic_fallback": False,
                "validation_failures": [],
            }
        ),
        traces=SimpleNamespace(
            get_events=lambda trace_id: [
                {
                    "trace_id": trace_id,
                    "event_type": "AGENT_STARTED",
                    "tool_name": None,
                    "status": "started",
                    "input_summary": {"scenario": "RAINSTORM"},
                    "output_summary": None,
                    "duration_ms": None,
                    "model": "system.ai.meta-llama-3-3-70b-instruct",
                    "created_at": datetime(2026, 8, 9, 12, 0),
                },
                {
                    "trace_id": trace_id,
                    "event_type": "TOOL_CALLED",
                    "tool_name": "get_trip_state",
                    "status": "ok",
                    "input_summary": {"argument_keys": ["trip_id"]},
                    "output_summary": {"result_keys": ["trip", "itinerary"]},
                    "duration_ms": 12,
                    "model": None,
                    "created_at": datetime(2026, 8, 9, 12, 0, 1),
                },
            ]
        ),
        ask=SimpleNamespace(
            answer=lambda trip_id, question, scenario: {
                "answer": "Pearl Street is exposed to the simulated rain. Use Repair My Trip to review a safer option.",
                "scenario": scenario or "LIVE",
                "simulated": bool(scenario),
                "read_only": True,
            }
        ),
        creator_calls=creator_calls,
    )
    return services


@pytest.fixture()
def phase4_client(app, phase4_services):
    app.config["DETOUR_SERVICES"] = phase4_services
    return app.test_client()


def test_landing_explains_product_and_has_presets(phase4_client):
    response = phase4_client.get("/")
    assert response.status_code == 200
    assert b"Plans change" in response.data
    assert b"Boulder Adventure" in response.data
    assert b"Austin Weekend" in response.data
    assert b"Miami Escape" in response.data
    assert b"Recommended" in response.data
    assert b"Seattle Weekend" not in response.data
    assert b'name="destination"' in response.data


def test_landing_form_contract_uses_exact_trip_creation_field_names(phase4_client):
    response = phase4_client.get("/")
    parser = TripFormContractParser()
    parser.feed(response.get_data(as_text=True))

    assert parser.action == "/trips"
    assert parser.method == "post"
    assert set(parser.field_names) == {
        "destination",
        "start_date",
        "end_date",
        "preferences",
        "pace",
    }


def test_loading_javascript_keeps_named_inputs_enabled_for_native_submission(phase4_client):
    script = phase4_client.get("/static/js/app.js").get_data(as_text=True)

    assert 'form.querySelectorAll("button")' in script
    assert 'form.querySelectorAll("button, input")' not in script


@pytest.mark.parametrize(
    ("index", "destination", "preferences"),
    [
        (0, "Boulder, Colorado", ["Outdoors", "Photography", "Adventure", "Culture"]),
        (1, "Austin, Texas", ["Food", "Culture", "Outdoors", "Photography"]),
        (2, "Miami, Florida", ["Food", "Photography", "Relaxed", "Culture"]),
    ],
)
def test_curated_preset_submits_verified_real_values(
    phase4_client, index, destination, preferences
):
    parser = TripFormContractParser()
    parser.feed(phase4_client.get("/").get_data(as_text=True))
    preset = parser.presets[index]

    assert preset["data-destination"] == destination
    assert json.loads(preset["data-preferences"]) == preferences
    assert preset["data-pace"] == "balanced"


def test_arbitrary_trip_creation_uses_service_contract(phase4_client, phase4_services):
    response = phase4_client.post(
        "/trips",
        data={
            "destination": "Boulder, Colorado",
            "start_date": "2026-08-10",
            "end_date": "2026-08-11",
            "preferences": ["Outdoors", "Culture"],
            "pace": "balanced",
        },
    )
    assert response.status_code == 303
    assert response.headers["Location"].endswith("/trips/42")
    assert phase4_services.creator_calls[0] == {
        "destination": "Boulder, Colorado",
        "start_date": "2026-08-10",
        "end_date": "2026-08-11",
        "preferences": ["Outdoors", "Culture"],
        "pace": "balanced",
    }


def test_dashboard_renders_persisted_itinerary_weather_and_resilience(phase4_client):
    response = phase4_client.get("/trips/42")
    assert response.status_code == 200
    assert b"Pearl Street Mall" in response.data
    assert b"TRIP RESILIENCE" in response.data.upper()
    assert b"AQI 42" in response.data
    assert b"LIVE CONDITIONS" in response.data


def test_future_trip_creation_skips_live_resilience(phase4_client, phase4_services):
    phase4_services.itineraries.generate = lambda trip_id: {
        "trip": _trip(),
        "items": _items(),
        "live_conditions_available": False,
    }
    phase4_services.resilience.evaluate_trip_resilience = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("future creation must not evaluate live resilience")
    )

    response = phase4_client.post(
        "/trips",
        data={
            "destination": "Boulder, Colorado",
            "start_date": "2026-09-10",
            "end_date": "2026-09-11",
            "preferences": ["Culture"],
            "pace": "balanced",
        },
    )

    assert response.status_code == 303


def test_future_trip_renders_planning_mode_without_fabricated_conditions(
    phase4_client, phase4_services
):
    phase4_services.trips.snapshot_available = False
    phase4_services.trips.trip["start_date"] = date(2026, 9, 10)
    phase4_services.trips.trip["end_date"] = date(2026, 9, 11)

    response = phase4_client.get("/trips/42")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "PLANNING MODE" in html
    assert "Live resilience unlocks closer to departure" in html
    assert "Weather and air-quality forecasts are not available" in html
    assert "LIVE CONDITIONS" not in html
    assert 'class="weather-day"' not in html
    assert 'class="score-ring"' not in html


def test_rainstorm_endpoint_is_clearly_simulated(phase4_client):
    response = phase4_client.get("/api/trips/42/resilience?scenario=rainstorm")
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["scenario"] == "RAINSTORM"
    assert payload["simulated"] is True
    assert payload["score"] == 61


def test_repair_endpoint_returns_pending_preview_without_mutation(phase4_client, phase4_services):
    before = phase4_services.trips.get_itinerary(42)
    response = phase4_client.post("/api/trips/42/repair", json={"scenario": "RAINSTORM"})
    payload = response.get_json()
    assert response.status_code == 201
    assert payload["proposal"]["status"] == "PENDING"
    assert payload["proposal"]["impact"]["risks_resolved"] == 1
    assert phase4_services.trips.get_itinerary(42) == before


def test_apply_endpoint_mutates_persisted_itinerary(phase4_client, phase4_services):
    response = phase4_client.post("/api/repairs/55/apply", json={})
    assert response.status_code == 200
    assert response.get_json()["repair"]["status"] == "APPLIED"
    assert phase4_services.trips.get_itinerary(42)[0]["title"] == "Museum of Boulder"


def test_trace_endpoint_returns_only_safe_observable_events(phase4_client):
    response = phase4_client.get("/api/traces/dbd09c89-test")
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["summary"]["tool_calls"] == 1
    assert payload["events"][1]["tool_name"] == "get_trip_state"
    assert "prompt" not in response.get_data(as_text=True).lower()
    assert "token" not in response.get_data(as_text=True).lower()


def test_ask_detour_is_valid_and_read_only(phase4_client):
    response = phase4_client.post(
        "/api/trips/42/ask",
        json={"question": "Why is Pearl Street at risk?", "scenario": "RAINSTORM"},
    )
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["read_only"] is True
    assert "Repair My Trip" in payload["answer"]


def test_api_failure_never_exposes_unknown_exception_details(app, phase4_services):
    def fail(*args, **kwargs):
        raise RuntimeError("postgres://user:secret@example.internal/database")

    phase4_services.agent.run = fail
    app.config["DETOUR_SERVICES"] = phase4_services
    response = app.test_client().post("/api/trips/42/repair", json={"scenario": "RAINSTORM"})
    assert response.status_code == 400
    assert "postgres" not in response.get_data(as_text=True).lower()
    assert "secret" not in response.get_data(as_text=True).lower()
