"""Safe, lightweight persistence for observable AI events."""

from __future__ import annotations

import logging
from typing import Any

from psycopg2.extras import Json

from detour.db import LakebaseError, run_query, run_write

logger = logging.getLogger(__name__)

ALLOWED_STATUSES = {"started", "ok", "error"}
SENSITIVE_KEY_PARTS = {
    "authorization",
    "chain_of_thought",
    "credential",
    "password",
    "prompt",
    "reasoning",
    "secret",
    "token",
}
MAX_SUMMARY_STRING_LENGTH = 500
MAX_SUMMARY_ITEMS = 30


class TraceError(RuntimeError):
    """Raised when a trace event is unsafe or cannot be persisted."""


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_SUMMARY_STRING_LENGTH]
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in list(value.items())[:MAX_SUMMARY_ITEMS]:
            normalized_key = str(key)
            lowered_key = normalized_key.casefold()
            if any(part in lowered_key for part in SENSITIVE_KEY_PARTS):
                continue
            safe[normalized_key] = _safe_value(item, depth=depth + 1)
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth=depth + 1) for item in value[:MAX_SUMMARY_ITEMS]]
    return str(value)[:MAX_SUMMARY_STRING_LENGTH]


def safe_summary(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    """Bound summary size and remove secret/reasoning-shaped fields."""
    if summary is None:
        return None
    if not isinstance(summary, dict):
        raise TraceError("Trace summaries must be dictionaries.")
    return _safe_value(summary)


class TraceService:
    """Persist and read concise events from the Phase 0 agent_events table."""

    def __init__(self, **connection_options: Any):
        self.connection_options = connection_options

    def record(
        self,
        *,
        trace_id: str,
        event_type: str,
        status: str,
        tool_name: str | None = None,
        input_summary: dict[str, Any] | None = None,
        output_summary: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        model: str | None = None,
        trip_id: int | None = None,
        repair_run_id: int | None = None,
    ) -> None:
        if not trace_id.strip() or not event_type.strip():
            raise TraceError("trace_id and event_type are required.")
        if status not in ALLOWED_STATUSES:
            raise TraceError("Trace status must be started, ok, or error.")
        if duration_ms is not None and duration_ms < 0:
            raise TraceError("Trace duration cannot be negative.")

        try:
            run_write(
                """
                INSERT INTO agent_events (
                    trace_id, trip_id, repair_run_id, event_type, tool_name,
                    status, input_summary, output_summary, duration_ms, model
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    trace_id,
                    trip_id,
                    repair_run_id,
                    event_type,
                    tool_name,
                    status,
                    Json(safe_summary(input_summary)) if input_summary is not None else None,
                    Json(safe_summary(output_summary)) if output_summary is not None else None,
                    duration_ms,
                    model,
                ),
                **self.connection_options,
            )
        except LakebaseError as exc:
            raise TraceError("Could not persist the trace event.") from exc

    def record_safe(self, **event: Any) -> bool:
        """Best-effort persistence so tracing never masks the core integration."""
        try:
            self.record(**event)
            return True
        except TraceError:
            logger.warning(
                "trace_persistence_failed event_type=%s status=%s",
                event.get("event_type"),
                event.get("status"),
            )
            return False

    def get_events(self, trace_id: str) -> list[dict]:
        """Read back safe diagnostic events for verification."""
        try:
            return run_query(
                """
                SELECT trace_id, event_type, tool_name, status,
                       input_summary, output_summary, duration_ms, model, created_at
                FROM agent_events
                WHERE trace_id = %s
                ORDER BY created_at, id
                """,
                (trace_id,),
                **self.connection_options,
            )
        except LakebaseError as exc:
            raise TraceError("Could not read trace events.") from exc
