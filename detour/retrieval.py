"""Lakebase attraction persistence and destination-scoped pgvector retrieval."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from psycopg2.extras import Json

from detour.db import LakebaseError, get_connection
from detour.embeddings import DEFAULT_DIMENSIONS, vector_literal


class AttractionRepository:
    """Small repository used by Phase 1 diagnostics and later retrieval flows."""

    def __init__(self, **connection_options: Any):
        self.connection_options = connection_options

    @contextmanager
    def _connection(self, connection: Any | None) -> Iterator[tuple[Any, bool]]:
        if connection is not None:
            yield connection, False
            return
        with get_connection(**self.connection_options) as owned_connection:
            yield owned_connection, True

    def upsert_destination(
        self,
        *,
        city_key: str,
        requested_name: str,
        display_name: str,
        latitude: float,
        longitude: float,
        timezone: str,
        connection: Any | None = None,
    ) -> int:
        """Upsert destination metadata and return its identifier."""
        sql = """
            INSERT INTO destinations (
                city_key, requested_name, display_name, latitude, longitude, timezone
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (city_key) DO UPDATE SET
                requested_name = EXCLUDED.requested_name,
                display_name = EXCLUDED.display_name,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                timezone = EXCLUDED.timezone
            RETURNING id
        """
        try:
            with self._connection(connection) as (active_connection, owns_connection):
                with active_connection.cursor() as cursor:
                    cursor.execute(
                        sql,
                        (city_key, requested_name, display_name, latitude, longitude, timezone),
                    )
                    row = cursor.fetchone()
                if owns_connection:
                    active_connection.commit()
        except LakebaseError:
            raise
        except Exception as exc:
            raise LakebaseError("Could not upsert the destination.") from exc
        if not row or row.get("id") is None:
            raise LakebaseError("Destination upsert did not return an identifier.")
        return int(row["id"])

    def get_destination(self, destination_id: int) -> dict | None:
        """Return one destination without exposing database-specific row objects."""
        sql = """
            SELECT id, city_key, requested_name, display_name, latitude, longitude,
                   timezone, last_ingested_at, created_at
            FROM destinations
            WHERE id = %s
        """
        try:
            with self._connection(None) as (connection, _):
                with connection.cursor() as cursor:
                    cursor.execute(sql, (destination_id,))
                    row = cursor.fetchone()
        except LakebaseError:
            raise
        except Exception as exc:
            raise LakebaseError("Could not load the destination.") from exc
        return dict(row) if row else None

    def count_usable_attractions(self, destination_id: int) -> int:
        """Count attributable, embedded attractions available for retrieval."""
        sql = """
            SELECT COUNT(*) AS attraction_count
            FROM attractions
            WHERE destination_id = %s
              AND source_page_id IS NOT NULL
              AND description <> ''
              AND embedding IS NOT NULL
        """
        try:
            with self._connection(None) as (connection, _):
                with connection.cursor() as cursor:
                    cursor.execute(sql, (destination_id,))
                    row = cursor.fetchone()
        except LakebaseError:
            raise
        except Exception as exc:
            raise LakebaseError("Could not count destination attractions.") from exc
        return int(row["attraction_count"]) if row else 0

    def list_attractions(self, destination_id: int, *, limit: int = 30) -> list[dict]:
        """Return compact stored attraction records for orchestration and diagnostics."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        sql = """
            SELECT id, destination_id, source_page_id, name,
                   LEFT(description, 1200) AS description, source_url,
                   latitude, longitude, category, indoor_outdoor,
                   weather_sensitivity, activity_level,
                   estimated_duration_minutes, tags, traveler_summary,
                   embedding_model
            FROM attractions
            WHERE destination_id = %s
            ORDER BY name, id
            LIMIT %s
        """
        try:
            with self._connection(None) as (connection, _):
                with connection.cursor() as cursor:
                    cursor.execute(sql, (destination_id, limit))
                    rows = cursor.fetchall()
        except LakebaseError:
            raise
        except Exception as exc:
            raise LakebaseError("Could not load destination attractions.") from exc
        return [_compact_attraction(row) for row in rows]

    def mark_destination_ingested(self, destination_id: int, *, connection: Any | None = None) -> None:
        """Record a successful ingestion only after attraction persistence succeeds."""
        try:
            with self._connection(connection) as (active_connection, owns_connection):
                with active_connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE destinations SET last_ingested_at = NOW() WHERE id = %s",
                        (destination_id,),
                    )
                    if cursor.rowcount != 1:
                        raise LakebaseError("Destination ingestion update matched no row.")
                if owns_connection:
                    active_connection.commit()
        except LakebaseError:
            raise
        except Exception as exc:
            raise LakebaseError("Could not update destination ingestion state.") from exc

    def upsert_attraction(
        self,
        *,
        destination_id: int,
        source_page_id: str,
        name: str,
        description: str,
        source_url: str,
        latitude: float | None,
        longitude: float | None,
        category: str | None,
        indoor_outdoor: str | None,
        weather_sensitivity: float | None,
        activity_level: str | None,
        estimated_duration_minutes: int | None,
        tags: list[str],
        traveler_summary: str | None,
        embedding: list[float],
        embedding_model: str,
        connection: Any | None = None,
    ) -> int:
        """Upsert one attributable attraction with its validated embedding."""
        embedding_value = vector_literal(embedding, dimensions=DEFAULT_DIMENSIONS)
        sql = """
            INSERT INTO attractions (
                destination_id, source_page_id, name, description, source_url,
                latitude, longitude, category, indoor_outdoor, weather_sensitivity,
                activity_level, estimated_duration_minutes, tags, traveler_summary,
                embedding, embedding_model
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s::vector, %s
            )
            ON CONFLICT (destination_id, source_page_id) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                source_url = EXCLUDED.source_url,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                category = EXCLUDED.category,
                indoor_outdoor = EXCLUDED.indoor_outdoor,
                weather_sensitivity = EXCLUDED.weather_sensitivity,
                activity_level = EXCLUDED.activity_level,
                estimated_duration_minutes = EXCLUDED.estimated_duration_minutes,
                tags = EXCLUDED.tags,
                traveler_summary = EXCLUDED.traveler_summary,
                embedding = EXCLUDED.embedding,
                embedding_model = EXCLUDED.embedding_model
            RETURNING id
        """
        params = (
            destination_id,
            source_page_id,
            name,
            description,
            source_url,
            latitude,
            longitude,
            category,
            indoor_outdoor,
            weather_sensitivity,
            activity_level,
            estimated_duration_minutes,
            Json(tags),
            traveler_summary,
            embedding_value,
            embedding_model,
        )
        try:
            with self._connection(connection) as (active_connection, owns_connection):
                with active_connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    row = cursor.fetchone()
                if owns_connection:
                    active_connection.commit()
        except LakebaseError:
            raise
        except Exception as exc:
            raise LakebaseError("Could not upsert the attraction.") from exc
        if not row or row.get("id") is None:
            raise LakebaseError("Attraction upsert did not return an identifier.")
        return int(row["id"])

    def semantic_search(
        self,
        *,
        destination_id: int,
        query_embedding: list[float],
        limit: int = 8,
        connection: Any | None = None,
    ) -> list[dict]:
        """Cosine-rank compact attraction evidence within one destination."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
            raise ValueError("limit must be an integer from 1 to 30.")
        query_vector = vector_literal(query_embedding, dimensions=DEFAULT_DIMENSIONS)
        sql = """
            SELECT
                id,
                name,
                LEFT(description, 600) AS description,
                source_url,
                category,
                indoor_outdoor,
                weather_sensitivity,
                activity_level,
                estimated_duration_minutes,
                tags,
                traveler_summary,
                1 - (embedding <=> %s::vector) AS similarity
            FROM attractions
            WHERE destination_id = %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        params = (query_vector, destination_id, query_vector, limit)
        try:
            with self._connection(connection) as (active_connection, _):
                with active_connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    rows = cursor.fetchall()
        except LakebaseError:
            raise
        except Exception as exc:
            raise LakebaseError("Attraction semantic search failed.") from exc

        results: list[dict] = []
        for row in rows:
            result = _compact_attraction(row)
            result["similarity"] = float(row["similarity"])
            results.append(result)
        return results


def _compact_attraction(row: dict) -> dict:
    """Normalize repository rows into safe, JSON-serializable evidence objects."""
    result = {
        "id": int(row["id"]),
        "name": row["name"],
        "description": row["description"],
        "source_url": row.get("source_url"),
        "category": row.get("category"),
        "indoor_outdoor": row.get("indoor_outdoor"),
        "tags": list(row.get("tags") or []),
    }
    if "weather_sensitivity" in row:
        result["weather_sensitivity"] = (
            float(row["weather_sensitivity"])
            if row.get("weather_sensitivity") is not None
            else None
        )
    for optional_key in (
        "destination_id",
        "source_page_id",
        "latitude",
        "longitude",
        "activity_level",
        "estimated_duration_minutes",
        "traveler_summary",
        "embedding_model",
    ):
        if optional_key in row:
            result[optional_key] = row.get(optional_key)
    return result
