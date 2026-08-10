"""Batched Llama classification for Wikimedia-sourced attractions."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from detour.llm import LLMService
from detour.tracing import TraceService

logger = logging.getLogger(__name__)

VALID_ENVIRONMENTS = {"indoor", "outdoor", "mixed"}
VALID_ACTIVITY_LEVELS = {"low", "moderate", "high"}
MAX_TAGS = 8


class EnrichmentError(ValueError):
    """Raised when a structured enrichment payload has no usable shape."""


def _clean_tags(value: Any, defaults: list[str]) -> list[str]:
    if not isinstance(value, list):
        return defaults
    tags: list[str] = []
    for raw_tag in value:
        if not isinstance(raw_tag, str):
            continue
        tag = re.sub(r"[^a-z0-9 -]", "", raw_tag.casefold()).strip().replace(" ", "-")
        if tag and len(tag) <= 40 and tag not in tags:
            tags.append(tag)
    return tags[:MAX_TAGS] or defaults


def _first_sentence(description: str, *, limit: int = 240) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", description.strip(), maxsplit=1)[0]
    if len(sentence) <= limit:
        return sentence
    return sentence[: limit - 1].rstrip() + "…"


def deterministic_metadata(candidate: dict) -> dict:
    """Return conservative useful metadata when Llama output is unavailable."""
    name = str(candidate.get("name") or candidate.get("title") or "Attraction").strip()
    description = str(candidate.get("description") or "").strip()
    text = f"{name} {description}".casefold()

    category = "cultural_site"
    environment = "mixed"
    sensitivity = 0.5
    activity_level = "moderate"
    duration = 90
    tags = ["culture", "sightseeing"]

    if any(term in text for term in ("museum", "gallery", "library", "observatory")):
        category, environment, sensitivity = "museum", "indoor", 0.15
        activity_level, duration, tags = "low", 120, ["culture", "indoor", "rainy-day"]
    elif any(term in text for term in ("theater", "theatre", "auditorium", "performing arts")):
        category, environment, sensitivity = "performing_arts", "indoor", 0.2
        activity_level, duration, tags = "low", 120, ["culture", "indoor", "arts"]
    elif any(term in text for term in ("trail", "hiking", "mountain", "canyon")):
        category, environment, sensitivity = "trail", "outdoor", 0.95
        activity_level, duration, tags = "high", 180, ["outdoors", "walking", "scenic"]
    elif any(term in text for term in ("park", "garden", "arboretum", "beach", "creek")):
        category, environment, sensitivity = "park", "outdoor", 0.8
        activity_level, duration, tags = "moderate", 120, ["outdoors", "walking", "photography"]
    elif any(term in text for term in ("market", "mall")):
        category, environment, sensitivity = "market", "mixed", 0.4
        activity_level, duration, tags = "low", 90, ["shopping", "walking", "relaxed"]
    elif any(term in text for term in ("neighborhood", "district", "street")):
        category, environment, sensitivity = "neighborhood", "mixed", 0.6
        activity_level, duration, tags = "moderate", 120, ["walking", "culture", "photography"]
    elif any(term in text for term in ("historic", "landmark", "building", "memorial", "site")):
        category, environment, sensitivity = "landmark", "mixed", 0.55
        activity_level, duration, tags = "low", 75, ["history", "architecture", "sightseeing"]

    return {
        "category": category,
        "indoor_outdoor": environment,
        "weather_sensitivity": sensitivity,
        "activity_level": activity_level,
        "estimated_duration_minutes": duration,
        "tags": tags,
        "traveler_summary": _first_sentence(description) or f"Explore {name}.",
        "enrichment_source": "deterministic_fallback",
    }


def normalize_metadata(candidate: dict, raw: Any) -> dict:
    """Validate one Llama classification, falling back field-by-field."""
    defaults = deterministic_metadata(candidate)
    if not isinstance(raw, dict):
        return defaults

    category = raw.get("category")
    if not isinstance(category, str) or not re.fullmatch(r"[a-z][a-z0-9_ -]{1,39}", category.strip().casefold()):
        category = defaults["category"]
    else:
        category = category.strip().casefold().replace(" ", "_").replace("-", "_")

    environment = raw.get("indoor_outdoor")
    if environment not in VALID_ENVIRONMENTS:
        environment = defaults["indoor_outdoor"]

    try:
        sensitivity = float(raw.get("weather_sensitivity"))
        if not 0 <= sensitivity <= 1:
            raise ValueError
    except (TypeError, ValueError):
        sensitivity = defaults["weather_sensitivity"]

    activity_level = raw.get("activity_level")
    if activity_level not in VALID_ACTIVITY_LEVELS:
        activity_level = defaults["activity_level"]

    try:
        duration = int(raw.get("estimated_duration_minutes"))
        if not 30 <= duration <= 480:
            raise ValueError
    except (TypeError, ValueError):
        duration = defaults["estimated_duration_minutes"]

    summary = raw.get("traveler_summary")
    if not isinstance(summary, str) or not 20 <= len(summary.strip()) <= 320:
        summary = defaults["traveler_summary"]
    else:
        summary = summary.strip()

    return {
        "category": category,
        "indoor_outdoor": environment,
        "weather_sensitivity": round(sensitivity, 2),
        "activity_level": activity_level,
        "estimated_duration_minutes": duration,
        "tags": _clean_tags(raw.get("tags"), defaults["tags"]),
        "traveler_summary": summary,
        "enrichment_source": "llama",
    }


def parse_enrichment_payload(content: str, candidates: list[dict]) -> list[dict]:
    """Map strict JSON classifications to existing Wikimedia page IDs only."""
    if not isinstance(content, str) or not content.strip():
        raise EnrichmentError("Enrichment response was empty.")
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise EnrichmentError("Enrichment response was not valid JSON.") from exc
    rows = payload.get("attractions") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise EnrichmentError("Enrichment response must contain an attractions list.")

    by_page_id = {
        str(row.get("page_id")): row
        for row in rows
        if isinstance(row, dict) and row.get("page_id") is not None
    }
    return [
        {**candidate, **normalize_metadata(candidate, by_page_id.get(str(candidate["page_id"])))}
        for candidate in candidates
    ]


class AttractionEnricher:
    """Enrich a city in one batched model call with deterministic degradation."""

    def __init__(self, llm: LLMService, traces: TraceService | None = None):
        self.llm = llm
        self.traces = traces

    def _record(self, *, trace_id: str | None, **event: Any) -> None:
        if trace_id and self.traces:
            self.traces.record_safe(trace_id=trace_id, model=self.llm.model, **event)

    def enrich(self, candidates: list[dict], *, trace_id: str | None = None) -> list[dict]:
        if not candidates:
            return []
        compact = [
            {
                "page_id": candidate["page_id"],
                "name": candidate["name"],
                "description": candidate["description"][:1200],
            }
            for candidate in candidates
        ]
        messages = [
            {
                "role": "system",
                "content": (
                    "Classify only the supplied Wikimedia attractions. Never add, rename, or remove an "
                    "attraction. Return JSON only as {\"attractions\":[...]}. Each row must preserve "
                    "page_id and contain category, indoor_outdoor (indoor|outdoor|mixed), "
                    "weather_sensitivity (0..1), activity_level (low|moderate|high), "
                    "estimated_duration_minutes (30..480), tags (short string array), and a factual "
                    "traveler_summary grounded only in the supplied text."
                ),
            },
            {"role": "user", "content": json.dumps({"attractions": compact}, ensure_ascii=False)},
        ]
        self._record(
            trace_id=trace_id,
            event_type="MODEL_REQUEST",
            status="started",
            input_summary={"operation": "attraction_enrichment", "attraction_count": len(candidates)},
        )
        try:
            message, duration_ms = self.llm.create_message(messages)
            content = getattr(message, "content", None)
            enriched = parse_enrichment_payload(content, candidates)
            llama_count = sum(item["enrichment_source"] == "llama" for item in enriched)
            self._record(
                trace_id=trace_id,
                event_type="MODEL_RESPONSE",
                status="ok",
                duration_ms=duration_ms,
                output_summary={"classified_count": llama_count, "fallback_count": len(enriched) - llama_count},
            )
            return enriched
        except Exception as exc:
            logger.warning("attraction_enrichment_fallback error_type=%s count=%d", type(exc).__name__, len(candidates))
            self._record(
                trace_id=trace_id,
                event_type="MODEL_RESPONSE",
                status="error",
                output_summary={"error_type": type(exc).__name__, "fallback_count": len(candidates)},
            )
            return [{**candidate, **deterministic_metadata(candidate)} for candidate in candidates]


def semantic_document(attraction: dict) -> str:
    """Compose grounded retrieval text while retaining the Wikimedia description."""
    tags = ", ".join(attraction.get("tags") or [])
    return (
        f"{attraction['name']}. {attraction['description']} "
        f"Traveler summary: {attraction.get('traveler_summary') or ''} "
        f"Category: {attraction.get('category') or 'attraction'}. "
        f"Setting: {attraction.get('indoor_outdoor') or 'mixed'}. "
        f"Activity level: {attraction.get('activity_level') or 'moderate'}. Tags: {tags}."
    )
