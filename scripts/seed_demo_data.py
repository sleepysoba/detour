"""Prewarm Detour's curated destinations through the normal ingestion service."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from detour.config import configure_logging, load_config
from detour.db import init_schema
from detour.services import DetourServices

DEMO_DESTINATIONS = ("Boulder, Colorado", "Austin, Texas", "Miami, Florida")


def seed_demo_destinations(
    ingest: Callable[[str], dict[str, Any]],
    *,
    output: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Ingest each demo independently so one failure cannot undo cache hits."""
    results: list[dict[str, Any]] = []
    for destination in DEMO_DESTINATIONS:
        try:
            result = ingest(destination)
            usable_count = len(result.get("attractions") or [])
            cached = bool(result.get("cached"))
            results.append(
                {
                    "destination": destination,
                    "ok": True,
                    "cached": cached,
                    "usable_attractions": usable_count,
                }
            )
            output(
                f"[OK] {destination}: {usable_count} usable attractions "
                f"({'cache hit' if cached else 'ingested'})"
            )
        except Exception as exc:
            results.append(
                {
                    "destination": destination,
                    "ok": False,
                    "error_type": type(exc).__name__,
                }
            )
            output(f"[FAIL] {destination}: {type(exc).__name__}")
    return results


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    config = load_config()
    configure_logging(config["LOG_LEVEL"])
    services = DetourServices(config)
    init_schema(**services.database_options)
    results = seed_demo_destinations(services.destinations.ingest)
    failures = [result for result in results if not result["ok"]]
    if failures:
        print(f"Demo prewarm completed with {len(failures)} failure(s).")
        return 1
    print("Demo prewarm completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
