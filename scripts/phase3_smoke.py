"""Exercise Detour's defining scenario-to-repair workflow against real services."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from detour.agent import RepairAgent
from detour.config import configure_logging, load_config
from detour.db import init_schema, run_query
from detour.destination import AttractionSearchService, DestinationIngestionService
from detour.embeddings import EmbeddingService
from detour.enrichment import AttractionEnricher
from detour.itinerary import ItineraryService
from detour.llm import LLMService
from detour.models import TripRepository
from detour.repairs import MIN_CONDITION_SCORE_IMPROVEMENT, RepairService
from detour.resilience import TripResilienceService
from detour.retrieval import AttractionRepository
from detour.tools import RepairToolbox
from detour.tracing import TraceService
from detour.trips import TripCreationService
from detour.weather import OpenMeteoService
from detour.wikimedia import WikimediaService


def _database_options(config: dict) -> dict:
    return {
        "database_url": config["LAKEBASE_URL"],
        "secret_scope": config["LAKEBASE_SECRET_SCOPE"],
        "secret_key": config["LAKEBASE_SECRET_KEY"],
        "connect_timeout": config["LAKEBASE_CONNECT_TIMEOUT"],
    }


def _fingerprint(items: list[dict[str, Any]]) -> list[tuple]:
    return [
        (
            int(item["id"]),
            int(item["attraction_id"]),
            str(item["day_date"]),
            str(item["start_time"]),
            item["title"],
        )
        for item in items
    ]


def _print_resilience(result: dict[str, Any]) -> None:
    for item in result["item_evaluations"]:
        reasons = "; ".join(item["primary_risk_factors"])
        marker = " *VULNERABLE*" if item["vulnerable"] else ""
        print(
            f"{item['day_date']} {item['start_time']}  {item['title']}  "
            f"{item['condition_score']}/{item['status']} - {reasons}{marker}"
        )


def _print_trace(events: list[dict[str, Any]], trace_id: str) -> None:
    friendly = {
        "AGENT_STARTED": "Agent started",
        "MODEL_REQUEST": "Asked Llama for the next action",
        "MODEL_RESPONSE": "Received Llama response",
        "TOOL_CALLED": "Called tool",
        "TOOL_COMPLETED": "Completed tool",
        "RETRIEVAL_COMPLETED": "Retrieved destination alternatives",
        "REPAIR_PROPOSED": "Repair saved as pending",
        "AGENT_COMPLETED": "Agent completed",
        "REPAIR_APPLY_STARTED": "Repair apply started",
        "REPAIR_ACTION_APPLIED": "Repair action applied",
        "REPAIR_APPLIED": "Repair applied",
    }
    print(f"\nTRACE {trace_id}")
    for event in events:
        detail = friendly.get(event["event_type"], event["event_type"].replace("_", " ").title())
        if event.get("tool_name"):
            detail += f" ({event['tool_name']})"
        validation_summary = (event.get("output_summary") or {}).get("validation_summary")
        if validation_summary:
            detail += f" - {validation_summary}"
        marker = "[OK]" if event["status"] != "error" else "[ERROR]"
        duration = f" {event['duration_ms']}ms" if event.get("duration_ms") is not None else ""
        print(f"{marker} {detail}{duration}")


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    config = load_config()
    configure_logging(config["LOG_LEVEL"])
    database_options = _database_options(config)
    init_schema(**database_options)

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
    attractions = AttractionRepository(**database_options)
    trips = TripRepository(**database_options)
    traces = TraceService(**database_options)
    search = AttractionSearchService(embeddings=embeddings, repository=attractions, traces=traces)
    destinations = DestinationIngestionService(
        weather=weather,
        wikimedia=wikimedia,
        enricher=AttractionEnricher(llm, traces),
        embeddings=embeddings,
        repository=attractions,
        traces=traces,
        connection_options=database_options,
    )
    trip_creator = TripCreationService(destinations=destinations, repository=trips)
    itinerary_service = ItineraryService(
        trips=trips,
        attractions=attractions,
        search=search,
        weather=weather,
        llm=llm,
        traces=traces,
    )
    resilience = TripResilienceService(trips)
    repairs = RepairService(
        trips=trips,
        attractions=attractions,
        resilience=resilience,
        traces=traces,
        connection_options=database_options,
    )
    toolbox = RepairToolbox(
        trips=trips,
        attractions=attractions,
        search=search,
        resilience=resilience,
        repairs=repairs,
    )
    agent = RepairAgent(llm=llm, toolbox=toolbox, traces=traces)

    start_date = date.today() + timedelta(days=1)
    end_date = start_date + timedelta(days=1)
    created = trip_creator.create(
        destination="Boulder, Colorado",
        start_date=start_date,
        end_date=end_date,
        preferences=["outdoors", "culture", "photography", "relaxed"],
        pace="balanced",
    )
    trip_id = int(created["trip"]["id"])
    itinerary_service.generate(trip_id)

    live = resilience.evaluate_trip_resilience(trip_id)
    print("LIVE TRIP")
    print(f"Trip: {trip_id} | {created['trip']['destination_name']}")
    print(f"Resilience: {live['score']}/100 ({live['label']})")
    print(f"Vulnerable activities: {live['vulnerable_activity_count']}")
    _print_resilience(live)

    storm = resilience.evaluate_trip_resilience(trip_id, "RAINSTORM")
    print("\nSCENARIO: RAINSTORM (SIMULATED)")
    print(f"Live resilience: {live['score']}")
    print(f"Simulated resilience: {storm['score']}")
    _print_resilience(storm)
    outdoor_live = {
        item["itinerary_item_id"]: item
        for item in live["item_evaluations"]
        if item["indoor_outdoor"] == "outdoor"
    }
    outdoor_storm = {
        item["itinerary_item_id"]: item
        for item in storm["item_evaluations"]
        if item["indoor_outdoor"] == "outdoor"
    }
    if not outdoor_live:
        raise RuntimeError("Generated Boulder itinerary contained no outdoor activity to stress-test.")
    if not any(
        outdoor_storm[item_id]["condition_score"] <= item["condition_score"] - 20
        for item_id, item in outdoor_live.items()
    ):
        raise RuntimeError("Rainstorm did not materially worsen an outdoor activity.")

    itinerary_before = trips.get_itinerary(trip_id)
    fingerprint_before = _fingerprint(itinerary_before)
    guardrail_analysis = repairs.analyze_repair_options(trip_id=trip_id, scenario="RAINSTORM")
    worst_repairable = next(
        (
            item
            for item in guardrail_analysis["vulnerable"]
            if any(
                option["improvement"] >= MIN_CONDITION_SCORE_IMPROVEMENT
                for option in guardrail_analysis["options_by_item"].get(
                    int(item["itinerary_item_id"]), []
                )
            )
        ),
        None,
    )
    agent_result = agent.run(trip_id, "RAINSTORM")
    proposal = agent_result["proposal"]
    print("\nPROPOSED DETOUR")
    print(f"Resilience: {proposal['resilience_before']} -> {proposal['resilience_projected']}")
    for action in proposal["actions"]:
        before = action["before"]
        after = action["after"]
        print(
            f"- {action['action_type']} {before['title']} "
            f"({before['day_date']} {before['start_time']}) -> {after['title']} "
            f"({after['day_date']} {after['start_time']})"
        )
    print(f"Rationale: {proposal['rationale']}")
    print(f"Deterministic fallback used: {agent_result['used_deterministic_fallback']}")
    print(f"Save attempts: {agent_result['save_attempt_count']}")
    for failure in agent_result["validation_failures"]:
        print(f"Rejected save: {failure}")

    if proposal["status"] != "PENDING":
        raise RuntimeError("Agent did not persist a pending proposal.")
    if _fingerprint(trips.get_itinerary(trip_id)) != fingerprint_before:
        raise RuntimeError("Creating the proposal mutated the itinerary.")
    if agent_result["save_attempt_count"] > 2:
        raise RuntimeError("Agent exceeded the one-correction save limit.")
    if worst_repairable and int(worst_repairable["itinerary_item_id"]) not in {
        int(action["itinerary_item_id"]) for action in proposal["actions"]
    }:
        raise RuntimeError("Proposal did not address the worst repairable vulnerable activity.")
    proposed_ids = [
        int(action["after"]["attraction_id"]) for action in proposal["actions"]
    ]
    for attraction_id in proposed_ids:
        if attractions.get_attraction(
            attraction_id, destination_id=int(created["trip"]["destination_id"])
        ) is None:
            raise RuntimeError(f"Proposal referenced invalid attraction {attraction_id}.")
    all_proposed_ids = {int(item["attraction_id"]) for item in itinerary_before}
    for action in proposal["actions"]:
        all_proposed_ids.discard(int(action["before"]["attraction_id"]))
        if int(action["after"]["attraction_id"]) in all_proposed_ids:
            raise RuntimeError("Proposal would create a duplicate attraction.")
        all_proposed_ids.add(int(action["after"]["attraction_id"]))

    applied = repairs.apply_repair(proposal["repair_id"])
    if applied["repair"]["status"] != "APPLIED":
        raise RuntimeError("Repair was not marked applied.")
    if _fingerprint(applied["itinerary"]) == fingerprint_before:
        raise RuntimeError("Applying the repair did not change the itinerary.")
    applied_by_id = {int(item["id"]): item for item in applied["itinerary"]}
    for action in proposal["actions"]:
        actual_item = applied_by_id[int(action["itinerary_item_id"])]
        expected = action["after"]
        actual_core = (
            int(actual_item["attraction_id"]),
            str(actual_item["day_date"]),
            str(actual_item["start_time"])[:5],
            actual_item["title"],
        )
        expected_core = (
            int(expected["attraction_id"]),
            str(expected["day_date"]),
            str(expected["start_time"])[:5],
            expected["title"],
        )
        if actual_core != expected_core:
            raise RuntimeError("Applied itinerary does not exactly match the proposed action.")
    if _fingerprint(trips.get_itinerary(trip_id)) != _fingerprint(applied["itinerary"]):
        raise RuntimeError("Returned itinerary does not match persisted state.")
    if not trips.itinerary_references_are_valid(trip_id):
        raise RuntimeError("Applied itinerary has an invalid destination attraction reference.")
    resulting_ids = [int(item["attraction_id"]) for item in applied["itinerary"]]
    if len(resulting_ids) != len(set(resulting_ids)):
        raise RuntimeError("Applied itinerary contains duplicate attractions.")
    actual = applied["resilience"]
    if actual["score"] <= storm["score"] and actual["vulnerable_activity_count"] >= storm[
        "vulnerable_activity_count"
    ]:
        raise RuntimeError("Applied repair did not improve simulated resilience or vulnerability.")
    if worst_repairable:
        if actual["vulnerable_activity_count"] >= storm["vulnerable_activity_count"]:
            raise RuntimeError("Repair did not reduce severe vulnerability despite valid alternatives.")
        if actual["score"] - storm["score"] < 5:
            raise RuntimeError("Repair improvement was below 5 points despite valid alternatives.")
    before_scores = {
        int(item["itinerary_item_id"]): item["condition_score"]
        for item in storm["item_evaluations"]
    }
    after_scores = {
        int(item["itinerary_item_id"]): item["condition_score"]
        for item in actual["item_evaluations"]
    }
    for action in proposal["actions"]:
        item_id = int(action["itinerary_item_id"])
        after = action["after"]
        if action["action_type"] == "REPLACE":
            factual_fragments = (
                f"stored {after['indoor_outdoor']} replacement",
                f"scores {after_scores[item_id]} versus {before_scores[item_id]}",
            )
            if any(fragment not in proposal["rationale"] for fragment in factual_fragments):
                raise RuntimeError("Proposal rationale does not match stored metadata and scores.")

    print("\nAPPLIED DETOUR")
    print(f"Actual simulated resilience: {actual['score']}/100 ({actual['label']})")
    _print_resilience(actual)

    for scenario_name in ("HEATWAVE", "POOR_AQI"):
        result = resilience.evaluate_trip_resilience(trip_id, scenario_name)
        print(f"\nSCENARIO: {scenario_name} (SIMULATED)")
        print(
            f"Live -> simulated: {applied['live_resilience']['score']} -> {result['score']} | "
            f"vulnerable activities: {result['vulnerable_activity_count']}"
        )

    persisted = run_query(
        """
        SELECT status, before_resilience, projected_resilience
        FROM repair_runs WHERE id = %s
        """,
        (proposal["repair_id"],),
        **database_options,
    )[0]
    if persisted["status"] != "applied":
        raise RuntimeError("Persisted repair status is not applied.")
    events = traces.get_events(agent_result["trace_id"])
    _print_trace(events, agent_result["trace_id"])
    tool_calls = sum(event["event_type"] == "TOOL_CALLED" for event in events)
    model_calls = sum(event["event_type"] == "MODEL_REQUEST" for event in events)
    total_duration = sum(int(event.get("duration_ms") or 0) for event in events)
    print(
        f"\nTool calls: {tool_calls} | Model calls: {model_calls} | "
        f"Observed duration: {total_duration}ms"
    )
    print(f"Fallback used: {agent_result['used_deterministic_fallback']}")
    print(f"Validation failures: {agent_result['validation_failures'] or 'none'}")
    required = {
        "AGENT_STARTED", "MODEL_REQUEST", "TOOL_CALLED", "TOOL_COMPLETED",
        "RETRIEVAL_COMPLETED", "REPAIR_PROPOSED", "AGENT_COMPLETED",
        "REPAIR_APPLY_STARTED", "REPAIR_ACTION_APPLIED", "REPAIR_APPLIED",
    }
    missing = required - {event["event_type"] for event in events}
    if missing:
        raise RuntimeError(f"Repair trace is missing events: {', '.join(sorted(missing))}")
    print("\nPhase 3 smoke passed: scenario -> agent -> pending proposal -> explicit apply works.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
