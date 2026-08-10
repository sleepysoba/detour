import json

from detour.enrichment import (
    deterministic_metadata,
    normalize_metadata,
    parse_enrichment_payload,
    semantic_document,
)


CANDIDATE = {
    "page_id": "42",
    "name": "Boulder Art Museum",
    "description": "A local museum with galleries focused on regional art and history.",
    "source_url": "https://en.wikipedia.org/wiki/Boulder_Art_Museum",
}


def test_deterministic_enrichment_defaults_are_weather_aware():
    metadata = deterministic_metadata(CANDIDATE)

    assert metadata["category"] == "museum"
    assert metadata["indoor_outdoor"] == "indoor"
    assert metadata["activity_level"] == "low"
    assert metadata["weather_sensitivity"] < 0.3


def test_enrichment_parser_preserves_candidates_and_falls_back_per_item():
    second = {
        **CANDIDATE,
        "page_id": "43",
        "name": "Mountain Trail",
        "description": "A steep outdoor hiking trail across a scenic mountain ridge.",
    }
    payload = json.dumps(
        {
            "attractions": [
                {
                    "page_id": "42",
                    "category": "art museum",
                    "indoor_outdoor": "indoor",
                    "weather_sensitivity": 0.1,
                    "activity_level": "low",
                    "estimated_duration_minutes": 100,
                    "tags": ["Art", "Rainy Day"],
                    "traveler_summary": "A compact regional museum suited to an indoor cultural visit.",
                },
                {"page_id": "invented", "category": "fake"},
            ]
        }
    )

    results = parse_enrichment_payload(payload, [CANDIDATE, second])

    assert [row["page_id"] for row in results] == ["42", "43"]
    assert results[0]["category"] == "art_museum"
    assert results[0]["enrichment_source"] == "llama"
    assert results[1]["enrichment_source"] == "deterministic_fallback"
    assert results[1]["activity_level"] == "high"


def test_malformed_fields_use_bounded_defaults_and_semantic_text_keeps_source_description():
    metadata = normalize_metadata(
        CANDIDATE,
        {
            "weather_sensitivity": 9,
            "estimated_duration_minutes": -1,
            "activity_level": "extreme",
            "tags": "not-a-list",
            "traveler_summary": "too short",
        },
    )
    document = semantic_document({**CANDIDATE, **metadata})

    assert 0 <= metadata["weather_sensitivity"] <= 1
    assert metadata["estimated_duration_minutes"] >= 30
    assert "A local museum with galleries" in document
    assert "Category:" in document
