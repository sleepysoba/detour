"""Lazy composition root for Detour's existing service layer."""

from __future__ import annotations

from functools import cached_property
from typing import Any

from detour.agent import RepairAgent
from detour.ask import AskDetourService
from detour.destination import AttractionSearchService, DestinationIngestionService
from detour.embeddings import EmbeddingService
from detour.enrichment import AttractionEnricher
from detour.itinerary import ItineraryService
from detour.llm import LLMService
from detour.models import TripRepository
from detour.repairs import RepairService
from detour.resilience import TripResilienceService
from detour.retrieval import AttractionRepository
from detour.tools import RepairToolbox
from detour.tracing import TraceService
from detour.trips import TripCreationService
from detour.weather import OpenMeteoService
from detour.wikimedia import WikimediaService


class DetourServices:
    """Build shared services only when a route actually needs them."""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    @cached_property
    def database_options(self) -> dict[str, Any]:
        return {
            "database_url": self.config["LAKEBASE_URL"],
            "secret_scope": self.config["LAKEBASE_SECRET_SCOPE"],
            "secret_key": self.config["LAKEBASE_SECRET_KEY"],
            "connect_timeout": self.config["LAKEBASE_CONNECT_TIMEOUT"],
        }

    @cached_property
    def trips(self) -> TripRepository:
        return TripRepository(**self.database_options)

    @cached_property
    def attractions(self) -> AttractionRepository:
        return AttractionRepository(**self.database_options)

    @cached_property
    def traces(self) -> TraceService:
        return TraceService(**self.database_options)

    @cached_property
    def weather(self) -> OpenMeteoService:
        return OpenMeteoService(timeout_seconds=self.config["OPEN_METEO_TIMEOUT_SECONDS"])

    @cached_property
    def wikimedia(self) -> WikimediaService:
        return WikimediaService(
            timeout_seconds=self.config["WIKIMEDIA_TIMEOUT_SECONDS"],
            user_agent=self.config["WIKIMEDIA_USER_AGENT"],
        )

    @cached_property
    def embeddings(self) -> EmbeddingService:
        return EmbeddingService(
            model_name=self.config["EMBEDDING_MODEL"],
            dimensions=self.config["EMBEDDING_DIMENSIONS"],
            threads=self.config["EMBEDDING_THREADS"],
        )

    @cached_property
    def llm(self) -> LLMService:
        return LLMService(
            base_url=self.config["DATABRICKS_AI_BASE_URL"],
            token=self.config["DATABRICKS_TOKEN"],
            model=self.config["DATABRICKS_CHAT_MODEL"],
            timeout_seconds=self.config["DATABRICKS_LLM_TIMEOUT_SECONDS"],
        )

    @cached_property
    def search(self) -> AttractionSearchService:
        return AttractionSearchService(
            embeddings=self.embeddings,
            repository=self.attractions,
            traces=self.traces,
        )

    @cached_property
    def destinations(self) -> DestinationIngestionService:
        return DestinationIngestionService(
            weather=self.weather,
            wikimedia=self.wikimedia,
            enricher=AttractionEnricher(self.llm, self.traces),
            embeddings=self.embeddings,
            repository=self.attractions,
            traces=self.traces,
            connection_options=self.database_options,
        )

    @cached_property
    def trip_creator(self) -> TripCreationService:
        return TripCreationService(destinations=self.destinations, repository=self.trips)

    @cached_property
    def itineraries(self) -> ItineraryService:
        return ItineraryService(
            trips=self.trips,
            attractions=self.attractions,
            search=self.search,
            weather=self.weather,
            llm=self.llm,
            traces=self.traces,
        )

    @cached_property
    def resilience(self) -> TripResilienceService:
        return TripResilienceService(self.trips)

    @cached_property
    def repairs(self) -> RepairService:
        return RepairService(
            trips=self.trips,
            attractions=self.attractions,
            resilience=self.resilience,
            traces=self.traces,
            connection_options=self.database_options,
        )

    @cached_property
    def toolbox(self) -> RepairToolbox:
        return RepairToolbox(
            trips=self.trips,
            attractions=self.attractions,
            search=self.search,
            resilience=self.resilience,
            repairs=self.repairs,
        )

    @cached_property
    def agent(self) -> RepairAgent:
        return RepairAgent(llm=self.llm, toolbox=self.toolbox, traces=self.traces)

    @cached_property
    def ask(self) -> AskDetourService:
        return AskDetourService(
            trips=self.trips,
            resilience=self.resilience,
            search=self.search,
            llm=self.llm,
        )


__all__ = ["DetourServices"]
