from datetime import date

import pytest

from detour.itinerary import (
    ItineraryValidationError,
    build_schedule_slots,
    validate_itinerary_selection,
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
