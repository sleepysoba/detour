from detour.scoring import evaluate_activity_conditions


RAINY_POOR_AIR = {
    "precipitation_probability_pct": 90,
    "temperature_f": 72,
    "wind_speed_mph": 20,
    "us_aqi": 135,
}


def test_indoor_museum_remains_more_suitable_than_outdoor_trail():
    museum = evaluate_activity_conditions(
        {
            "indoor_outdoor": "indoor",
            "weather_sensitivity": 0.1,
            "activity_level": "low",
        },
        RAINY_POOR_AIR,
    )
    trail = evaluate_activity_conditions(
        {
            "indoor_outdoor": "outdoor",
            "weather_sensitivity": 0.95,
            "activity_level": "high",
        },
        RAINY_POOR_AIR,
    )

    assert museum["score"] >= 90
    assert trail["score"] < museum["score"]
    assert "High rain probability" in trail["reasons"] or "Very high rain probability" in trail["reasons"]
    assert "Poor AQI for outdoor activity" in trail["reasons"]


def test_condition_score_is_bounded_and_has_explainable_label():
    result = evaluate_activity_conditions(
        {"indoor_outdoor": "outdoor", "weather_sensitivity": 1.0},
        {
            "precipitation_probability_pct": 100,
            "temperature_f": 110,
            "wind_speed_mph": 50,
            "us_aqi": 250,
        },
    )

    assert 0 <= result["score"] <= 100
    assert result["state"] in {"GO", "CAUTION", "AT_RISK"}
    assert 1 <= len(result["reasons"]) <= 3
