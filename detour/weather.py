"""Open-Meteo geocoding, forecast, and air-quality integration."""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

import requests

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
PROVIDER = "Open-Meteo"
MAX_FORECAST_DAYS = 7

logger = logging.getLogger(__name__)


class OpenMeteoError(RuntimeError):
    """Normalized Open-Meteo failure safe for application error handling."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise OpenMeteoError("MALFORMED_RESPONSE", f"Open-Meteo returned invalid {field} data.")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise OpenMeteoError(
            "MALFORMED_RESPONSE", f"Open-Meteo returned invalid {field} data."
        ) from exc


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not numeric")
    return float(value)


def _parallel_records(section: Any, fields: tuple[str, ...], provider_name: str) -> list[dict]:
    if not isinstance(section, dict):
        raise OpenMeteoError("MALFORMED_RESPONSE", f"{provider_name} data was missing.")
    times = section.get("time")
    if not isinstance(times, list) or not times:
        raise OpenMeteoError("MALFORMED_RESPONSE", f"{provider_name} timestamps were missing.")

    columns: dict[str, list] = {}
    for field in fields:
        values = section.get(field)
        if not isinstance(values, list) or len(values) != len(times):
            raise OpenMeteoError("MALFORMED_RESPONSE", f"{provider_name} data was incomplete.")
        columns[field] = values

    records: list[dict] = []
    try:
        for index, timestamp in enumerate(times):
            record = {"time": str(timestamp)}
            record.update({field: _optional_number(columns[field][index]) for field in fields})
            records.append(record)
    except (TypeError, ValueError) as exc:
        raise OpenMeteoError("MALFORMED_RESPONSE", f"{provider_name} contained invalid values.") from exc
    return records


class OpenMeteoService:
    """Small reusable client for Detour's required Open-Meteo primitives."""

    def __init__(self, *, timeout_seconds: int = 10, session: requests.Session | None = None):
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def _get_json(self, url: str, params: dict[str, Any], integration: str) -> dict:
        started = perf_counter()
        try:
            response = self.session.get(url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except requests.Timeout as exc:
            raise OpenMeteoError("UPSTREAM_TIMEOUT", f"{integration} timed out.") from exc
        except requests.RequestException as exc:
            raise OpenMeteoError("UPSTREAM_ERROR", f"{integration} request failed.") from exc
        except ValueError as exc:
            raise OpenMeteoError("MALFORMED_RESPONSE", f"{integration} returned invalid JSON.") from exc

        if not isinstance(payload, dict) or payload.get("error"):
            raise OpenMeteoError("MALFORMED_RESPONSE", f"{integration} returned an invalid response.")
        logger.info("integration=%s duration_ms=%d", integration, round((perf_counter() - started) * 1000))
        return payload

    @staticmethod
    def _validate_days(days: int) -> int:
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_FORECAST_DAYS:
            raise OpenMeteoError(
                "INVALID_ARGUMENT", f"forecast_days must be an integer from 1 to {MAX_FORECAST_DAYS}."
            )
        return days

    def resolve_city(self, city: str) -> dict:
        """Resolve an arbitrary city string to normalized coordinates and metadata."""
        requested_name = (city or "").strip()
        if not requested_name:
            raise OpenMeteoError("INVALID_ARGUMENT", "City is required.")

        payload = self._get_json(
            GEOCODING_URL,
            {"name": requested_name, "count": 5, "language": "en", "format": "json"},
            "Open-Meteo geocoding",
        )
        results = payload.get("results")
        if results is None or results == []:
            raise OpenMeteoError("LOCATION_NOT_FOUND", f"Could not resolve city '{requested_name}'.")
        if not isinstance(results, list) or not isinstance(results[0], dict):
            raise OpenMeteoError("MALFORMED_RESPONSE", "Open-Meteo geocoding returned invalid results.")

        result = results[0]
        name = result.get("name")
        if not isinstance(name, str) or not name.strip():
            raise OpenMeteoError("MALFORMED_RESPONSE", "Open-Meteo geocoding omitted the city name.")
        admin_region = result.get("admin1")
        country = result.get("country")
        display_parts = [name.strip(), admin_region, country]
        display_name = ", ".join(dict.fromkeys(str(part) for part in display_parts if part))

        normalized = {
            "requested_name": requested_name,
            "display_name": display_name,
            "latitude": _number(result.get("latitude"), "latitude"),
            "longitude": _number(result.get("longitude"), "longitude"),
            "timezone": result.get("timezone") or "auto",
            "country": country,
            "country_code": result.get("country_code"),
            "admin_region": admin_region,
            "provider_id": result.get("id"),
            "provider": PROVIDER,
        }
        logger.info("destination_resolved display_name=%s provider=%s", display_name, PROVIDER)
        return normalized

    def get_forecast(self, latitude: float, longitude: float, *, forecast_days: int = 5) -> dict:
        """Fetch normalized hourly and daily forecast data for coordinates."""
        days = self._validate_days(forecast_days)
        payload = self._get_json(
            FORECAST_URL,
            {
                "latitude": latitude,
                "longitude": longitude,
                "hourly": ",".join(
                    [
                        "temperature_2m",
                        "apparent_temperature",
                        "precipitation_probability",
                        "precipitation",
                        "weather_code",
                        "wind_speed_10m",
                    ]
                ),
                "daily": ",".join(
                    [
                        "weather_code",
                        "temperature_2m_max",
                        "temperature_2m_min",
                        "precipitation_probability_max",
                        "precipitation_sum",
                        "wind_speed_10m_max",
                        "uv_index_max",
                        "sunrise",
                        "sunset",
                    ]
                ),
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "precipitation_unit": "inch",
                "timezone": "auto",
                "forecast_days": days,
            },
            "Open-Meteo forecast",
        )

        hourly = _parallel_records(
            payload.get("hourly"),
            (
                "temperature_2m",
                "apparent_temperature",
                "precipitation_probability",
                "precipitation",
                "weather_code",
                "wind_speed_10m",
            ),
            "Open-Meteo hourly forecast",
        )
        hourly_names = {
            "temperature_2m": "temperature_f",
            "apparent_temperature": "apparent_temperature_f",
            "precipitation_probability": "precipitation_probability_pct",
            "precipitation": "precipitation_in",
            "wind_speed_10m": "wind_speed_mph",
        }
        for record in hourly:
            for provider_field, normalized_field in hourly_names.items():
                record[normalized_field] = record.pop(provider_field)
            if record["weather_code"] is not None:
                record["weather_code"] = int(record["weather_code"])

        daily_section = payload.get("daily")
        if not isinstance(daily_section, dict):
            raise OpenMeteoError("MALFORMED_RESPONSE", "Open-Meteo daily forecast was missing.")
        daily_times = daily_section.get("time")
        daily_fields = (
            "weather_code",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_probability_max",
            "precipitation_sum",
            "wind_speed_10m_max",
            "uv_index_max",
        )
        daily = _parallel_records(daily_section, daily_fields, "Open-Meteo daily forecast")
        sunrise = daily_section.get("sunrise")
        sunset = daily_section.get("sunset")
        if not isinstance(daily_times, list) or not isinstance(sunrise, list) or not isinstance(sunset, list):
            raise OpenMeteoError("MALFORMED_RESPONSE", "Open-Meteo sunrise/sunset data was missing.")
        if len(sunrise) != len(daily) or len(sunset) != len(daily):
            raise OpenMeteoError("MALFORMED_RESPONSE", "Open-Meteo sunrise/sunset data was incomplete.")
        for index, record in enumerate(daily):
            record["date"] = record.pop("time")
            record["high_temperature_f"] = record.pop("temperature_2m_max")
            record["low_temperature_f"] = record.pop("temperature_2m_min")
            record["precipitation_probability_pct"] = record.pop(
                "precipitation_probability_max"
            )
            record["precipitation_in"] = record.pop("precipitation_sum")
            record["max_wind_speed_mph"] = record.pop("wind_speed_10m_max")
            record["uv_index"] = record.pop("uv_index_max")
            if record["weather_code"] is not None:
                record["weather_code"] = int(record["weather_code"])
            record["sunrise"] = sunrise[index]
            record["sunset"] = sunset[index]

        return {
            "provider": PROVIDER,
            "latitude": _number(payload.get("latitude"), "latitude"),
            "longitude": _number(payload.get("longitude"), "longitude"),
            "timezone": payload.get("timezone") or "auto",
            "hourly": hourly,
            "daily": daily,
        }

    def get_air_quality(self, latitude: float, longitude: float, *, forecast_days: int = 5) -> dict:
        """Fetch normalized hourly US AQI and particulate measurements."""
        days = self._validate_days(forecast_days)
        payload = self._get_json(
            AIR_QUALITY_URL,
            {
                "latitude": latitude,
                "longitude": longitude,
                "hourly": "us_aqi,pm2_5,pm10",
                "timezone": "auto",
                "forecast_days": days,
            },
            "Open-Meteo air quality",
        )
        hourly = _parallel_records(
            payload.get("hourly"),
            ("us_aqi", "pm2_5", "pm10"),
            "Open-Meteo hourly air quality",
        )
        for record in hourly:
            record["pm2_5_ug_m3"] = record.pop("pm2_5")
            record["pm10_ug_m3"] = record.pop("pm10")
        return {
            "provider": PROVIDER,
            "latitude": _number(payload.get("latitude"), "latitude"),
            "longitude": _number(payload.get("longitude"), "longitude"),
            "timezone": payload.get("timezone") or "auto",
            "hourly": hourly,
        }
