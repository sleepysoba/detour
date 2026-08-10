"""Thin Flask routes for Detour's interactive Phase 4 product."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from detour.ask import AskValidationError
from detour.db import LakebaseError, check_connection
from detour.destination import DestinationIngestionError
from detour.embeddings import EmbeddingError
from detour.itinerary import ItineraryValidationError
from detour.llm import LLMError, ToolCallError
from detour.presentation import (
    decorate_repair_preview,
    group_itinerary,
    json_ready,
    trace_payload,
    weather_days,
)
from detour.scenarios import normalize_scenario
from detour.services import DetourServices
from detour.repairs import RepairValidationError
from detour.resilience import ResilienceError
from detour.scenarios import ScenarioValidationError
from detour.tracing import TraceError
from detour.trips import TripValidationError
from detour.weather import OpenMeteoError
from detour.wikimedia import WikimediaError

pages = Blueprint("pages", __name__)

PREFERENCES = ("Outdoors", "Culture", "Food", "Photography", "Relaxed", "Adventure", "Low crowds")
PRESETS = (
    {
        "name": "Boulder Adventure",
        "destination": "Boulder, Colorado",
        "preferences": ["Outdoors", "Photography", "Adventure", "Culture"],
        "pace": "balanced",
        "label": "Trails, views, local culture",
        "recommended": True,
    },
    {
        "name": "Austin Weekend",
        "destination": "Austin, Texas",
        "preferences": ["Food", "Culture", "Outdoors", "Photography"],
        "pace": "balanced",
        "label": "Food, music, outdoor energy",
    },
    {
        "name": "Miami Escape",
        "destination": "Miami, Florida",
        "preferences": ["Food", "Photography", "Relaxed", "Culture"],
        "pace": "balanced",
        "label": "Color, culture, coastal calm",
    },
)

PUBLIC_EXCEPTIONS = (
    AskValidationError,
    DestinationIngestionError,
    EmbeddingError,
    ItineraryValidationError,
    LakebaseError,
    LLMError,
    OpenMeteoError,
    RepairValidationError,
    ResilienceError,
    ScenarioValidationError,
    ToolCallError,
    TraceError,
    TripValidationError,
    WikimediaError,
    ValueError,
)


def _connection_options() -> dict[str, Any]:
    return {
        "database_url": current_app.config["LAKEBASE_URL"],
        "secret_scope": current_app.config["LAKEBASE_SECRET_SCOPE"],
        "secret_key": current_app.config["LAKEBASE_SECRET_KEY"],
        "connect_timeout": current_app.config["LAKEBASE_CONNECT_TIMEOUT"],
    }


def _services() -> Any:
    injected = current_app.config.get("DETOUR_SERVICES")
    if injected is not None:
        return injected
    services = current_app.extensions.get("detour_services")
    if services is None:
        services = DetourServices(dict(current_app.config))
        current_app.extensions["detour_services"] = services
    return services


def _landing_context(*, error: str | None = None, values: dict[str, Any] | None = None) -> dict:
    start = date.today() + timedelta(days=1)
    end = start + timedelta(days=1)
    return {
        "preferences": PREFERENCES,
        "presets": PRESETS,
        "default_start": start.isoformat(),
        "default_end": end.isoformat(),
        "error": error,
        "values": values or {},
    }


def _positive_id(value: int, label: str) -> int:
    if value < 1:
        raise ValueError(f"{label} was not found.")
    return value


def _public_error(exc: Exception, fallback: str) -> str:
    if isinstance(exc, PUBLIC_EXCEPTIONS):
        message = str(exc).splitlines()[0].strip()
        if message:
            return message[:240]
    return fallback


def _json_error(exc: Exception, fallback: str, status: int = 400):
    current_app.logger.warning("request_failed error_type=%s", type(exc).__name__)
    return jsonify(error=_public_error(exc, fallback)), status


def _trip_or_404(trip_id: int) -> tuple[Any, dict[str, Any] | None]:
    _positive_id(trip_id, "Trip")
    services = _services()
    trip = services.trips.get_trip(trip_id)
    return services, trip


def _require_live_snapshot(services: Any, trip_id: int) -> None:
    if services.trips.get_latest_weather_snapshot(trip_id) is None:
        raise ResilienceError(
            "Live conditions are not available for this trip yet. Try again closer to departure."
        )


@pages.get("/")
def index():
    return render_template("index.html", **_landing_context())


@pages.post("/trips")
def create_trip():
    payload = request.get_json(silent=True) if request.is_json else request.form
    payload = payload or {}
    preferences = (
        payload.get("preferences", [])
        if request.is_json
        else request.form.getlist("preferences")
    )
    values = {
        "destination": payload.get("destination", ""),
        "start_date": payload.get("start_date", ""),
        "end_date": payload.get("end_date", ""),
        "preferences": preferences,
        "pace": payload.get("pace", "balanced"),
    }
    try:
        services = _services()
        created = services.trip_creator.create(**values)
        trip_id = int(created["trip"]["id"])
        generated = services.itineraries.generate(trip_id)
        if generated.get("live_conditions_available", True):
            services.resilience.evaluate_trip_resilience(trip_id)
    except Exception as exc:
        current_app.logger.warning("trip_creation_failed error_type=%s", type(exc).__name__)
        message = _public_error(
            exc,
            "We couldn't build that trip right now. Check the destination and dates, then try again.",
        )
        if request.is_json:
            return jsonify(error=message), 400
        return render_template("index.html", **_landing_context(error=message, values=values)), 400

    destination_url = url_for("pages.trip_dashboard", trip_id=trip_id)
    if request.is_json:
        return jsonify(trip_id=trip_id, url=destination_url), 201
    return redirect(destination_url, code=303)


@pages.get("/trips/<int:trip_id>")
def trip_dashboard(trip_id: int):
    try:
        services, trip = _trip_or_404(trip_id)
        if trip is None:
            return render_template("error.html", title="Trip not found", message="This trip is no longer available."), 404
        items = services.trips.get_itinerary(trip_id)
        snapshot = services.trips.get_latest_weather_snapshot(trip_id)
        has_live_conditions = snapshot is not None
        selected_scenario = (
            normalize_scenario(request.args.get("scenario")) if has_live_conditions else None
        )
        live = services.resilience.evaluate_trip_resilience(trip_id) if has_live_conditions else None
        active = (
            services.resilience.evaluate_trip_resilience(trip_id, selected_scenario)
            if selected_scenario and has_live_conditions
            else live
        )
        applied_preview = None
        applied_id = request.args.get("applied", type=int)
        if applied_id and has_live_conditions:
            candidate = services.repairs.get_preview(applied_id)
            if int(candidate["trip_id"]) == trip_id and candidate["status"] == "APPLIED":
                applied_preview = decorate_repair_preview(candidate, services.resilience)
        dashboard = {
            "trip": json_ready(trip),
            "live": json_ready(live),
            "active": json_ready(active),
            "scenario": selected_scenario or "LIVE",
            "days": group_itinerary(items, active),
            "item_count": len(items),
            "weather_days": weather_days(trip, snapshot),
            "applied": applied_preview,
            "live_conditions_available": has_live_conditions,
        }
        return render_template("trip.html", dashboard=dashboard)
    except Exception as exc:
        current_app.logger.warning("dashboard_failed trip_id=%d error_type=%s", trip_id, type(exc).__name__)
        return render_template(
            "error.html",
            title="Trip unavailable",
            message=_public_error(exc, "We couldn't load this trip right now. Please try again."),
        ), 400


@pages.get("/api/trips/<int:trip_id>/resilience")
def trip_resilience(trip_id: int):
    try:
        _positive_id(trip_id, "Trip")
        scenario = normalize_scenario(request.args.get("scenario"))
        services = _services()
        _require_live_snapshot(services, trip_id)
        result = services.resilience.evaluate_trip_resilience(trip_id, scenario)
        return jsonify(json_ready(result))
    except Exception as exc:
        return _json_error(exc, "We couldn't evaluate this trip right now.")


@pages.post("/api/trips/<int:trip_id>/repair")
def repair_trip(trip_id: int):
    try:
        _positive_id(trip_id, "Trip")
        payload = request.get_json(silent=True) or {}
        scenario = normalize_scenario(payload.get("scenario"))
        services = _services()
        _require_live_snapshot(services, trip_id)
        result = services.agent.run(trip_id, scenario)
        proposal = decorate_repair_preview(result["proposal"], services.resilience)
        return jsonify(
            {
                "trace_id": result["trace_id"],
                "proposal": proposal,
                "agent": {
                    "tool_call_count": result["tool_call_count"],
                    "model_call_count": result["model_call_count"],
                    "used_deterministic_fallback": result["used_deterministic_fallback"],
                    "validation_failures": result["validation_failures"],
                },
            }
        ), 201
    except Exception as exc:
        return _json_error(exc, "Detour couldn't create a safe repair. Your itinerary was not changed.")


@pages.get("/api/repairs/<int:repair_id>")
def repair_preview(repair_id: int):
    try:
        _positive_id(repair_id, "Repair")
        services = _services()
        preview = services.repairs.get_preview(repair_id)
        return jsonify(decorate_repair_preview(preview, services.resilience))
    except Exception as exc:
        return _json_error(exc, "That repair proposal is unavailable.", 404)


@pages.post("/api/repairs/<int:repair_id>/apply")
def apply_repair(repair_id: int):
    try:
        _positive_id(repair_id, "Repair")
        services = _services()
        result = services.repairs.apply_repair(repair_id)
        return jsonify(json_ready(result))
    except Exception as exc:
        return _json_error(exc, "The repair could not be applied. Your itinerary was not changed.", 409)


@pages.get("/api/traces/<trace_id>")
def agent_trace(trace_id: str):
    normalized = str(trace_id or "").strip()
    if not normalized or len(normalized) > 80:
        return jsonify(error="Trace was not found."), 404
    try:
        events = _services().traces.get_events(normalized)
        if not events:
            return jsonify(error="Trace was not found."), 404
        return jsonify(trace_payload(normalized, events))
    except Exception as exc:
        return _json_error(exc, "The agent trace is temporarily unavailable.")


@pages.post("/api/trips/<int:trip_id>/ask")
def ask_detour(trip_id: int):
    try:
        _positive_id(trip_id, "Trip")
        payload = request.get_json(silent=True) or {}
        services = _services()
        _require_live_snapshot(services, trip_id)
        result = services.ask.answer(
            trip_id,
            payload.get("question", ""),
            normalize_scenario(payload.get("scenario")),
        )
        return jsonify(json_ready(result))
    except Exception as exc:
        return _json_error(exc, "Ask Detour couldn't answer right now. Please try again.")


@pages.get("/health")
def health():
    """Return application and database health without configuration details."""
    try:
        database = check_connection(**_connection_options())
    except LakebaseError:
        current_app.logger.warning("Lakebase health check failed.")
        return jsonify(status="degraded", application="ok", database="unavailable"), 503

    if not database["ok"]:
        return jsonify(status="degraded", application="ok", database="unavailable"), 503
    return jsonify(status="ok", application="ok", database="ok")
