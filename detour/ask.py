"""Small, read-only conversational view over persisted trip intelligence."""

from __future__ import annotations

import json
from typing import Any

from detour.destination import AttractionSearchService
from detour.llm import LLMError, LLMService
from detour.models import TripRepository
from detour.resilience import TripResilienceService
from detour.scenarios import normalize_scenario


class AskValidationError(ValueError):
    """Raised when an Ask Detour request is invalid."""


class AskDetourService:
    """Answer grounded questions without creating a second write-capable agent."""

    def __init__(
        self,
        *,
        trips: TripRepository,
        resilience: TripResilienceService,
        search: AttractionSearchService,
        llm: LLMService,
    ):
        self.trips = trips
        self.resilience = resilience
        self.search = search
        self.llm = llm

    def answer(self, trip_id: int, question: str, scenario: str | None = None) -> dict[str, Any]:
        prompt = str(question or "").strip()
        if not prompt or len(prompt) > 500:
            raise AskValidationError("Ask a concise question of 500 characters or fewer.")
        trip = self.trips.get_trip(trip_id)
        if trip is None:
            raise AskValidationError("Trip was not found.")
        normalized_scenario = normalize_scenario(scenario)
        result = self.resilience.evaluate_trip_resilience(trip_id, normalized_scenario)
        itinerary = self.trips.get_itinerary(trip_id)
        alternatives = self.search.search(
            destination_id=int(trip["destination_id"]),
            query=prompt,
            limit=4,
            trip_id=trip_id,
        )
        context = {
            "trip": {
                "destination": trip["destination_name"],
                "dates": [str(trip["start_date"]), str(trip["end_date"])],
                "preferences": trip.get("preferences") or [],
                "pace": trip["pace"],
            },
            "conditions": {
                "scenario": result["scenario"],
                "simulated": result["simulated"],
                "resilience": result["score"],
                "summary": result["summary"],
            },
            "activities": [
                {
                    "title": item["title"],
                    "date": str(item["day_date"]),
                    "time": str(item["start_time"])[:5],
                    "environment": item.get("indoor_outdoor"),
                    "condition_score": evaluation["condition_score"],
                    "status": evaluation["status"],
                    "risk_factors": evaluation["primary_risk_factors"],
                }
                for item, evaluation in zip(itinerary, result["item_evaluations"], strict=True)
            ],
            "retrieved_options": [
                {
                    "name": item["name"],
                    "category": item.get("category"),
                    "environment": item.get("indoor_outdoor"),
                    "summary": item.get("traveler_summary") or item.get("description", "")[:180],
                }
                for item in alternatives
            ],
        }
        message, _ = self.llm.create_message(
            [
                {
                    "role": "system",
                    "content": (
                        "You are Ask Detour, a concise read-only trip guide. Answer only from the supplied "
                        "trip, deterministic condition scores, and retrieved attraction context. Clearly label "
                        "simulations. Never claim to have changed the itinerary. If the traveler asks for a "
                        "change, explain the useful option briefly and direct them to Repair My Trip for an "
                        "explicitly reviewable proposal. Do not reveal hidden reasoning. Use plain text only, "
                        "with no Markdown, and answer in 2-4 short sentences."
                    ),
                },
                {"role": "user", "content": json.dumps({"question": prompt, "context": context})},
            ]
        )
        answer = getattr(message, "content", None)
        if not isinstance(answer, str) or not answer.strip():
            raise LLMError("Ask Detour returned an empty response.")
        return {
            "answer": answer.strip(),
            "scenario": result["scenario"],
            "simulated": result["simulated"],
            "read_only": True,
        }


__all__ = ["AskDetourService", "AskValidationError"]
