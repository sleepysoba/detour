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
        estimated_duration_minutes: int | None,
        tags: list[str],
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
                estimated_duration_minutes, tags, embedding, embedding_model
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
            ON CONFLICT (destination_id, source_page_id) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                source_url = EXCLUDED.source_url,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                category = EXCLUDED.category,
                indoor_outdoor = EXCLUDED.indoor_outdoor,
                weather_sensitivity = EXCLUDED.weather_sensitivity,
                estimated_duration_minutes = EXCLUDED.estimated_duration_minutes,
                tags = EXCLUDED.tags,
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
            estimated_duration_minutes,
            Json(tags),
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
                tags,
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
            results.append(
                {
                    "id": int(row["id"]),
                    "name": row["name"],
                    "description": row["description"],
                    "source_url": row.get("source_url"),
                    "category": row.get("category"),
                    "indoor_outdoor": row.get("indoor_outdoor"),
                    "tags": list(row.get("tags") or []),
                    "similarity": float(row["similarity"]),
                }
            )
        return results
