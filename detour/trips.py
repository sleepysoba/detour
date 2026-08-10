"""Validated trip creation over the destination intelligence layer."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

from detour.destination import DestinationIngestionService
from detour.models import TripRepository
from detour.weather import MAX_FORECAST_DAYS

VALID_PACES = {"relaxed", "balanced", "packed"}
MAX_TRIP_DAYS = 5


class TripValidationError(ValueError):
    """Raised before network or persistence work for invalid trip input."""


def live_conditions_available(
    start_date: date, end_date: date, *, today: date | None = None
) -> bool:
    """Return whether the complete trip fits inside Open-Meteo's live window."""
    current_day = today or date.today()
    last_forecast_day = current_day + timedelta(days=MAX_FORECAST_DAYS - 1)
    return start_date >= current_day and end_date <= last_forecast_day


def validate_trip_input(
    *,
    destination: str,
    start_date: str | date,
    end_date: str | date,
    preferences: list[str],
    pace: str,
    today: date | None = None,
) -> dict:
    destination_name = (destination or "").strip()
    if not destination_name or len(destination_name) > 160:
        raise TripValidationError("Destination must be a short non-empty city name.")
    try:
        start = start_date if isinstance(start_date, date) else date.fromisoformat(str(start_date))
        end = end_date if isinstance(end_date, date) else date.fromisoformat(str(end_date))
    except (TypeError, ValueError) as exc:
        raise TripValidationError("Trip dates must use YYYY-MM-DD format.") from exc

    current_day = today or date.today()
    duration_days = (end - start).days + 1
    if duration_days < 1 or duration_days > MAX_TRIP_DAYS:
        raise TripValidationError("Trips must be from 1 to 5 days long.")
    if start < current_day:
        raise TripValidationError("Trip start date cannot be in the past.")
    has_live_conditions = live_conditions_available(start, end, today=current_day)

    normalized_pace = (pace or "").strip().casefold()
    if normalized_pace not in VALID_PACES:
        raise TripValidationError("Pace must be relaxed, balanced, or packed.")
    if not isinstance(preferences, list) or len(preferences) > 10:
        raise TripValidationError("Preferences must be a list with at most 10 values.")
    normalized_preferences: list[str] = []
    for preference in preferences:
        if not isinstance(preference, str) or not preference.strip() or len(preference.strip()) > 40:
            raise TripValidationError("Each preference must be a short non-empty string.")
        normalized = preference.strip().casefold()
        if normalized not in normalized_preferences:
            normalized_preferences.append(normalized)

    return {
        "destination": destination_name,
        "start_date": start,
        "end_date": end,
        "preferences": normalized_preferences,
        "pace": normalized_pace,
        "duration_days": duration_days,
        "forecast_days": (end - current_day).days + 1 if has_live_conditions else None,
        "live_conditions_available": has_live_conditions,
    }


class TripCreationService:
    """Create a trip only after its arbitrary destination data is ready."""

    def __init__(
        self,
        *,
        destinations: DestinationIngestionService,
        repository: TripRepository,
        today_provider: Callable[[], date] = date.today,
    ):
        self.destinations = destinations
        self.repository = repository
        self.today_provider = today_provider

    def create(
        self,
        *,
        destination: str,
        start_date: str | date,
        end_date: str | date,
        preferences: list[str],
        pace: str,
    ) -> dict:
        validated = validate_trip_input(
            destination=destination,
            start_date=start_date,
            end_date=end_date,
            preferences=preferences,
            pace=pace,
            today=self.today_provider(),
        )
        ingestion = self.destinations.ingest(validated["destination"])
        trip_id = self.repository.create_trip(
            destination_id=ingestion["destination"]["id"],
            start_date=validated["start_date"].isoformat(),
            end_date=validated["end_date"].isoformat(),
            preferences=validated["preferences"],
            pace=validated["pace"],
        )
        trip = self.repository.get_trip(trip_id)
        if trip is None:
            raise RuntimeError("Persisted trip could not be read back.")
        return {"trip": trip, "destination_ingestion": ingestion}
