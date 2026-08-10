"""Exercise Detour's real Phase 2 destination-to-itinerary data flow."""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from detour.config import configure_logging, load_config
from detour.db import init_schema, run_query
from detour.destination import AttractionSearchService, DestinationIngestionService
from detour.embeddings import EmbeddingService
from detour.enrichment import AttractionEnricher
from detour.itinerary import ItineraryService
from detour.llm import LLMService
from detour.models import TripRepository
from detour.retrieval import AttractionRepository
from detour.tracing import TraceService
from detour.trips import TripCreationService
from detour.weather import OpenMeteoService
from detour.wikimedia import WikimediaService

SEMANTIC_QUERIES = (
    "scenic outdoor place for photography and walking",
    "quiet indoor cultural activity if it is raining",
    "relaxed attraction that does not require strenuous activity",
)


class SmokeRunner:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, label: str, operation: Callable[[], Any]) -> Any:
        try:
            result = operation()
            detail = result if isinstance(result, str) else None
            print(f"[PASS] {label}{': ' + detail if detail else ''}")
            return result
        except Exception as exc:
            print(f"[FAIL] {label}: {str(exc) or type(exc).__name__}")
            self.failures.append(label)
            return None


def _database_options(config: dict) -> dict:
    return {
        "database_url": config["LAKEBASE_URL"],
        "secret_scope": config["LAKEBASE_SECRET_SCOPE"],
        "secret_key": config["LAKEBASE_SECRET_KEY"],
        "connect_timeout": config["LAKEBASE_CONNECT_TIMEOUT"],
    }


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    config = load_config()
    configure_logging(config["LOG_LEVEL"])
    database_options = _database_options(config)
    runner = SmokeRunner()

    runner.check("Phase 2 schema initialized", lambda: init_schema(**database_options))

    weather = OpenMeteoService(timeout_seconds=config["OPEN_METEO_TIMEOUT_SECONDS"])
    wikimedia = WikimediaService(
        timeout_seconds=config["WIKIMEDIA_TIMEOUT_SECONDS"],
        user_agent=config["WIKIMEDIA_USER_AGENT"],
    )
    embeddings = EmbeddingService(
        model_name=config["EMBEDDING_MODEL"],
        dimensions=config["EMBEDDING_DIMENSIONS"],
        threads=config["EMBEDDING_THREADS"],
    )
    llm = LLMService(
        base_url=config["DATABRICKS_AI_BASE_URL"],
        token=config["DATABRICKS_TOKEN"],
        model=config["DATABRICKS_CHAT_MODEL"],
        timeout_seconds=config["DATABRICKS_LLM_TIMEOUT_SECONDS"],
    )
    attraction_repository = AttractionRepository(**database_options)
    trip_repository = TripRepository(**database_options)
    traces = TraceService(**database_options)
    enricher = AttractionEnricher(llm, traces)
    destinations = DestinationIngestionService(
        weather=weather,
        wikimedia=wikimedia,
        enricher=enricher,
        embeddings=embeddings,
        repository=attraction_repository,
        traces=traces,
        connection_options=database_options,
    )
    search = AttractionSearchService(
        embeddings=embeddings,
        repository=attraction_repository,
        traces=traces,
    )
    trips = TripCreationService(destinations=destinations, repository=trip_repository)
    itineraries = ItineraryService(
        trips=trip_repository,
        attractions=attraction_repository,
        search=search,
        weather=weather,
        llm=llm,
        traces=traces,
    )

    boulder: dict[str, Any] = {}

    def ingest_boulder() -> str:
        result = destinations.ingest("Boulder, Colorado")
        boulder.update(result)
        count = len(result["attractions"])
        if count < 10:
            raise RuntimeError(f"Only {count} usable Boulder attractions were stored; expected at least 10.")
        return f"destination {result['destination']['id']} with {count} usable attractions"

    runner.check("Boulder destination upserted and attractions ready", ingest_boulder)
    if boulder:
        print("\nAttraction sample:")
        for attraction in boulder["attractions"][: max(8, min(12, len(boulder["attractions"])) )]:
            print(f"  - {attraction['name']}")

        print("\nSemantic retrieval:")
        for query in SEMANTIC_QUERIES:
            print(f'\nSemantic query: "{query}"')
            results = runner.check(
                f"retrieval returned results for query {query[:24]}",
                lambda query=query: search.search(
                    destination_id=boulder["destination"]["id"], query=query, limit=3
                ),
            )
            if isinstance(results, list):
                for index, result in enumerate(results, start=1):
                    print(f"  {index}. {result['name']} ({result['similarity']:.3f})")

    generated: dict[str, Any] = {}
    if boulder:
        start_date = date.today() + timedelta(days=1)
        end_date = start_date + timedelta(days=1)

        def create_and_generate() -> str:
            created = trips.create(
                destination="Boulder, Colorado",
                start_date=start_date,
                end_date=end_date,
                preferences=["outdoors", "culture", "photography", "relaxed"],
                pace="balanced",
            )
            result = itineraries.generate(created["trip"]["id"])
            generated.update(result)
            return f"trip {created['trip']['id']} | {start_date} to {end_date} | balanced"

        runner.check("Boulder trip and initial itinerary persisted", create_and_generate)

    if generated:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for item in generated["items"]:
            grouped[item["day_date"]].append(item)
        print(
            f"\nTrip: {generated['trip']['destination_name']} | "
            f"{generated['trip']['start_date']} to {generated['trip']['end_date']} | "
            f"{generated['trip']['pace']}"
        )
        for day_value, items in sorted(grouped.items()):
            print(f"\nDAY {day_value}")
            for item in items:
                reasons = "; ".join(item["risk_reasons"])
                print(f"{item['start_time']} {item['title']}")
                print(f"       {item['risk_state']} {item['suitability_score']} — {reasons}")

        runner.check(
            "itinerary items reference real stored attractions",
            lambda: (
                "all references valid"
                if trip_repository.itinerary_references_are_valid(generated["trip"]["id"])
                else (_ for _ in ()).throw(RuntimeError("invalid attraction reference found"))
            ),
        )
        unique_ids = {item["attraction_id"] for item in generated["items"]}
        runner.check(
            "itinerary has no duplicate attractions",
            lambda: (
                f"{len(unique_ids)} unique real attractions"
                if len(unique_ids) == len(generated["items"])
                else (_ for _ in ()).throw(RuntimeError("duplicate attraction found"))
            ),
        )

    if boulder:
        before_count = attraction_repository.count_usable_attractions(boulder["destination"]["id"])

        def verify_cache() -> str:
            second = destinations.ingest("Boulder, Colorado")
            after_count = attraction_repository.count_usable_attractions(second["destination"]["id"])
            if not second["cached"]:
                raise RuntimeError("Second ingestion did not use the attraction cache.")
            if after_count != before_count:
                raise RuntimeError(f"Attraction count changed from {before_count} to {after_count}.")
            return (
                f"reused {len(second['attractions'])} quality-qualified attractions; "
                f"raw row count remained {after_count}"
            )

        runner.check("second Boulder ingestion reused cached attractions", verify_cache)

    miami: dict[str, Any] = {}

    def ingest_miami() -> str:
        result = destinations.ingest("Miami, Florida")
        miami.update(result)
        if len(result["attractions"]) < 8:
            raise RuntimeError("Miami produced fewer than 8 usable attractions.")
        return f"destination {result['destination']['id']} with {len(result['attractions'])} usable attractions"

    runner.check("Miami arbitrary-city ingestion", ingest_miami)
    if miami:
        print("\nMiami attraction sample:")
        for attraction in miami["attractions"][:8]:
            print(f"  - {attraction['name']}")

    def verify_database() -> str:
        destination_ids = [
            result["destination"]["id"] for result in (boulder, miami) if result.get("destination")
        ]
        if not destination_ids or not generated:
            raise RuntimeError("Destination or trip prerequisites failed.")
        counts = run_query(
            """
            SELECT
                (SELECT COUNT(*) FROM destinations WHERE id = ANY(%s)) AS destinations,
                (SELECT COUNT(*) FROM attractions WHERE destination_id = ANY(%s)) AS attractions,
                (SELECT COUNT(*) FROM trips WHERE id = %s) AS trips,
                (SELECT COUNT(*) FROM itinerary_items WHERE trip_id = %s) AS itinerary_items
            """,
            (destination_ids, destination_ids, generated["trip"]["id"], generated["trip"]["id"]),
            **database_options,
        )[0]
        if int(counts["destinations"]) != len(destination_ids):
            raise RuntimeError("Expected destinations were not persisted.")
        if int(counts["trips"]) != 1 or int(counts["itinerary_items"]) != len(generated["items"]):
            raise RuntimeError("Trip or itinerary persistence count was incorrect.")
        return (
            f"{counts['destinations']} destinations, {counts['attractions']} attractions, "
            f"{counts['trips']} demo trip, {counts['itinerary_items']} itinerary items"
        )

    runner.check("Lakebase Phase 2 records verified", verify_database)

    if runner.failures:
        print(f"\nPhase 2 smoke failed: {', '.join(runner.failures)}")
        return 1
    print("\nPhase 2 smoke passed: arbitrary-city data, retrieval, trip, and itinerary flow is operational.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
