# Detour Coding Agent Instructions

Read `DETOUR_SPEC.md` completely before writing code. It is the source of truth.

## Locked decisions
- Flask + Jinja + vanilla JS/CSS
- Lakebase/PostgreSQL + pgvector
- MiniLM `sentence-transformers/all-MiniLM-L6-v2` for 384-d embeddings
- Databricks Llama model `system.ai.meta-llama-3-3-70b-instruct` for generation/tool calling
- Open-Meteo + Wikimedia external APIs
- explicit function-calling loop; no agent framework unless a verified blocker requires it
- lightweight tracing in `agent_events` + Python logging

## Working style
1. Inspect `_reference/README.md` and relevant reference files before reimplementing proven patterns.
2. Build in the phases from `DETOUR_SPEC.md`; do not implement stretch features early.
3. Keep route handlers thin and business logic modular/testable.
4. Never commit secrets or log credentials.
5. Never expose chain-of-thought. Trace only observable model/tool/retrieval/action events.
6. Validate all LLM JSON and tool arguments before persistence.
7. Use parameterized SQL.
8. Prefer the smallest reliable implementation over framework complexity.
9. Run tests after every meaningful phase.
10. If a platform assumption fails, report the exact failure and make the smallest compatible change; do not silently redesign the app.

## First task
Implement **Phase 0 only**, run tests/smoke checks, and report created files, commands run, and blockers. Do not continue to Phase 1 until instructed.
