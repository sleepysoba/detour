from unittest.mock import Mock

import pytest

from detour.wikimedia import WikimediaError, WikimediaService


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
