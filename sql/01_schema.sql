CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS destinations (
    id BIGSERIAL PRIMARY KEY,
    city_key TEXT UNIQUE NOT NULL,
    requested_name TEXT,
    display_name TEXT NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    timezone TEXT,
    last_ingested_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trips (
    id BIGSERIAL PRIMARY KEY,
    destination_id BIGINT NOT NULL REFERENCES destinations(id),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    preferences JSONB NOT NULL DEFAULT '[]'::jsonb,
    pace TEXT NOT NULL CHECK (pace IN ('relaxed', 'balanced', 'packed')),
    status TEXT NOT NULL DEFAULT 'active',
    live_resilience_score INTEGER CHECK (live_resilience_score BETWEEN 0 AND 100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_date >= start_date)
);

CREATE TABLE IF NOT EXISTS attractions (
    id BIGSERIAL PRIMARY KEY,
    destination_id BIGINT NOT NULL REFERENCES destinations(id) ON DELETE CASCADE,
    source_page_id TEXT,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    source_url TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    category TEXT,
    indoor_outdoor TEXT CHECK (indoor_outdoor IN ('indoor', 'outdoor', 'mixed')),
    weather_sensitivity DOUBLE PRECISION CHECK (weather_sensitivity BETWEEN 0.0 AND 1.0),
    activity_level TEXT CHECK (activity_level IN ('low', 'moderate', 'high')),
    estimated_duration_minutes INTEGER CHECK (estimated_duration_minutes > 0),
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    traveler_summary TEXT,
    embedding VECTOR(384) NOT NULL,
    embedding_model TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (destination_id, source_page_id)
);

-- Keep initialization idempotent for Phase 0 databases created before the
-- traveler-facing enrichment fields were added.
ALTER TABLE attractions ADD COLUMN IF NOT EXISTS activity_level TEXT;
ALTER TABLE attractions ADD COLUMN IF NOT EXISTS traveler_summary TEXT;

CREATE INDEX IF NOT EXISTS attractions_destination_idx
    ON attractions (destination_id);
CREATE INDEX IF NOT EXISTS attractions_embedding_hnsw_idx
    ON attractions USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS itinerary_items (
    id BIGSERIAL PRIMARY KEY,
    trip_id BIGINT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    attraction_id BIGINT REFERENCES attractions(id),
    day_date DATE NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME,
    title TEXT NOT NULL,
    category TEXT,
    indoor_outdoor TEXT CHECK (indoor_outdoor IN ('indoor', 'outdoor', 'mixed')),
    weather_sensitivity DOUBLE PRECISION NOT NULL CHECK (weather_sensitivity BETWEEN 0.0 AND 1.0),
    suitability_score INTEGER CHECK (suitability_score BETWEEN 0 AND 100),
    risk_state TEXT CHECK (risk_state IN ('GO', 'CAUTION', 'AT_RISK')),
    risk_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_time IS NULL OR end_time > start_time)
);

CREATE INDEX IF NOT EXISTS itinerary_items_trip_day_idx
    ON itinerary_items (trip_id, day_date, sort_order);

CREATE TABLE IF NOT EXISTS weather_snapshots (
    id BIGSERIAL PRIMARY KEY,
    trip_id BIGINT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'Open-Meteo',
    forecast_json JSONB NOT NULL,
    air_quality_json JSONB,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS weather_snapshots_trip_fetched_idx
    ON weather_snapshots (trip_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS repair_runs (
    id BIGSERIAL PRIMARY KEY,
    trip_id BIGINT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    trace_id TEXT NOT NULL,
    scenario_type TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'applied', 'rejected', 'failed')),
    before_resilience INTEGER CHECK (before_resilience BETWEEN 0 AND 100),
    projected_resilience INTEGER CHECK (projected_resilience BETWEEN 0 AND 100),
    rationale TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS repair_runs_trip_created_idx
    ON repair_runs (trip_id, created_at DESC);
CREATE INDEX IF NOT EXISTS repair_runs_trace_idx
    ON repair_runs (trace_id);

CREATE TABLE IF NOT EXISTS repair_actions (
    id BIGSERIAL PRIMARY KEY,
    repair_run_id BIGINT NOT NULL REFERENCES repair_runs(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL CHECK (action_type IN ('MOVE', 'REPLACE', 'ADD', 'REMOVE')),
    itinerary_item_id BIGINT REFERENCES itinerary_items(id),
    before_state JSONB,
    after_state JSONB,
    reason TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS repair_actions_run_order_idx
    ON repair_actions (repair_run_id, sort_order);

CREATE TABLE IF NOT EXISTS agent_events (
    id BIGSERIAL PRIMARY KEY,
    trace_id TEXT NOT NULL,
    trip_id BIGINT REFERENCES trips(id) ON DELETE SET NULL,
    repair_run_id BIGINT REFERENCES repair_runs(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    tool_name TEXT,
    status TEXT NOT NULL CHECK (status IN ('started', 'ok', 'error')),
    input_summary JSONB,
    output_summary JSONB,
    duration_ms BIGINT CHECK (duration_ms IS NULL OR duration_ms >= 0),
    model TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS agent_events_trace_created_idx
    ON agent_events (trace_id, created_at);
CREATE INDEX IF NOT EXISTS agent_events_trip_created_idx
    ON agent_events (trip_id, created_at);
