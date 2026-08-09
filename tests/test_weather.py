from unittest.mock import Mock

import pytest
import requests

from detour.weather import OpenMeteoError, OpenMeteoService


def response(payload):
    result = Mock()
    result.json.return_value = payload
    result.raise_for_status.return_value = None
    return result


def test_geocoding_normalizes_city_metadata():
    session = Mock()
    session.get.return_value = response(
        {
            "results": [
                {
                    "id": 5574991,
                    "name": "Boulder",
                    "latitude": 40.01499,
                    "longitude": -105.27055,
                    "timezone": "America/Denver",
                    "country": "United States",
                    "country_code": "US",
                    "admin1": "Colorado",
                }
            ]
        }
    )

    result = OpenMeteoService(session=session).resolve_city("Boulder, Colorado")

    assert result["display_name"] == "Boulder, Colorado, United States"
    assert result["latitude"] == 40.01499
    assert result["admin_region"] == "Colorado"


def test_geocoding_handles_no_results_and_timeout():
    no_results = Mock()
    no_results.get.return_value = response({"results": []})
    with pytest.raises(OpenMeteoError, match="Could not resolve") as not_found:
        OpenMeteoService(session=no_results).resolve_city("Not A Real Place")
    assert not_found.value.code == "LOCATION_NOT_FOUND"

    timed_out = Mock()
    timed_out.get.side_effect = requests.Timeout("private upstream detail")
    with pytest.raises(OpenMeteoError, match="timed out") as timeout:
        OpenMeteoService(session=timed_out).resolve_city("Boulder")
    assert timeout.value.code == "UPSTREAM_TIMEOUT"
    assert "private upstream detail" not in str(timeout.value)


def test_geocoding_rejects_malformed_coordinates():
    session = Mock()
    session.get.return_value = response({"results": [{"name": "Boulder", "latitude": "bad"}]})

    with pytest.raises(OpenMeteoError) as error:
        OpenMeteoService(session=session).resolve_city("Boulder")

    assert error.value.code == "MALFORMED_RESPONSE"


def test_forecast_normalizes_hourly_and_daily_records():
    session = Mock()
    session.get.return_value = response(
        {
            "latitude": 40.0,
            "longitude": -105.2,
            "timezone": "America/Denver",
            "hourly": {
                "time": ["2026-08-09T10:00"],
                "temperature_2m": [72.0],
                "apparent_temperature": [71.0],
                "precipitation_probability": [20],
                "precipitation": [0.0],
                "weather_code": [1],
                "wind_speed_10m": [6.2],
            },
            "daily": {
                "time": ["2026-08-09"],
                "weather_code": [1],
                "temperature_2m_max": [82.0],
                "temperature_2m_min": [60.0],
                "precipitation_probability_max": [25],
                "precipitation_sum": [0.01],
                "wind_speed_10m_max": [12.0],
                "uv_index_max": [7.1],
                "sunrise": ["2026-08-09T06:05"],
                "sunset": ["2026-08-09T20:04"],
            },
        }
    )

    result = OpenMeteoService(session=session).get_forecast(40.0, -105.2, forecast_days=1)

    assert result["hourly"][0]["apparent_temperature_f"] == 71.0
    assert result["daily"][0]["date"] == "2026-08-09"
    assert result["daily"][0]["sunrise"] == "2026-08-09T06:05"


def test_air_quality_normalizes_required_fields():
    session = Mock()
    session.get.return_value = response(
        {
            "latitude": 25.7,
            "longitude": -80.2,
            "timezone": "America/New_York",
            "hourly": {
                "time": ["2026-08-09T10:00"],
                "us_aqi": [42],
                "pm2_5": [8.2],
                "pm10": [14.1],
            },
        }
    )

    result = OpenMeteoService(session=session).get_air_quality(25.7, -80.2, forecast_days=1)

    assert result["hourly"][0] == {
        "time": "2026-08-09T10:00",
        "us_aqi": 42.0,
        "pm2_5_ug_m3": 8.2,
        "pm10_ug_m3": 14.1,
    }
