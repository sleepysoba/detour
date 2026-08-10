from unittest.mock import Mock

from detour.destination import AttractionSearchService, DestinationIngestionService, city_key_from_location


LOCATION = {
    "requested_name": "Boulder, Colorado",
    "display_name": "Boulder, Colorado, United States",
    "latitude": 40.015,
    "longitude": -105.2705,
    "timezone": "America/Denver",
    "country_code": "US",
}


def test_city_key_uses_canonical_geocoder_fields():
    assert city_key_from_location(LOCATION) == "boulder-colorado-united-states-us"


def test_second_destination_ingestion_reuses_sufficient_cached_attractions():
    weather = Mock()
    weather.resolve_city.return_value = LOCATION
    repository = Mock()
    repository.connection_options = {}
    repository.upsert_destination.return_value = 7
    repository.count_usable_attractions.return_value = 18
    repository.list_attractions.return_value = [
        {
            "id": index,
            "name": f"Museum {index}",
            "description": "A public museum and cultural visitor attraction.",
        }
        for index in range(18)
    ]
    wikimedia = Mock()
    enricher = Mock()
    embeddings = Mock()
    service = DestinationIngestionService(
        weather=weather,
        wikimedia=wikimedia,
        enricher=enricher,
        embeddings=embeddings,
        repository=repository,
        cache_min_attractions=12,
    )

    first = service.ingest("Boulder, Colorado")
    second = service.ingest("Boulder, Colorado")

    assert first["cached"] is True
    assert second["destination"]["id"] == 7
    assert repository.upsert_destination.call_count == 2
    wikimedia.discover_attractions.assert_not_called()
    enricher.enrich.assert_not_called()
    embeddings.embed_batch.assert_not_called()


def test_semantic_search_reranks_clear_relaxed_activity_metadata():
    embeddings = Mock()
    embeddings.embed.return_value = [0.0] * 384
    repository = Mock()
    repository.semantic_search.return_value = [
        {
            "id": 1,
            "name": "Steep Mountain Trail",
            "similarity": 0.40,
            "indoor_outdoor": "outdoor",
            "activity_level": "high",
            "category": "trail",
            "tags": ["hiking"],
        },
        {
            "id": 2,
            "name": "Local History Museum",
            "similarity": 0.34,
            "indoor_outdoor": "indoor",
            "activity_level": "low",
            "category": "museum",
            "tags": ["culture", "relaxed"],
        },
    ]

    results = AttractionSearchService(
        embeddings=embeddings, repository=repository
    ).search(
        destination_id=7,
        query="relaxed attraction that does not require strenuous activity",
        limit=2,
    )

    assert [row["id"] for row in results] == [2, 1]
    assert repository.semantic_search.call_args.kwargs["destination_id"] == 7
