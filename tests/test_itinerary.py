from datetime import date
from types import SimpleNamespace

import pytest

from detour.itinerary import (
    ItineraryValidationError,
    build_schedule_slots,
    validate_itinerary_selection,
    ItineraryService,
)


CANDIDATES = [{"id": index, "name": f"Attraction {index}"} for index in range(1, 7)]
SLOTS = build_schedule_slots(
    start_date=date(2026, 8, 9),
    end_date=date(2026, 8, 10),
    pace="balanced",
    candidate_count=len(CANDIDATES),
)


def test_balanced_schedule_distributes_three_unique_slots_per_day():
    assert len(SLOTS) == 6
    assert sum(slot["day_date"] == "2026-08-09" for slot in SLOTS) == 3
    assert sum(slot["day_date"] == "2026-08-10" for slot in SLOTS) == 3


def test_itinerary_validation_accepts_only_supplied_candidate_ids():
    payload = {
        "items": [
            {"attraction_id": candidate["id"], **slot}
            for candidate, slot in zip(CANDIDATES, SLOTS, strict=True)
        ]
    }

    result = validate_itinerary_selection(payload, candidates=CANDIDATES, slots=SLOTS)

    assert [row["attraction_id"] for row in result] == [1, 2, 3, 4, 5, 6]


def test_itinerary_validation_rejects_unknown_ids():
    payload = {
        "items": [
            {"attraction_id": 999 if index == 0 else candidate["id"], **slot}
            for index, (candidate, slot) in enumerate(zip(CANDIDATES, SLOTS, strict=True))
        ]
    }

    with pytest.raises(ItineraryValidationError, match="Unknown attraction ID"):
        validate_itinerary_selection(payload, candidates=CANDIDATES, slots=SLOTS)


def test_itinerary_validation_rejects_duplicate_attractions():
    payload = {
        "items": [
            {"attraction_id": 1 if index < 2 else candidate["id"], **slot}
            for index, (candidate, slot) in enumerate(zip(CANDIDATES, SLOTS, strict=True))
        ]
    }

    with pytest.raises(ItineraryValidationError, match="cannot appear twice"):
        validate_itinerary_selection(payload, candidates=CANDIDATES, slots=SLOTS)


def test_future_itinerary_generation_never_requests_unavailable_weather():
    candidates = [
        {
            "id": index,
            "name": f"Museum {index}",
            "description": "A public museum and cultural visitor attraction.",
            "traveler_summary": "A cultural stop.",
            "category": "museum",
            "indoor_outdoor": "indoor",
            "weather_sensitivity": 0.1,
            "activity_level": "low",
            "estimated_duration_minutes": 90,
            "tags": ["culture"],
            "similarity": 0.8,
        }
        for index in range(1, 7)
    ]

    class Trips:
        saved_items = None

        def get_trip(self, trip_id):
            return {
                "id": trip_id,
                "destination_id": 7,
                "destination_name": "Boulder, Colorado",
                "latitude": 40.0,
                "longitude": -105.2,
                "start_date": date(2026, 9, 10),
                "end_date": date(2026, 9, 11),
                "preferences": ["culture"],
                "pace": "balanced",
            }

        def save_weather_snapshot(self, **kwargs):
            raise AssertionError("future trips must not save weather")

        def replace_itinerary(self, *, trip_id, items):
            self.saved_items = items
            return list(range(101, 101 + len(items)))

    class Weather:
        def get_forecast(self, *args, **kwargs):
            raise AssertionError("future trips must not request forecasts")

        def get_air_quality(self, *args, **kwargs):
            raise AssertionError("future trips must not request AQI")

    trips = Trips()
    service = ItineraryService(
        trips=trips,
        attractions=SimpleNamespace(list_attractions=lambda *args, **kwargs: candidates),
        search=SimpleNamespace(search=lambda **kwargs: candidates),
        weather=Weather(),
        llm=SimpleNamespace(
            model="test-model",
            create_message=lambda messages: (SimpleNamespace(content="{}"), 1),
        ),
        today_provider=lambda: date(2026, 8, 9),
    )

    result = service.generate(42)

    assert result["live_conditions_available"] is False
    assert result["weather_snapshot_id"] is None
    assert all(item["suitability_score"] is None for item in trips.saved_items)
    assert all(item["risk_state"] is None for item in trips.saved_items)
