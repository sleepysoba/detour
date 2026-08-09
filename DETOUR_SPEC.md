# DETOUR — Engineering/Product Specification

> **Tagline:** Plans change. Yours can too.
>
> **Product thesis:** Detour is a self-healing travel planner. It creates a personalized itinerary, evaluates how resilient each activity is to live weather and air-quality conditions, identifies vulnerable plans, and uses a tool-calling AI agent to propose an evidence-backed repair that preserves the traveler's intent. The user reviews a visual diff before applying changes to persistent Lakebase state.

## 1. Capstone Goal

Build a polished, interactive Databricks App that clearly demonstrates:

- a real third-party data pipeline using Open-Meteo and Wikimedia/Wikipedia APIs;
- unstructured text ingestion and semantic retrieval over attraction descriptions;
- Lakebase/PostgreSQL persistence, including pgvector retrieval;
- a Databricks-hosted LLM (`system.ai.meta-llama-3-3-70b-instruct`) acting as a tool-calling agent;
- meaningful read + write agent actions;
- lightweight agent tracing/observability;
- a frontend that feels like a coherent travel product rather than a chatbot demo.

The original capstone Spark requirement is intentionally out of scope per the current course update.

## 2. Success Criteria / Judge Moment

The happy-path demo must work end-to-end:

1. Open Detour and create a 2–4 day trip to an arbitrary city such as **Boulder, Colorado** or **Miami, Florida**.
2. Select a few preferences and a travel pace.
3. Detour resolves the city, obtains current forecast/AQI data, ingests/caches nearby attraction descriptions, embeds them, and creates a personalized itinerary.
4. The trip dashboard shows an explainable **Trip Resilience** score and individual activity risk states.
5. Trigger **Scenario Lab → Rainstorm**.
6. Resilience visibly drops and vulnerable itinerary items are highlighted.
7. Click **Repair My Trip**.
8. The Llama agent uses tools to inspect trip state, conditions, and semantic attraction candidates, then writes a **pending repair proposal** to Lakebase.
9. The UI shows a concise itinerary diff, rationale, and projected resilience improvement.
10. Click **Apply Detour**.
11. The itinerary changes persist in Lakebase and survive a refresh.
12. Open **View Agent Trace** to show model/tool/retrieval/persistence events for the repair.

If this sequence is reliable and polished, the capstone is submission-ready.

## 3. Product Positioning

Detour is **not** a generic “AI travel planner.” Its differentiator is itinerary resilience.

Traditional planners answer: **“What should I do?”**

Detour also answers: **“Will this plan still make sense when conditions change, and can it repair itself without destroying what I wanted from the trip?”**

### Core design principle

**AI for judgment; deterministic code for facts and constraints.**

- Open-Meteo provides factual weather/AQI data.
- Python calculates suitability/risk/resilience deterministically.
- MiniLM + pgvector retrieves semantically relevant attractions.
- Llama reasons over candidates and proposes changes.
- Narrow backend tools perform persistence.

Do not ask the LLM to invent weather, calculate numerical scores, or write arbitrary SQL.

## 4. Locked Technology Choices

### Application
- Python 3.x
- Flask + Jinja templates
- Vanilla JavaScript and CSS
- Gunicorn for Databricks Apps

### Persistence
- Databricks Lakebase / PostgreSQL
- `psycopg2`/existing proven Lakebase connection pattern
- `pgvector` extension for attraction embeddings

### AI
- Agent / generation model: `system.ai.meta-llama-3-3-70b-instruct`
- OpenAI-compatible client pointed at the workspace AI Gateway base URL
- Embeddings for MVP: `sentence-transformers/all-MiniLM-L6-v2`
- Embedding dimension: **384**
- Load MiniLM lazily as a process singleton; keep CPU/thread settings conservative

### Optional embedding contingency only
Do not spend early build time changing embedding models. If MiniLM cannot run reliably in the deployed Databricks App, introduce a small `EmbeddingService` implementation backed by a workspace-served embedding model such as Qwen3. This is a fallback, not the initial architecture.

### External APIs
- Open-Meteo Geocoding
- Open-Meteo Forecast
- Open-Meteo Air Quality
- Wikimedia/Wikipedia APIs for unstructured attraction descriptions

### Observability
- Python standard `logging`
- Lakebase `agent_events` rows for user-visible trace events
- No heavyweight tracing framework is required
- Never store or expose hidden chain-of-thought

## 5. Scope Boundaries

### MVP — must work
- arbitrary-city trip creation;
- 3 curated demo presets;
- weather + AQI retrieval;
- dynamic Wikimedia attraction ingestion/caching;
- MiniLM embeddings and pgvector semantic retrieval;
- itinerary generation and persistence;
- deterministic activity suitability and trip resilience;
- Rainstorm Scenario Lab;
- tool-calling repair agent;
- pending repair proposal with visual diff;
- explicit user approval and persistent apply;
- agent event tracing and trace drawer;
- error/loading/empty states;
- Databricks Apps deployment;
- credible README and screenshots.

### Should have
- Heatwave scenario;
- Poor AQI scenario;
- source/evidence drawer for attraction descriptions;
- simple packing recommendations derived from forecast;
- prewarming script for demo cities;
- undo of most recent repair if implementation is trivial.

### Explicit non-goals
Do not build these before the MVP is complete:
- user authentication;
- flights/hotels/booking;
- restaurant reservations;
- route optimization/travel-time matrix;
- Google Maps integration;
- calendar sync;
- collaborative trips;
- budget optimization;
- notifications/background workers;
- multi-agent orchestration;
- LangChain/LangGraph unless a blocker forces it;
- mobile-native app;
- complex ML scoring model.

## 6. User Experience

### 6.1 Landing / Create Trip

Primary fields:
- destination text input;
- start date;
- end date (prefer 2–4 day trips; cap MVP at 5 days);
- preference chips: Outdoors, Food, Culture, Photography, Relaxed, Adventure, Low crowds;
- pace: Relaxed / Balanced / Packed.

Primary CTA: **Build My Trip**

Below the form, show 3 demo shortcuts:
- Seattle Weekend
- San Francisco Escape
- New York Explorer

Presets must use the same underlying pipeline as custom cities. A prewarming script may cache their destination/attraction data ahead of the demo.

### 6.2 Trip Dashboard

The dashboard is the product centerpiece. Do not center the page around a chat box.

Top section:
- destination + dates;
- compact multi-day weather ribbon;
- **Trip Resilience** score, 0–100;
- one-sentence status summary.

Main left column:
- itinerary grouped by day;
- chronological cards/timeline;
- each activity shows time, name, category, indoor/outdoor classification, suitability score, status, and top risk reason when relevant.

Main right column:
- **Detour Intelligence** vulnerability summary;
- **Repair My Trip** CTA when risk exists;
- **Scenario Lab** buttons;
- optional small packing/safety summary.

Bottom/subordinate element:
- compact “Ask Detour” input may be added only after the repair flow is complete. The application itself should demonstrate intelligence without requiring chat.

### 6.3 Scenario Lab

MVP scenario: **Rainstorm**.

Optional scenarios: Heatwave, Poor AQI.

Scenario simulation must:
- be clearly labeled as simulated;
- never overwrite the real forecast snapshot;
- inject overrides into the same deterministic scoring pipeline used for live conditions;
- return before/after resilience and affected items;
- be reversible by selecting “Live conditions.”

Suggested Rainstorm override:
- raise precipitation probability materially (e.g. floor at 85%);
- add precipitation severity/rain condition;
- optionally raise wind modestly.

The exact numbers matter less than being deterministic, documented, and obviously simulated.

### 6.4 Repair Proposal

After “Repair My Trip,” show:
- before resilience;
- projected after resilience;
- number of changes;
- concise rationale;
- visual diff of each action;
- `Apply Detour` and `Keep Original` actions;
- `View Agent Trace` link/button.

Example diff:

```text
Tuesday
- 2:00 PM  Chautauqua Trail
+ 2:00 PM  Museum of Boulder

- 4:30 PM  Museum of Boulder
+ 4:30 PM  Chautauqua Trail
```

Prefer **move/swap** operations that preserve activities before replacing an attraction entirely. Replacement is allowed when rescheduling cannot sufficiently repair risk.

## 7. Visual Direction

Aim for a polished travel/editorial product, not a generic admin dashboard.

Principles:
- strong typography and whitespace;
- restrained cards and borders;
- high information hierarchy;
- clear risk states;
- timeline/diff interactions as the visual signature;
- subtle motion only for loading, applying repairs, and score changes;
- no giant chatbot dominating the page;
- responsive enough for laptop and tablet judges, but do not burn time on perfect mobile behavior.

The interface should make the core story understandable in ~10 seconds:
**live conditions → itinerary risk → self-healing repair**.

## 8. Data Model

Use a SQL setup script and idempotent schema initialization.

### 8.1 `destinations`
Caches resolved city metadata and ingestion state.

Suggested fields:
- `id BIGSERIAL PRIMARY KEY`
- `city_key TEXT UNIQUE NOT NULL` — normalized key, e.g. `boulder-co-us`
- `requested_name TEXT`
- `display_name TEXT NOT NULL`
- `latitude DOUBLE PRECISION NOT NULL`
- `longitude DOUBLE PRECISION NOT NULL`
- `timezone TEXT`
- `last_ingested_at TIMESTAMPTZ`
- `created_at TIMESTAMPTZ DEFAULT NOW()`

### 8.2 `trips`
- `id BIGSERIAL PRIMARY KEY`
- `destination_id BIGINT REFERENCES destinations(id)`
- `start_date DATE NOT NULL`
- `end_date DATE NOT NULL`
- `preferences JSONB NOT NULL DEFAULT '[]'::jsonb`
- `pace TEXT NOT NULL`
- `status TEXT NOT NULL DEFAULT 'active'`
- `live_resilience_score INTEGER`
- `created_at TIMESTAMPTZ DEFAULT NOW()`
- `updated_at TIMESTAMPTZ DEFAULT NOW()`

### 8.3 `attractions`
Unstructured knowledge + structured retrieval metadata.

- `id BIGSERIAL PRIMARY KEY`
- `destination_id BIGINT REFERENCES destinations(id)`
- `source_page_id TEXT`
- `name TEXT NOT NULL`
- `description TEXT NOT NULL`
- `source_url TEXT`
- `latitude DOUBLE PRECISION`
- `longitude DOUBLE PRECISION`
- `category TEXT`
- `indoor_outdoor TEXT` — `indoor`, `outdoor`, `mixed`
- `weather_sensitivity DOUBLE PRECISION` — 0.0–1.0
- `estimated_duration_minutes INTEGER`
- `tags JSONB NOT NULL DEFAULT '[]'::jsonb`
- `embedding VECTOR(384) NOT NULL`
- `embedding_model TEXT NOT NULL`
- `created_at TIMESTAMPTZ DEFAULT NOW()`
- unique constraint over destination/source page when possible

Create an HNSW cosine index if supported by the working Lakebase pattern.

### 8.4 `itinerary_items`
- `id BIGSERIAL PRIMARY KEY`
- `trip_id BIGINT REFERENCES trips(id) ON DELETE CASCADE`
- `attraction_id BIGINT REFERENCES attractions(id)` nullable for free-form items
- `day_date DATE NOT NULL`
- `start_time TIME NOT NULL`
- `end_time TIME`
- `title TEXT NOT NULL`
- `category TEXT`
- `indoor_outdoor TEXT`
- `weather_sensitivity DOUBLE PRECISION NOT NULL`
- `suitability_score INTEGER`
- `risk_state TEXT` — `GO`, `CAUTION`, `AT_RISK`
- `risk_reasons JSONB NOT NULL DEFAULT '[]'::jsonb`
- `notes TEXT`
- `sort_order INTEGER NOT NULL DEFAULT 0`
- `created_at TIMESTAMPTZ DEFAULT NOW()`
- `updated_at TIMESTAMPTZ DEFAULT NOW()`

### 8.5 `weather_snapshots`
Cache live conditions used for explainability/debugging.

- `id BIGSERIAL PRIMARY KEY`
- `trip_id BIGINT REFERENCES trips(id) ON DELETE CASCADE`
- `provider TEXT NOT NULL DEFAULT 'Open-Meteo'`
- `forecast_json JSONB NOT NULL`
- `air_quality_json JSONB`
- `fetched_at TIMESTAMPTZ DEFAULT NOW()`
- `expires_at TIMESTAMPTZ`

### 8.6 `repair_runs`
One repair proposal / lifecycle.

- `id BIGSERIAL PRIMARY KEY`
- `trip_id BIGINT REFERENCES trips(id) ON DELETE CASCADE`
- `trace_id TEXT NOT NULL`
- `scenario_type TEXT` nullable (`live`, `rainstorm`, etc.)
- `status TEXT NOT NULL` — `pending`, `applied`, `rejected`, `failed`
- `before_resilience INTEGER`
- `projected_resilience INTEGER`
- `rationale TEXT`
- `created_at TIMESTAMPTZ DEFAULT NOW()`
- `applied_at TIMESTAMPTZ`

### 8.7 `repair_actions`
- `id BIGSERIAL PRIMARY KEY`
- `repair_run_id BIGINT REFERENCES repair_runs(id) ON DELETE CASCADE`
- `action_type TEXT NOT NULL` — `MOVE`, `REPLACE`, `ADD`, `REMOVE`
- `itinerary_item_id BIGINT`
- `before_state JSONB`
- `after_state JSONB`
- `reason TEXT`
- `sort_order INTEGER NOT NULL DEFAULT 0`

### 8.8 `agent_events`
Lightweight trace events only; never chain-of-thought.

- `id BIGSERIAL PRIMARY KEY`
- `trace_id TEXT NOT NULL`
- `trip_id BIGINT`
- `repair_run_id BIGINT`
- `event_type TEXT NOT NULL`
- `tool_name TEXT`
- `status TEXT NOT NULL` — `started`, `ok`, `error`
- `input_summary JSONB`
- `output_summary JSONB`
- `duration_ms BIGINT`
- `model TEXT`
- `created_at TIMESTAMPTZ DEFAULT NOW()`

Indexes:
- `(trace_id, created_at)`
- `(trip_id, created_at)`

## 9. External Data Pipeline

### 9.1 Destination resolution
Use the proven Open-Meteo geocoding broker pattern.

Input: arbitrary human city text such as `Boulder, Colorado`.

Output:
- canonical display name;
- latitude/longitude;
- timezone;
- normalized `city_key`.

Check `destinations` first. Upsert resolved result.

### 9.2 Weather + air quality
Extend the proven WeatherOps broker.

Needed weather signals:
- temperature;
- precipitation probability;
- wind/gust;
- UV where practical;
- weather condition/code.

Needed AQI signals:
- US AQI;
- PM2.5 if available.

Prefer hourly/slot-level conditions for scheduled activities. If hourly parsing threatens the deadline, daily values are an acceptable fallback, but keep the service interface slot-oriented so it can be upgraded without changing the rest of the app.

Normalize upstream failures into safe application errors. Use timeouts. Do not leak provider response bodies or credentials.

### 9.3 Wikimedia attraction discovery
For a destination not recently ingested:

1. Use destination coordinates/name to discover approximately 15–30 potentially relevant Wikipedia/Wikimedia pages near or strongly associated with the city.
2. Fetch plain-text extracts and canonical page URLs.
3. Remove obviously irrelevant/empty/administrative pages.
4. Keep roughly 12–20 good attraction candidates.
5. Enrich candidates in a **batched** Llama request into structured metadata:
   - category;
   - indoor/outdoor/mixed;
   - weather sensitivity 0–1;
   - estimated duration;
   - concise tags.
6. Validate the returned JSON and clamp numeric fields.
7. Embed description + tags with MiniLM.
8. Upsert into `attractions`.
9. Set `destinations.last_ingested_at`.

Do not perform one LLM call per attraction unless absolutely necessary. Batch work to reduce latency and quota risk.

If enrichment fails, use conservative heuristics/defaults rather than making the whole trip unusable.

## 10. Semantic Retrieval

### Embedding model
`sentence-transformers/all-MiniLM-L6-v2`

- expected dimensions: 384;
- normalize embeddings;
- lazy-load once per process;
- vector literal / pgvector code may reuse the proven Weather Intelligence pattern.

### Retrieval document
Compose a text representation similar to:

```text
Museum of Boulder. Local history and cultural museum...
Category: museum. Indoor. Tags: history, culture, relaxed, rainy-day.
```

### Query examples
- `scenic relaxed indoor activity good during rain and photography friendly`
- `outdoor nature experience not strenuous`
- `culture activity that works during poor air quality`

Filter by `destination_id`, then cosine-rank with pgvector.

The retrieval service should return compact evidence objects, not raw database rows.

## 11. Deterministic Suitability + Resilience

Reuse/adapt the WeatherOps recommendation philosophy.

### 11.1 Attraction sensitivity
`weather_sensitivity` is between 0 and 1:
- ~0.10 = fully indoor museum;
- ~0.30 = mostly indoor market;
- ~0.55 = mixed walking/neighborhood;
- ~0.85 = outdoor sightseeing;
- 1.00 = hiking/beach/highly exposed activity.

### 11.2 Condition penalties
Build deterministic penalties for:
- rain;
- temperature/heat/cold;
- wind;
- UV;
- AQI.

Reuse existing thresholds where practical. Add AQI as a separate penalty.

Suggested AQI behavior:
- <= 50: no penalty;
- 51–100: small penalty for sensitive/high-exertion outdoor activities;
- 101–150: meaningful outdoor penalty;
- >150: strong penalty for outdoor activities.

Indoor attractions should be much less sensitive to AQI/weather but not necessarily zero-risk.

### 11.3 Activity score
Return:
- `score` 0–100;
- state: `GO`, `CAUTION`, `AT_RISK`;
- top 1–3 risk reasons;
- weather/AQI signals used;
- policy version.

### 11.4 Trip resilience
Compute from itinerary activity scores with weather sensitivity weighting.

A simple explainable formula is preferred over fake complexity. For example:

```text
weighted_risk_i = (100 - suitability_i) * max(weather_sensitivity_i, 0.2)
trip_resilience = clamp(100 - average(weighted_risk_i), 0, 100)
```

Refine only if the resulting numbers are unintuitive in Boulder/Seattle/Miami tests.

## 12. Initial Itinerary Generation

Input:
- destination;
- trip dates;
- preferences;
- pace;
- semantic attraction candidates;
- forecast summary.

Pipeline:
1. Retrieve a generous candidate set using preference-driven semantic queries.
2. Deterministically score candidates for relevant days/slots.
3. Send compact candidates + constraints to Llama.
4. Ask for strict structured JSON only.
5. Validate:
   - dates are within trip;
   - times are sensible;
   - attraction IDs exist;
   - no obvious duplicates;
   - schedule count respects pace.
6. Persist itinerary items.
7. Calculate and persist live resilience.

If Llama output fails validation after one repair attempt, generate a deterministic fallback itinerary from the highest-ranked candidate set rather than crashing.

Suggested pace counts:
- Relaxed: ~2 activities/day;
- Balanced: ~3/day;
- Packed: ~4/day.

## 13. Agent Architecture

Use an explicit function/tool-calling loop with the Databricks-hosted Llama model. No agent framework is required.

### Agent objective
Given a current trip and live or simulated conditions, propose the smallest set of itinerary changes that materially improves resilience while preserving preferences and avoiding unnecessary activity deletion.

### Tool contracts
Keep tools narrow and JSON-serializable.

#### `get_trip_state(trip_id)`
Returns trip metadata, preferences, itinerary items, and current resilience.
Read-only.

#### `get_conditions(trip_id, scenario=None)`
Returns normalized live or simulated condition summaries for the trip dates/slots.
Read-only.

#### `search_attractions(trip_id, query, limit=8)`
Embeds query and performs destination-scoped pgvector retrieval.
Read-only.

#### `evaluate_activity(activity_or_attraction_id, date_time, scenario=None)`
Runs deterministic scoring and returns score/risk reasons.
Read-only.

#### `save_repair_proposal(trip_id, scenario, before_resilience, projected_resilience, rationale, actions)`
Validates action references and writes a `pending` `repair_runs` row plus `repair_actions` rows.
**This is the agent's persistent write action.**
Returns `repair_id`.

Do not give the model arbitrary SQL or generic write access.

### Applying a repair
`Apply Detour` is an explicit user confirmation.

Backend endpoint/service:
- loads pending proposal;
- transactionally applies validated actions to `itinerary_items`;
- recalculates live/scenario resilience;
- marks repair `applied`;
- emits trace event.

The model does not need to autonomously apply an unreviewed repair.

### Agent guardrails
- max tool loop iterations: 6–8;
- max repair actions: 3 for MVP;
- prefer MOVE/SWAP before REPLACE;
- never invent attraction IDs;
- never alter destination/dates;
- do not remove every outdoor activity just because of one bad slot;
- preserve user preferences;
- return concise rationale, not chain-of-thought.

## 14. Agent Tracing / Logging

### 14.1 Standard application logs
Use `logging.getLogger(__name__)` in every service.

Useful structured-ish messages:
- destination resolved;
- weather/AQI fetch duration;
- attraction discovery counts;
- embedding count/duration;
- retrieval query/top-k/duration;
- model call duration;
- tool name/duration/state;
- repair proposal and apply summaries;
- upstream/database errors.

Never log secrets, auth headers, full database URLs, or hidden model reasoning.

### 14.2 `agent_events`
Every repair run has a UUID `trace_id`.

Recommended event types:
- `AGENT_STARTED`
- `MODEL_REQUEST`
- `MODEL_RESPONSE`
- `TOOL_CALLED`
- `TOOL_COMPLETED`
- `RETRIEVAL_COMPLETED`
- `REPAIR_PROPOSED`
- `REPAIR_APPLIED`
- `AGENT_COMPLETED`
- `AGENT_ERROR`

Store concise summaries only.

### 14.3 User-visible trace
The dashboard should show a friendly progress sequence such as:

```text
✓ Inspected 6 itinerary activities
✓ Checked live conditions
✓ Found 2 vulnerable activities
✓ Retrieved 8 alternatives
✓ Compared weather and preference fit
✓ Created a 2-change repair
```

A “View Agent Trace” drawer/modal can show technical events:
- timestamp;
- event/tool;
- status;
- duration;
- safe input/output summary;
- model name.

Do not display private chain-of-thought.

## 15. Suggested Flask Routes

Page routes:
- `GET /` — landing/create trip
- `GET /trips/<int:trip_id>` — dashboard

API/action routes:
- `POST /api/trips` — create trip and start/execute initialization
- `POST /api/trips/<id>/generate` — generate itinerary if separated from create
- `POST /api/trips/<id>/scenario` — evaluate scenario without mutating real forecast
- `POST /api/trips/<id>/repair` — run repair agent and persist pending proposal
- `POST /api/repairs/<id>/apply` — user-approved transactional apply
- `POST /api/repairs/<id>/reject` — optional
- `GET /api/traces/<trace_id>` — safe trace events
- `GET /health` — app/database health without secret leakage

Prefer simple synchronous requests initially. Add polling only if model/ingestion latency makes it necessary.

## 16. Service / Module Structure

Target structure (names may vary slightly, but responsibilities should remain separated):

```text
detour/
├── app.py
├── app.yaml
├── gunicorn.conf.py
├── requirements.txt
├── .env.example
├── setup_secrets.py
├── detour/
│   ├── __init__.py
│   ├── config.py
│   ├── db.py
│   ├── models.py              # repository/query functions, not ORM required
│   ├── weather.py             # Open-Meteo broker
│   ├── air_quality.py         # may be combined with weather.py
│   ├── scoring.py             # deterministic suitability/resilience
│   ├── wikimedia.py           # discovery/extracts
│   ├── embeddings.py          # lazy MiniLM singleton + vector helpers
│   ├── retrieval.py           # pgvector search
│   ├── llm.py                 # Databricks AI Gateway client
│   ├── itinerary.py           # initial itinerary generation/validation
│   ├── agent.py               # function-calling loop
│   ├── tools.py               # tool schemas + dispatch
│   ├── repairs.py             # proposal validation/apply transaction
│   ├── scenarios.py           # live/rainstorm/etc. overrides
│   └── tracing.py             # agent_events + timing helpers
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── trip.html
│   └── components/...
├── static/
│   ├── css/app.css
│   └── js/app.js
├── sql/
│   ├── 01_schema.sql
│   └── 02_demo_seed.sql       # optional; do not fake live weather
├── scripts/
│   ├── prewarm_demo_cities.py
│   ├── smoke_test.py
│   └── validate_submission.py
└── tests/
    ├── test_scoring.py
    ├── test_scenarios.py
    ├── test_repair_validation.py
    ├── test_retrieval_contract.py
    └── test_app_contract.py
```

Avoid a giant `app.py`. Route handlers should orchestrate services, not contain all business logic.

## 17. Configuration / Secrets

Reuse the proven `setup_secrets.py` pattern for Lakebase.

Expected environment values:

```text
LAKEBASE_URL=                      # local direct URL, optional
LAKEBASE_SECRET_SCOPE=database
LAKEBASE_SECRET_KEY=lakebase-url

DATABRICKS_TOKEN=
DATABRICKS_AI_BASE_URL=https://<workspace>/ai-gateway/mlflow/v1
DATABRICKS_CHAT_MODEL=system.ai.meta-llama-3-3-70b-instruct

EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
EMBEDDING_DIMENSIONS=384
LOG_LEVEL=INFO
AUTO_INIT_DB=true
```

Do not commit `.env` or credentials.

## 18. Failure Handling

A judge should get a useful UI message, not a stack trace.

Required cases:
- location not found;
- trip dates outside supported live forecast window;
- Open-Meteo timeout/error;
- Wikimedia timeout/too few attractions;
- Llama call fails;
- invalid Llama JSON/tool arguments;
- MiniLM model load failure;
- Lakebase unavailable;
- repair proposal no longer matches current itinerary state.

Where possible, degrade gracefully:
- Wikimedia enrichment failure → heuristic metadata;
- Llama itinerary failure → deterministic candidate schedule;
- trace persistence failure → log locally and continue core action if safe;
- no AQI → calculate weather-only resilience and label AQI unavailable.

## 19. Tests / Acceptance Contracts

At minimum, automated tests must cover:

### Scoring
- indoor museum remains high-score during rainstorm;
- hiking score drops materially in rainstorm;
- high AQI penalizes outdoor/high-exertion activities more than indoor activities;
- trip resilience stays 0–100.

### Scenario
- scenario evaluation does not mutate stored live forecast;
- turning scenario off restores live scores.

### Repair validation
- unknown itinerary/attraction IDs rejected;
- max action count enforced;
- out-of-trip dates rejected;
- apply occurs in one DB transaction conceptually;
- applied proposal cannot be applied twice.

### Retrieval
- query embedding dimension = 384;
- destination filter is always applied;
- retrieval returns safe compact objects.

### App contracts
- `/health` works;
- landing page renders;
- invalid trip input returns clean validation;
- trace endpoint never returns hidden prompts/reasoning/secrets.

## 20. Demo Data / Test Matrix

Manually test at least:

### Presets
- Seattle
- San Francisco
- New York

### Arbitrary cities
- Boulder, Colorado
- Miami, Florida
- one additional non-preset city

For each custom city, confirm:
- geocoding;
- attractions discovered;
- embeddings stored;
- semantic retrieval works;
- itinerary generated;
- weather/resilience visible;
- Rainstorm changes scores;
- repair proposal applies and persists.

## 21. Build Order

Do not build horizontally. Build vertical slices.

### Phase 0 — bootstrap
- repo structure;
- dependencies;
- config;
- Lakebase connection;
- schema;
- `/health`;
- clean base page.

### Phase 1 — prove integrations
- one Llama chat call;
- one Llama tool-call smoke test;
- MiniLM 384-d embedding smoke test;
- pgvector insert/search smoke test;
- Open-Meteo Boulder request;
- Wikimedia Boulder request.

Stop and fix any failures before continuing.

### Phase 2 — destination intelligence
- destination resolve/cache;
- Wikimedia ingest/enrich/embed/cache;
- semantic retrieval;
- tests.

### Phase 3 — live trip
- trip creation;
- weather/AQI snapshot;
- initial itinerary generation;
- deterministic scoring/resilience;
- dashboard.

At this point Boulder must work end-to-end.

### Phase 4 — self-healing core
- Rainstorm scenario;
- repair agent tools;
- pending proposal persistence;
- visual diff;
- Apply Detour transaction;
- refresh persistence.

### Phase 5 — tracing/polish
- `agent_events` instrumentation;
- progress UI;
- trace drawer;
- loading/error states;
- demo presets/prewarm.

### Phase 6 — submission
- Databricks deployment;
- manual test matrix;
- screenshots;
- README architecture/rubric mapping/demo script;
- ZIP validation.

## 22. Coding Principles for the Agent

1. Preserve this architecture unless a verified platform constraint makes a change necessary.
2. Prefer proven code from `_reference/` over rewrites, but adapt naming/contracts so Detour is coherent.
3. Keep business logic testable without network calls.
4. Mock external APIs in unit tests; use live calls only in smoke/manual tests.
5. Use parameterized SQL exclusively.
6. Never expose credentials in logs/errors/UI.
7. Validate all LLM structured outputs/tool arguments.
8. Avoid speculative abstractions and unused framework layers.
9. Do not build stretch features before the happy-path acceptance flow works.
10. After each phase, run tests and summarize what is actually working before continuing.

## 23. README Story

Final README should make the grader's job easy:

1. **What Detour is** — self-healing itinerary thesis.
2. **Why it is different** — resilience/repair, not generic travel chat.
3. **60-second demo flow**.
4. **Architecture diagram**.
5. **Capstone requirement mapping**:
   - Open-Meteo/Wikimedia APIs;
   - Wikimedia unstructured data;
   - MiniLM + pgvector RAG;
   - Databricks-hosted Llama tool-calling agent;
   - Lakebase reads/writes;
   - Databricks App frontend.
6. **AI engineering decisions** — deterministic scoring vs LLM judgment.
7. **Agent tools and trace events**.
8. **Local setup / Databricks deployment**.
9. **Screenshots**.
10. **Known MVP limits**.

## 24. Definition of Done

Detour is done when a fresh judge can:

- open the deployed app;
- create a Boulder/Miami/non-preset trip;
- see a real personalized itinerary and live resilience;
- simulate a rainstorm;
- watch the itinerary become vulnerable;
- run an agent repair;
- understand why the agent made its proposal;
- inspect the tool/action trace;
- apply the proposal;
- refresh and see the repaired itinerary persisted.

Anything that does not make this sequence more reliable, understandable, or visually compelling is lower priority.
