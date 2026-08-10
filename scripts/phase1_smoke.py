"""Run real Phase 1 integration checks without starting the Detour UI."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from detour.config import configure_logging, load_config
from detour.db import get_connection
from detour.embeddings import EmbeddingService
from detour.llm import LLMService, run_function_call_diagnostic
from detour.retrieval import AttractionRepository
from detour.tracing import TraceService
from detour.weather import OpenMeteoService
from detour.wikimedia import WikimediaService


class SmokeRunner:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, label: str, operation: Callable[[], str | None]) -> Any:
        try:
            detail = operation()
            suffix = f": {detail}" if detail else ""
            print(f"[PASS] {label}{suffix}")
            return detail if detail is not None else True
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            print(f"[FAIL] {label}: {message}")
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
    runner = SmokeRunner()

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
    database_options = _database_options(config)
    repository = AttractionRepository(**database_options)
    traces = TraceService(**database_options)

    locations: dict[str, dict] = {}

    def geocode(city: str, key: str) -> str:
        location = weather.resolve_city(city)
        locations[key] = location
        return f"{location['display_name']} ({location['latitude']:.4f}, {location['longitude']:.4f})"

    runner.check("Boulder geocoding", lambda: geocode("Boulder, Colorado", "boulder"))

    def forecast(key: str) -> str:
        location = locations[key]
        result = weather.get_forecast(location["latitude"], location["longitude"], forecast_days=5)
        if not result["hourly"] or not result["daily"]:
            raise RuntimeError("Forecast contained no normalized records.")
        return f"{len(result['hourly'])} hourly / {len(result['daily'])} daily records"

    if "boulder" in locations:
        runner.check("Boulder weather", lambda: forecast("boulder"))
    else:
        runner.check("Boulder weather", lambda: (_ for _ in ()).throw(RuntimeError("geocoding prerequisite failed")))

    def air_quality(key: str) -> str:
        location = locations[key]
        result = weather.get_air_quality(location["latitude"], location["longitude"], forecast_days=5)
        usable = [row for row in result["hourly"] if row["us_aqi"] is not None]
        if not usable:
            raise RuntimeError("Air-quality response contained no US AQI values.")
        return f"{len(result['hourly'])} hourly records with US AQI/PM fields"

    if "boulder" in locations:
        runner.check("Boulder air quality", lambda: air_quality("boulder"))
    else:
        runner.check(
            "Boulder air quality",
            lambda: (_ for _ in ()).throw(RuntimeError("geocoding prerequisite failed")),
        )

    runner.check("Miami geocoding", lambda: geocode("Miami, Florida", "miami"))
    if "miami" in locations:
        runner.check("Miami weather", lambda: forecast("miami"))
    else:
        runner.check("Miami weather", lambda: (_ for _ in ()).throw(RuntimeError("geocoding prerequisite failed")))

    def discover_boulder() -> str:
        location = locations["boulder"]
        candidates = wikimedia.discover_attractions(
            city=location["display_name"],
            latitude=location["latitude"],
            longitude=location["longitude"],
            limit=25,
        )
        if len(candidates) < 15:
            raise RuntimeError(f"Only {len(candidates)} usable candidates were returned; expected at least 15.")
        if any(not item["source_url"].startswith("https://") for item in candidates):
            raise RuntimeError("A Wikimedia candidate lacked an attributable source URL.")
        return f"{len(candidates)} candidates"

    if "boulder" in locations:
        runner.check("Wikimedia Boulder discovery", discover_boulder)
    else:
        runner.check(
            "Wikimedia Boulder discovery",
            lambda: (_ for _ in ()).throw(RuntimeError("geocoding prerequisite failed")),
        )

    vectors: dict[str, list[float] | list[list[float]]] = {}

    def embedding_check() -> str:
        single = embeddings.embed("An indoor art museum with modern galleries.")
        batch = embeddings.embed_batch(
            [
                "An indoor art museum with modern galleries.",
                "A scenic mountain hiking trail with expansive views.",
            ]
        )
        if len(single) != 384 or len(batch) != 2 or any(len(vector) != 384 for vector in batch):
            raise RuntimeError("MiniLM did not return the required 384-dimensional vectors.")
        vectors["museum"] = batch[0]
        vectors["trail"] = batch[1]
        vectors["query"] = embeddings.embed("indoor art museum for a rainy afternoon")
        return "model loaded; dimension 384; batch size 2"

    runner.check("MiniLM embeddings", embedding_check)

    def vector_round_trip() -> str:
        if not vectors:
            raise RuntimeError("embedding prerequisite failed")
        with get_connection(**database_options) as connection:
            try:
                destination_id = repository.upsert_destination(
                    city_key="phase1-vector-smoke",
                    requested_name="Phase 1 Vector Smoke",
                    display_name="Phase 1 Vector Smoke",
                    latitude=40.015,
                    longitude=-105.2705,
                    timezone="America/Denver",
                    connection=connection,
                )
                repository.upsert_attraction(
                    destination_id=destination_id,
                    source_page_id="phase1-diagnostic-museum",
                    name="Detour Diagnostic Art Museum",
                    description="An indoor art museum with modern galleries and exhibits for rainy days.",
                    source_url="https://example.invalid/phase1/museum",
                    latitude=40.015,
                    longitude=-105.2705,
                    category="museum",
                    indoor_outdoor="indoor",
                    weather_sensitivity=0.1,
                    activity_level="low",
                    estimated_duration_minutes=120,
                    tags=["art", "culture", "rainy-day"],
                    traveler_summary="A diagnostic indoor art museum.",
                    embedding=vectors["museum"],
                    embedding_model=config["EMBEDDING_MODEL"],
                    connection=connection,
                )
                repository.upsert_attraction(
                    destination_id=destination_id,
                    source_page_id="phase1-diagnostic-trail",
                    name="Detour Diagnostic Mountain Trail",
                    description="A steep outdoor mountain hiking trail with scenic summit views.",
                    source_url="https://example.invalid/phase1/trail",
                    latitude=40.02,
                    longitude=-105.29,
                    category="hiking",
                    indoor_outdoor="outdoor",
                    weather_sensitivity=0.95,
                    activity_level="high",
                    estimated_duration_minutes=180,
                    tags=["hiking", "outdoors", "mountains"],
                    traveler_summary="A diagnostic outdoor mountain trail.",
                    embedding=vectors["trail"],
                    embedding_model=config["EMBEDDING_MODEL"],
                    connection=connection,
                )
                results = repository.semantic_search(
                    destination_id=destination_id,
                    query_embedding=vectors["query"],
                    limit=2,
                    connection=connection,
                )
                if not results or results[0]["name"] != "Detour Diagnostic Art Museum":
                    raise RuntimeError("Cosine search did not rank the relevant controlled attraction first.")
                return f"top result '{results[0]['name']}' (similarity {results[0]['similarity']:.3f})"
            finally:
                connection.rollback()

    runner.check("Lakebase vector round trip", vector_round_trip)

    llm: LLMService | None = None

    def llama_chat() -> str:
        nonlocal llm
        llm = LLMService(
            base_url=config["DATABRICKS_AI_BASE_URL"],
            token=config["DATABRICKS_TOKEN"],
            model=config["DATABRICKS_CHAT_MODEL"],
            timeout_seconds=config["DATABRICKS_LLM_TIMEOUT_SECONDS"],
        )
        response = llm.chat("Reply with a short confirmation that the Detour Phase 1 chat diagnostic works.")
        return f"non-empty response ({len(response)} characters)"

    runner.check("Databricks Llama chat completion", llama_chat)

    diagnostic: dict[str, Any] = {}

    def llama_tool_call() -> str:
        if llm is None:
            raise RuntimeError("chat client prerequisite failed")
        result = run_function_call_diagnostic(llm, traces, city="Boulder, Colorado")
        diagnostic.update(result)
        if result["tool_name"] != "get_demo_weather_summary" or not result["final_response"]:
            raise RuntimeError("Function-calling loop did not complete.")
        return "tool requested, executed, returned to model, and final response received"

    runner.check("Databricks Llama function call", llama_tool_call)

    def trace_check() -> str:
        if not diagnostic:
            raise RuntimeError("function-call prerequisite failed")
        events = traces.get_events(diagnostic["trace_id"])
        event_types = {event["event_type"] for event in events}
        required = {
            "AGENT_STARTED",
            "MODEL_REQUEST",
            "TOOL_CALLED",
            "TOOL_COMPLETED",
            "MODEL_RESPONSE",
            "AGENT_COMPLETED",
        }
        missing = required - event_types
        if missing:
            raise RuntimeError(f"Persisted trace is missing events: {', '.join(sorted(missing))}")
        return f"{len(events)} safe events persisted"

    runner.check("agent_events trace persisted", trace_check)

    if runner.failures:
        print(f"\nPhase 1 smoke failed: {', '.join(runner.failures)}")
        return 1
    print("\nPhase 1 smoke passed: all required integrations are operational.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
