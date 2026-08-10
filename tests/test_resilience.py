from copy import deepcopy

from detour.resilience import aggregate_resilience, evaluate_items


def _evaluation(score, status, sensitivity=0.8, vulnerable=False):
    return {
        "condition_score": score,
        "status": status,
        "weather_sensitivity": sensitivity,
        "vulnerable": vulnerable,
    }


def test_resilience_aggregation_is_bounded_explainable_and_counts_vulnerability():
    result = aggregate_resilience(
        [_evaluation(95, "GO", 0.2), _evaluation(45, "AT_RISK", 0.9, vulnerable=True)]
    )
    assert 0 <= result["score"] <= 100
    assert result["label"] in {"RESILIENT", "WATCH", "VULNERABLE"}
    assert result["vulnerable_activity_count"] == 1
    assert "1 of 2" in result["summary"]


def test_rainstorm_worsens_exposed_item_and_preserves_input_state():
    items = [{
        "id": 1, "attraction_id": 10, "title": "Mountain Trail",
        "day_date": "2026-08-10", "start_time": "13:00",
        "indoor_outdoor": "outdoor", "weather_sensitivity": 0.95,
        "activity_level": "high",
    }]
    forecast = {"hourly": [{
        "time": "2026-08-10T13:00", "temperature_f": 72,
        "precipitation_probability_pct": 5, "wind_speed_mph": 4,
    }], "daily": []}
    air = {"hourly": [{"time": "2026-08-10T13:00", "us_aqi": 30}]}
    original_forecast = deepcopy(forecast)
    live = evaluate_items(items, forecast=forecast, air_quality=air)
    storm = evaluate_items(items, forecast=forecast, air_quality=air, scenario="RAINSTORM")
    assert storm["score"] < live["score"]
    assert storm["vulnerable_activity_count"] == 1
    assert storm["simulated"] is True
    assert forecast == original_forecast


def test_heat_and_aqi_penalize_outdoor_activity_more_than_indoor_activity():
    base = {"day_date": "2026-08-10", "start_time": "13:00", "activity_level": "high"}
    items = [
        {**base, "id": 1, "attraction_id": 10, "title": "Trail", "indoor_outdoor": "outdoor", "weather_sensitivity": 0.95},
        {**base, "id": 2, "attraction_id": 11, "title": "Museum", "indoor_outdoor": "indoor", "weather_sensitivity": 0.1},
    ]
    forecast = {"hourly": [{"time": "2026-08-10T13:00", "temperature_f": 72, "precipitation_probability_pct": 0, "wind_speed_mph": 0}], "daily": []}
    air = {"hourly": [{"time": "2026-08-10T13:00", "us_aqi": 20}]}
    for scenario in ("HEATWAVE", "POOR_AQI"):
        result = evaluate_items(items, forecast=forecast, air_quality=air, scenario=scenario)
        by_id = {row["itinerary_item_id"]: row for row in result["item_evaluations"]}
        assert by_id[1]["condition_score"] < by_id[2]["condition_score"]
