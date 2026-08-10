"""Lakebase repositories for persisted trips, forecasts, and itinerary items."""

from __future__ import annotations

from typing import Any

from psycopg2.extras import Json

from detour.db import LakebaseError, get_connection


class TripRepository:
    """Parameterized persistence for Phase 2 trip state."""

    def __init__(self, **connection_options: Any):
        self.connection_options = connection_options

    def create_trip(
        self,
        *,
        destination_id: int,
        start_date: str,
        end_date: str,
        preferences: list[str],
        pace: str,
    ) -> int:
        sql = """
            INSERT INTO trips (destination_id, start_date, end_date, preferences, pace)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """
        try:
            with get_connection(**self.connection_options) as connection, connection.cursor() as cursor:
                cursor.execute(sql, (destination_id, start_date, end_date, Json(preferences), pace))
                row = cursor.fetchone()
                connection.commit()
        except LakebaseError:
            raise
        except Exception as exc:
            raise LakebaseError("Could not create the trip.") from exc
        if not row:
            raise LakebaseError("Trip creation did not return an identifier.")
        return int(row["id"])

    def get_trip(self, trip_id: int) -> dict | None:
        sql = """
            SELECT t.id, t.destination_id, t.start_date, t.end_date, t.preferences,
                   t.pace, t.status, t.live_resilience_score, t.created_at, t.updated_at,
                   d.city_key, d.display_name AS destination_name,
                   d.latitude, d.longitude, d.timezone
            FROM trips AS t
            JOIN destinations AS d ON d.id = t.destination_id
            WHERE t.id = %s
        """
        try:
            with get_connection(**self.connection_options) as connection, connection.cursor() as cursor:
                cursor.execute(sql, (trip_id,))
                row = cursor.fetchone()
        except LakebaseError:
            raise
        except Exception as exc:
            raise LakebaseError("Could not load the trip.") from exc
        if not row:
            return None
        result = dict(row)
        result["preferences"] = list(result.get("preferences") or [])
        return result

    def save_weather_snapshot(
        self,
        *,
        trip_id: int,
        forecast: dict,
        air_quality: dict | None,
    ) -> int:
        sql = """
            INSERT INTO weather_snapshots (
                trip_id, provider, forecast_json, air_quality_json, expires_at
            ) VALUES (%s, %s, %s, %s, NOW() + INTERVAL '1 hour')
            RETURNING id
        """
        try:
            with get_connection(**self.connection_options) as connection, connection.cursor() as cursor:
                cursor.execute(
                    sql,
                    (
                        trip_id,
                        forecast.get("provider") or "Open-Meteo",
                        Json(forecast),
                        Json(air_quality) if air_quality is not None else None,
                    ),
                )
                row = cursor.fetchone()
                connection.commit()
        except LakebaseError:
            raise
        except Exception as exc:
            raise LakebaseError("Could not persist the weather snapshot.") from exc
        if not row:
            raise LakebaseError("Weather snapshot insert returned no identifier.")
        return int(row["id"])

    def get_latest_weather_snapshot(self, trip_id: int) -> dict | None:
        """Return the newest immutable live provider snapshot for a trip."""
        sql = """
            SELECT id, trip_id, provider, forecast_json, air_quality_json,
                   fetched_at, expires_at
            FROM weather_snapshots
            WHERE trip_id = %s
            ORDER BY fetched_at DESC, id DESC
            LIMIT 1
        """
        try:
            with get_connection(**self.connection_options) as connection, connection.cursor() as cursor:
                cursor.execute(sql, (trip_id,))
                row = cursor.fetchone()
        except LakebaseError:
            raise
        except Exception as exc:
            raise LakebaseError("Could not load the trip weather snapshot.") from exc
        return dict(row) if row else None

    def update_live_resilience(self, trip_id: int, score: int) -> None:
        """Persist a deterministic live score, never a simulated scenario score."""
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
            raise ValueError("Live resilience score must be an integer from 0 to 100.")
        try:
            with get_connection(**self.connection_options) as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE trips
                    SET live_resilience_score = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (score, trip_id),
                )
                if cursor.rowcount != 1:
                    raise LakebaseError("Trip was not found while saving live resilience.")
                connection.commit()
        except LakebaseError:
            raise
        except Exception as exc:
            raise LakebaseError("Could not save live trip resilience.") from exc

    def replace_itinerary(self, *, trip_id: int, items: list[dict]) -> list[int]:
        """Replace a generated itinerary atomically so a retry cannot create duplicates."""
        insert_sql = """
            INSERT INTO itinerary_items (
                trip_id, attraction_id, day_date, start_time, end_time, title,
                category, indoor_outdoor, weather_sensitivity, suitability_score,
                risk_state, risk_reasons, notes, sort_order
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        inserted_ids: list[int] = []
        try:
            with get_connection(**self.connection_options) as connection, connection.cursor() as cursor:
                try:
                    cursor.execute("DELETE FROM itinerary_items WHERE trip_id = %s", (trip_id,))
                    for item in items:
                        cursor.execute(
                            insert_sql,
                            (
                                trip_id,
                                item["attraction_id"],
                                item["day_date"],
                                item["start_time"],
                                item.get("end_time"),
                                item["title"],
                                item.get("category"),
                                item.get("indoor_outdoor"),
                                item["weather_sensitivity"],
                                item["suitability_score"],
                                item["risk_state"],
                                Json(item.get("risk_reasons") or []),
                                item.get("notes"),
                                item["sort_order"],
                            ),
                        )
                        row = cursor.fetchone()
                        if not row:
                            raise LakebaseError("Itinerary insert returned no identifier.")
                        inserted_ids.append(int(row["id"]))
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except LakebaseError:
            raise
        except Exception as exc:
            raise LakebaseError("Could not persist the generated itinerary.") from exc
        return inserted_ids

    def get_itinerary(self, trip_id: int) -> list[dict]:
        sql = """
            SELECT i.id, i.trip_id, i.attraction_id, i.day_date, i.start_time, i.end_time,
                   i.title, i.category, i.indoor_outdoor, i.weather_sensitivity,
                   i.suitability_score, i.risk_state, i.risk_reasons, i.notes, i.sort_order,
                   a.activity_level
            FROM itinerary_items AS i
            LEFT JOIN attractions AS a ON a.id = i.attraction_id
            WHERE i.trip_id = %s
            ORDER BY i.day_date, i.sort_order, i.start_time, i.id
        """
        try:
            with get_connection(**self.connection_options) as connection, connection.cursor() as cursor:
                cursor.execute(sql, (trip_id,))
                rows = cursor.fetchall()
        except LakebaseError:
            raise
        except Exception as exc:
            raise LakebaseError("Could not load the itinerary.") from exc
        results: list[dict] = []
        for row in rows:
            item = dict(row)
            item["risk_reasons"] = list(item.get("risk_reasons") or [])
            if item.get("weather_sensitivity") is not None:
                item["weather_sensitivity"] = float(item["weather_sensitivity"])
            results.append(item)
        return results

    def itinerary_references_are_valid(self, trip_id: int) -> bool:
        """Verify every itinerary attraction belongs to the trip destination."""
        sql = """
            SELECT COUNT(*) AS invalid_count
            FROM itinerary_items AS i
            JOIN trips AS t ON t.id = i.trip_id
            LEFT JOIN attractions AS a
              ON a.id = i.attraction_id AND a.destination_id = t.destination_id
            WHERE i.trip_id = %s AND (i.attraction_id IS NULL OR a.id IS NULL)
        """
        try:
            with get_connection(**self.connection_options) as connection, connection.cursor() as cursor:
                cursor.execute(sql, (trip_id,))
                row = cursor.fetchone()
        except LakebaseError:
            raise
        except Exception as exc:
            raise LakebaseError("Could not validate itinerary attraction references.") from exc
        return bool(row and int(row["invalid_count"]) == 0)
