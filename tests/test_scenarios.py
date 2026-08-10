from copy import deepcopy

import pytest

from detour.scenarios import ScenarioValidationError, apply_scenario, normalize_scenario


LIVE = {
    "temperature_f": 72,
    "precipitation_probability_pct": 10,
    "wind_speed_mph": 5,
    "us_aqi": 35,
}


@pytest.mark.parametrize(
    ("scenario", "field", "minimum"),
    [
        ("RAINSTORM", "precipitation_probability_pct", 95),
        ("HEATWAVE", "apparent_temperature_f", 108),
        ("POOR_AQI", "us_aqi", 175),
    ],
)
def test_scenario_transformations_are_material(scenario, field, minimum):
    result = apply_scenario(LIVE, scenario)
    assert result[field] >= minimum


def test_scenarios_do_not_mutate_live_conditions():
    original = deepcopy(LIVE)
    apply_scenario(LIVE, "RAINSTORM")
    apply_scenario(LIVE, "HEATWAVE")
    apply_scenario(LIVE, "POOR_AQI")
    assert LIVE == original


def test_scenario_names_are_normalized_and_validated():
    assert normalize_scenario("poor aqi") == "POOR_AQI"
    assert normalize_scenario("live") is None
    with pytest.raises(ScenarioValidationError):
        normalize_scenario("tornado")
