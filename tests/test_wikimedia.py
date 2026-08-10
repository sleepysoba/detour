from unittest.mock import Mock

import pytest

from detour.wikimedia import (
    WikimediaError,
    WikimediaService,
    attraction_quality_score,
    filter_attraction_candidates,
)


def response(payload):
    result = Mock()
    result.json.return_value = payload
    result.raise_for_status.return_value = None
    return result


def test_discovery_returns_ranked_attributable_places():
    session = Mock()
    session.headers = {}
    session.get.side_effect = [
        response(
            {
                "query": {
                    "geosearch": [
                        {"pageid": 1, "title": "Local Office", "lat": 40.0, "lon": -105.2, "dist": 10},
                        {"pageid": 2, "title": "Boulder Art Museum", "lat": 40.1, "lon": -105.3, "dist": 500},
                        {"pageid": 3, "title": "List of Boulder people", "lat": 40.0, "lon": -105.2, "dist": 20},
                    ]
                }
            }
        ),
        response({"query": {"categorymembers": []}}),
        response({"query": {"categorymembers": []}}),
        response({"query": {"search": []}}),
        response(
            {
                "query": {
                    "pages": [
                        {
                            "pageid": 1,
                            "title": "Local Office",
                            "extract": "A municipal office building in central Boulder with a long local history and public services.",
                            "fullurl": "https://en.wikipedia.org/wiki/Local_Office",
                        },
                        {
                            "pageid": 2,
                            "title": "Boulder Art Museum",
                            "extract": "The Boulder Art Museum is a museum and gallery with exhibitions of modern art and regional culture.",
                            "fullurl": "https://en.wikipedia.org/wiki/Boulder_Art_Museum",
                        },
                        {
                            "pageid": 3,
                            "title": "List of Boulder people",
                            "extract": "This is a long list of people associated with Boulder and the surrounding region.",
                            "fullurl": "https://en.wikipedia.org/wiki/List_of_Boulder_people",
                        },
                    ]
                }
            }
        ),
    ]

    results = WikimediaService(session=session).discover_attractions(
        city="Boulder", latitude=40.0, longitude=-105.2, limit=2
    )

    assert [item["name"] for item in results] == ["Boulder Art Museum", "Local Office"]
    assert results[0]["page_id"] == "2"
    assert results[0]["source_url"].startswith("https://en.wikipedia.org/")
    assert "User-Agent" in session.headers


def test_discovery_rejects_malformed_search_response():
    session = Mock()
    session.headers = {}
    session.get.return_value = response({"query": {"geosearch": "not-a-list"}})

    with pytest.raises(WikimediaError) as error:
        WikimediaService(session=session).discover_attractions(
            city="Boulder", latitude=40.0, longitude=-105.2
        )

    assert error.value.code == "MALFORMED_RESPONSE"


def test_destination_filter_removes_transit_and_event_noise():
    candidates = [
        {
            "name": "Museum of Boulder",
            "description": "A public museum and cultural attraction in central Boulder.",
            "distance_m": 500,
        },
        {
            "name": "Bayfront Park station",
            "description": "A transit station in Miami.",
            "distance_m": 100,
        },
        {
            "name": "Boulder Running Festival",
            "description": "An annual race event.",
            "distance_m": 200,
        },
    ]

    assert [row["name"] for row in filter_attraction_candidates(candidates)] == [
        "Museum of Boulder"
    ]


@pytest.mark.parametrize(
    ("name", "description"),
    [
        (
            "United States District Court",
            "The United States district court has territorial jurisdiction over the region.",
        ),
        (
            "Commerce Center",
            "Commerce Center is an office skyscraper with commercial tenants downtown.",
        ),
        (
            "Atmospheric Research Center",
            "It is a federally funded research and development center with specialized laboratories.",
        ),
        (
            "Heritage Art Museum",
            "The historic museum closed forever in 2017 and no longer receives visitors.",
        ),
        (
            "J & S Building",
            "The building is a historic site on the National Register but was later demolished.",
        ),
    ],
)
def test_destination_filter_excludes_clear_non_visitor_or_defunct_facilities(name, description):
    assert attraction_quality_score({"name": name, "description": description}) < 1


def test_destination_filter_preserves_clear_visitor_places_despite_borderline_words():
    candidates = [
        {
            "name": "Vizcaya Museum and Gardens",
            "description": "A museum and garden in the former villa of a local businessman.",
        },
        {
            "name": "Historic Courthouse Museum",
            "description": (
                "A historic site on the National Register of Historic Places that now operates "
                "as a public museum and visitor center."
            ),
        },
        {
            "name": "University Observatory",
            "description": "An astronomical observatory with public visitor programs.",
        },
        {
            "name": "Downtown Historic District",
            "description": "A nationally recognized historic district known for notable architecture.",
        },
    ]

    assert {row["name"] for row in filter_attraction_candidates(candidates)} == {
        row["name"] for row in candidates
    }


@pytest.mark.parametrize("closure_text", ["now closed", "closed permanently"])
def test_destination_filter_excludes_obvious_permanent_closure_text(closure_text):
    candidate = {
        "name": "Harbor Art Museum",
        "description": f"A public museum and visitor attraction that is {closure_text}.",
    }

    assert filter_attraction_candidates([candidate]) == []
