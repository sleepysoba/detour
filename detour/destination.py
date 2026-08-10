"""Arbitrary-city destination ingestion and semantic attraction search."""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any
from uuid import uuid4

from detour.db import get_connection
from detour.embeddings import EmbeddingService
from detour.enrichment import AttractionEnricher, semantic_document
from detour.retrieval import AttractionRepository
from detour.tracing import TraceService
from detour.weather import OpenMeteoService
from detour.wikimedia import WikimediaService, filter_attraction_candidates

logger = logging.getLogger(__name__)


class DestinationIngestionError(RuntimeError):
    """Normalized failure for a destination that cannot yield usable attractions."""


def city_key_from_location(location: dict) -> str:
    """Build a stable human-readable key from canonical geocoder fields."""
    parts = [
        str(location.get("display_name") or location.get("requested_name") or "destination"),
        str(location.get("country_code") or ""),
    ]
    value = unicodedata.normalize("NFKD", "-".join(parts)).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not value:
        raise DestinationIngestionError("Could not build a destination cache key.")
    return value[:160]


class DestinationIngestionService:
    """Compose proven geocoding, Wikimedia, Llama, MiniLM, and Lakebase primitives."""

    def __init__(
        self,
        *,
        weather: OpenMeteoService,
        wikimedia: WikimediaService,
        enricher: AttractionEnricher,
        embeddings: EmbeddingService,
        repository: AttractionRepository,
        traces: TraceService | None = None,
        cache_min_attractions: int = 10,
        candidate_limit: int = 25,
        connection_options: dict[str, Any] | None = None,
    ):
        self.weather = weather
        self.wikimedia = wikimedia
        self.enricher = enricher
        self.embeddings = embeddings
        self.repository = repository
        self.traces = traces
        self.cache_min_attractions = cache_min_attractions
        self.candidate_limit = candidate_limit
        self.connection_options = connection_options or repository.connection_options

    def _record(self, *, trace_id: str, **event: Any) -> None:
        if self.traces:
            self.traces.record_safe(trace_id=trace_id, **event)

    def ingest(self, requested_destination: str) -> dict:
        """Resolve and idempotently populate a destination with real attractions."""
        trace_id = str(uuid4())
        destination_name = (requested_destination or "").strip()
        if not destination_name:
            raise DestinationIngestionError("Destination is required.")
        self._record(
            trace_id=trace_id,
            event_type="WORKFLOW_STARTED",
            status="started",
            input_summary={"workflow": "destination_ingestion", "destination": destination_name},
        )
        try:
            location = self.weather.resolve_city(destination_name)
            city_key = city_key_from_location(location)
            destination_id = self.repository.upsert_destination(
                city_key=city_key,
                requested_name=destination_name,
                display_name=location["display_name"],
                latitude=location["latitude"],
                longitude=location["longitude"],
                timezone=location["timezone"],
            )
            existing_count = self.repository.count_usable_attractions(destination_id)
            if existing_count >= self.cache_min_attractions:
                stored = self.repository.list_attractions(destination_id, limit=100)
                attractions = filter_attraction_candidates(stored, limit=100)
                if len(attractions) >= self.cache_min_attractions:
                    logger.info(
                        "destination_ingestion_cache_hit destination_id=%d attraction_count=%d",
                        destination_id,
                        len(attractions),
                    )
                    self._record(
                        trace_id=trace_id,
                        event_type="WORKFLOW_COMPLETED",
                        status="ok",
                        output_summary={"destination_id": destination_id, "cached": True, "attraction_count": len(attractions)},
                    )
                    return {
                        "trace_id": trace_id,
                        "destination": {**location, "id": destination_id, "city_key": city_key},
                        "attractions": attractions,
                        "cached": True,
                        "counts": {
                            "discovered": 0,
                            "filtered": len(attractions),
                            "enriched": 0,
                            "embedded": 0,
                            "persisted": existing_count,
                            "skipped_or_failed": existing_count - len(attractions),
                        },
                    }
                logger.info(
                    "destination_cache_quality_refresh destination_id=%d stored=%d usable=%d",
                    destination_id,
                    existing_count,
                    len(attractions),
                )

            discovered = self.wikimedia.discover_attractions(
                city=location["display_name"],
                latitude=location["latitude"],
                longitude=location["longitude"],
                limit=min(30, max(self.candidate_limit, 25)),
            )
            filtered = filter_attraction_candidates(discovered, limit=self.candidate_limit)
            logger.info(
                "destination_candidates destination_id=%d discovered=%d filtered=%d sample=%s",
                destination_id,
                len(discovered),
                len(filtered),
                [candidate["name"] for candidate in filtered[:8]],
            )
            if len(filtered) < 8:
                raise DestinationIngestionError(
                    f"Only {len(filtered)} usable attractions were found for {location['display_name']}."
                )

            enriched = self.enricher.enrich(filtered, trace_id=trace_id)
            documents = [semantic_document(attraction) for attraction in enriched]
            vectors = self.embeddings.embed_batch(documents)
            persisted = 0
            with get_connection(**self.connection_options) as connection:
                try:
                    for attraction, vector in zip(enriched, vectors, strict=True):
                        self.repository.upsert_attraction(
                            destination_id=destination_id,
                            source_page_id=str(attraction["page_id"]),
                            name=attraction["name"],
                            description=attraction["description"],
                            source_url=attraction["source_url"],
                            latitude=attraction.get("latitude"),
                            longitude=attraction.get("longitude"),
                            category=attraction["category"],
                            indoor_outdoor=attraction["indoor_outdoor"],
                            weather_sensitivity=attraction["weather_sensitivity"],
                            activity_level=attraction["activity_level"],
                            estimated_duration_minutes=attraction["estimated_duration_minutes"],
                            tags=attraction["tags"],
                            traveler_summary=attraction["traveler_summary"],
                            embedding=vector,
                            embedding_model=self.embeddings.model_name,
                            connection=connection,
                        )
                        persisted += 1
                    self.repository.mark_destination_ingested(destination_id, connection=connection)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise

            attractions = filter_attraction_candidates(
                self.repository.list_attractions(destination_id, limit=100), limit=100
            )
            llama_enriched = sum(item.get("enrichment_source") == "llama" for item in enriched)
            counts = {
                "discovered": len(discovered),
                "filtered": len(filtered),
                "enriched": llama_enriched,
                "embedded": len(vectors),
                "persisted": persisted,
                "skipped_or_failed": len(discovered) - len(filtered) + len(enriched) - llama_enriched,
            }
            logger.info(
                "destination_ingestion_completed destination_id=%d discovered=%d filtered=%d "
                "enriched=%d embedded=%d persisted=%d skipped_or_failed=%d",
                destination_id,
                counts["discovered"],
                counts["filtered"],
                counts["enriched"],
                counts["embedded"],
                counts["persisted"],
                counts["skipped_or_failed"],
            )
            self._record(
                trace_id=trace_id,
                event_type="WORKFLOW_COMPLETED",
                status="ok",
                output_summary={"destination_id": destination_id, "cached": False, **counts},
            )
            return {
                "trace_id": trace_id,
                "destination": {**location, "id": destination_id, "city_key": city_key},
                "attractions": attractions,
                "cached": False,
                "counts": counts,
            }
        except Exception as exc:
            self._record(
                trace_id=trace_id,
                event_type="WORKFLOW_ERROR",
                status="error",
                output_summary={"error_type": type(exc).__name__},
            )
            raise


class AttractionSearchService:
    """Embed a natural-language query and run destination-scoped retrieval."""

    def __init__(
        self,
        *,
        embeddings: EmbeddingService,
        repository: AttractionRepository,
        traces: TraceService | None = None,
    ):
        self.embeddings = embeddings
        self.repository = repository
        self.traces = traces

    def search(
        self,
        *,
        destination_id: int,
        query: str,
        limit: int = 8,
        trace_id: str | None = None,
        trip_id: int | None = None,
    ) -> list[dict]:
        normalized_query = (query or "").strip()
        if not normalized_query:
            raise ValueError("A semantic search query is required.")
        retrieval_limit = min(30, max(20, limit * 4))
        results = self.repository.semantic_search(
            destination_id=destination_id,
            query_embedding=self.embeddings.embed(normalized_query),
            limit=retrieval_limit,
        )
        results = filter_attraction_candidates(results, limit=retrieval_limit)
        query_text = normalized_query.casefold()
        query_tags = set(re.findall(r"[a-z]+", query_text))

        def rerank_score(result: dict) -> float:
            score = float(result.get("similarity") or 0)
            environment = result.get("indoor_outdoor")
            activity_level = result.get("activity_level")
            category = str(result.get("category") or "").casefold()
            tags = {str(tag).casefold().replace("-", " ") for tag in result.get("tags") or []}
            if {"indoor", "raining", "rain"} & query_tags:
                score += (
                    0.16
                    if environment == "indoor"
                    else -0.14
                    if environment == "outdoor"
                    else -0.06
                )
            if {"cultural", "culture", "museum"} & query_tags:
                if "museum" in category or "culture" in tags or "cultural" in tags:
                    score += 0.1
            if {"outdoor", "scenic", "photography", "walking"} & query_tags:
                if environment in {"outdoor", "mixed"}:
                    score += 0.06
                if {"photography", "scenic", "walking"} & tags:
                    score += 0.05
            if {"relaxed", "strenuous"} & query_tags:
                if activity_level == "low":
                    score += 0.14
                elif activity_level == "high":
                    score -= 0.14
                if "relaxed" in tags:
                    score += 0.06
            result["retrieval_score"] = round(score, 6)
            return score

        results.sort(key=rerank_score, reverse=True)
        results = results[:limit]
        if trace_id and self.traces:
            self.traces.record_safe(
                trace_id=trace_id,
                trip_id=trip_id,
                event_type="RETRIEVAL_COMPLETED",
                status="ok",
                input_summary={"destination_id": destination_id, "query": normalized_query, "limit": limit},
                output_summary={"result_count": len(results), "top_ids": [row["id"] for row in results[:5]]},
            )
        logger.info(
            "semantic_retrieval destination_id=%d query=%s top_k=%d",
            destination_id,
            normalized_query,
            len(results),
        )
        return results
