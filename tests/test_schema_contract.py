import re
from pathlib import Path


SCHEMA = (Path(__file__).parents[1] / "sql" / "01_schema.sql").read_text(encoding="utf-8")


def test_schema_declares_all_phase_zero_tables_idempotently():
    tables = {
        "destinations",
        "trips",
        "attractions",
        "itinerary_items",
        "weather_snapshots",
        "repair_runs",
        "repair_actions",
        "agent_events",
    }

    declared = set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", SCHEMA, re.IGNORECASE))
    assert declared == tables


def test_schema_enables_pgvector_and_uses_locked_embedding_dimension():
    assert re.search(r"CREATE EXTENSION IF NOT EXISTS vector", SCHEMA, re.IGNORECASE)
    assert re.search(r"embedding\s+VECTOR\(384\)\s+NOT NULL", SCHEMA, re.IGNORECASE)
    assert re.search(r"USING hnsw\s*\(embedding vector_cosine_ops\)", SCHEMA, re.IGNORECASE)


def test_schema_has_required_agent_event_indexes():
    assert "ON agent_events (trace_id, created_at)" in SCHEMA
    assert "ON agent_events (trip_id, created_at)" in SCHEMA
