from datetime import date

import pytest

from detour.trips import TripValidationError, validate_trip_input


TODAY = date(2026, 8, 9)


def test_trip_validation_accepts_live_five_day_trip_and_normalizes_preferences():
    result = validate_trip_input(
        destination=" Boulder, Colorado ",
        start_date="2026-08-10",
        end_date="2026-08-14",
        preferences=["Culture", "culture", "Photography"],
        pace="Balanced",
        today=TODAY,
    )

    assert result["duration_days"] == 5
    assert result["forecast_days"] == 6
    assert result["preferences"] == ["culture", "photography"]
    assert result["pace"] == "balanced"


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ("2026-08-08", "2026-08-09", "past"),
        ("2026-08-09", "2026-08-14", "1 to 5"),
        ("2026-08-14", "2026-08-16", "forecast window"),
        ("2026-08-11", "2026-08-10", "1 to 5"),
    ],
)
def test_trip_validation_rejects_bad_date_ranges(start, end, message):
    with pytest.raises(TripValidationError, match=message):
        validate_trip_input(
            destination="Boulder",
            start_date=start,
            end_date=end,
            preferences=[],
            pace="balanced",
            today=TODAY,
        )


def test_trip_validation_rejects_unknown_pace():
    with pytest.raises(TripValidationError, match="relaxed, balanced, or packed"):
        validate_trip_input(
            destination="Boulder",
            start_date="2026-08-09",
            end_date="2026-08-10",
            preferences=[],
            pace="rushed",
            today=TODAY,
        )
