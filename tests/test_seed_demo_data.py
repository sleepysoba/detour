from scripts.seed_demo_data import DEMO_DESTINATIONS, seed_demo_destinations


def test_demo_seed_is_idempotent_at_normal_ingestion_service_boundary():
    cache = {}
    calls = []

    def ingest(destination):
        calls.append(destination)
        cached = destination in cache
        cache.setdefault(destination, [{"id": index} for index in range(10)])
        return {"destination": {"display_name": destination}, "attractions": cache[destination], "cached": cached}

    first = seed_demo_destinations(ingest, output=lambda message: None)
    second = seed_demo_destinations(ingest, output=lambda message: None)

    assert DEMO_DESTINATIONS == (
        "Boulder, Colorado",
        "Austin, Texas",
        "Miami, Florida",
    )
    assert calls == [*DEMO_DESTINATIONS, *DEMO_DESTINATIONS]
    assert all(result["usable_attractions"] == 10 for result in first + second)
    assert all(result["cached"] is False for result in first)
    assert all(result["cached"] is True for result in second)


def test_demo_seed_continues_after_individual_failure():
    def ingest(destination):
        if destination == "Austin, Texas":
            raise RuntimeError("provider unavailable")
        return {"attractions": [{"id": 1}], "cached": True}

    results = seed_demo_destinations(ingest, output=lambda message: None)

    assert [result["ok"] for result in results] == [True, False, True]
