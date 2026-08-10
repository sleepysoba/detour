"""Explicit Databricks Llama function-calling loop for trip repair."""

from __future__ import annotations

import json
import logging
from time import perf_counter
from typing import Any
from uuid import uuid4

from detour.llm import LLMService, ToolCallError, _assistant_tool_message
from detour.repairs import RepairValidationError
from detour.scenarios import normalize_scenario
from detour.tools import REPAIR_TOOL_DEFINITIONS, RepairToolbox
from detour.tracing import TraceService

logger = logging.getLogger(__name__)

MAX_AGENT_ITERATIONS = 7
MAX_SAVE_ATTEMPTS = 2
REQUIRED_BEFORE_SAVE = {
    "get_trip_state",
    "get_trip_resilience",
    "search_attractions",
    "evaluate_candidate",
}


def safe_tool_error_summary(exc: Exception) -> str:
    """Expose only short validation contract feedback, never provider/database details."""
    if not isinstance(exc, (RepairValidationError, ToolCallError, ValueError, KeyError)):
        return f"{type(exc).__name__} while executing tool"
    message = (str(exc) or type(exc).__name__).splitlines()[0].strip()
    return message[:180]


class RepairAgent:
    """Let Llama choose among narrow tools while deterministic services enforce facts."""

    def __init__(self, *, llm: LLMService, toolbox: RepairToolbox, traces: TraceService):
        self.llm = llm
        self.toolbox = toolbox
        self.traces = traces

    def _record(self, *, trace_id: str, trip_id: int, **event: Any) -> None:
        self.traces.record_safe(
            trace_id=trace_id,
            trip_id=trip_id,
            model=self.llm.model,
            **event,
        )

    def run(self, trip_id: int, scenario: str | None) -> dict[str, Any]:
        normalized_scenario = normalize_scenario(scenario)
        scenario_name = normalized_scenario or "LIVE"
        trace_id = str(uuid4())
        called_tools: set[str] = set()
        tool_call_count = 0
        model_call_count = 0
        save_attempt_count = 0
        validation_failures: list[str] = []
        fallback_reason: str | None = None
        used_deterministic_fallback = False
        proposal: dict[str, Any] | None = None
        self._record(
            trace_id=trace_id,
            trip_id=trip_id,
            event_type="AGENT_STARTED",
            status="started",
            input_summary={"scenario": scenario_name, "max_actions": 3},
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are Detour's repair planner. Use tools for every fact and calculation. "
                    "You must inspect trip state and scenario resilience, search real alternatives, "
                    "evaluate candidates, then save exactly one pending proposal. Optimize in order: "
                    "repair the lowest-score AT_RISK item first, then vulnerable CAUTION items; choose "
                    "the largest deterministic condition-score improvement; minimize changes; preserve "
                    "preferences and pace. Use 1-3 actions and never reuse a scheduled attraction. "
                    "Prefer moving or swapping when slot conditions differ; otherwise replace only "
                    "vulnerable items with retrieved destination attractions. Never invent IDs, weather, "
                    "scores, settings, or apply a repair. Use the exact tool action schema. Stored metadata "
                    "and deterministic scores will generate the final factual rationale. Do not "
                    "provide chain-of-thought. After save succeeds, give only a short confirmation."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Investigate and create a pending repair proposal for trip {trip_id} under "
                    f"the clearly simulated {scenario_name} conditions."
                ),
            },
        ]

        try:
            for iteration in range(MAX_AGENT_ITERATIONS):
                self._record(
                    trace_id=trace_id,
                    trip_id=trip_id,
                    event_type="MODEL_REQUEST",
                    status="started",
                    input_summary={
                        "iteration": iteration + 1,
                        "message_count": len(messages),
                        "tool_count": len(REPAIR_TOOL_DEFINITIONS),
                    },
                )
                choice = None
                if iteration == 0:
                    choice = {"type": "function", "function": {"name": "get_trip_state"}}
                message, duration_ms = self.llm.create_message(
                    messages, tools=REPAIR_TOOL_DEFINITIONS, tool_choice=choice
                )
                model_call_count += 1
                tool_calls = list(getattr(message, "tool_calls", None) or [])
                self._record(
                    trace_id=trace_id,
                    trip_id=trip_id,
                    event_type="MODEL_RESPONSE",
                    status="ok",
                    duration_ms=duration_ms,
                    output_summary={
                        "iteration": iteration + 1,
                        "tool_call_count": len(tool_calls),
                        "has_text": bool(getattr(message, "content", None)),
                    },
                )
                if not tool_calls:
                    if proposal is not None:
                        break
                    messages.append(
                        {
                            "role": "user",
                            "content": "Continue using the required tools and save one valid pending proposal.",
                        }
                    )
                    continue

                messages.append(_assistant_tool_message(message, tool_calls))
                for call in tool_calls:
                    name = call.function.name
                    tool_call_count += 1
                    started = perf_counter()
                    try:
                        arguments = json.loads(call.function.arguments)
                        if not isinstance(arguments, dict):
                            raise ToolCallError("Tool arguments must be a JSON object.")
                        if int(arguments.get("trip_id")) != trip_id:
                            raise ToolCallError("Tool trip_id must match the requested trip.")
                        if name == "save_repair_proposal":
                            save_attempt_count += 1
                            missing = REQUIRED_BEFORE_SAVE - called_tools
                            if missing:
                                raise ToolCallError(
                                    "Before saving, call required tools: " + ", ".join(sorted(missing))
                                )
                            supplied_scenario = normalize_scenario(arguments.get("scenario"))
                            if supplied_scenario != normalized_scenario:
                                raise ToolCallError("The proposal scenario must match the requested scenario.")
                        self._record(
                            trace_id=trace_id,
                            trip_id=trip_id,
                            event_type="TOOL_CALLED",
                            status="started",
                            tool_name=name,
                            input_summary={
                                "argument_keys": sorted(arguments),
                                "scenario": arguments.get("scenario"),
                            },
                        )
                        result = self.toolbox.dispatch(name, arguments, trace_id=trace_id)
                        called_tools.add(name)
                        if name == "save_repair_proposal":
                            proposal = result
                        elapsed = round((perf_counter() - started) * 1000)
                        summary = {"result_keys": sorted(result)}
                        if name == "search_attractions":
                            summary["result_count"] = result.get("count")
                        elif name == "get_trip_resilience":
                            summary.update(
                                {
                                    "score": result.get("score"),
                                    "vulnerable_count": result.get("vulnerable_activity_count"),
                                }
                            )
                        elif name == "evaluate_candidate":
                            summary.update(
                                {"attraction_id": result.get("attraction_id"), "score": result.get("score")}
                            )
                        elif name == "save_repair_proposal":
                            summary.update(
                                {"repair_id": result.get("repair_id"), "action_count": len(result.get("actions", []))}
                            )
                        self._record(
                            trace_id=trace_id,
                            trip_id=trip_id,
                            event_type="TOOL_COMPLETED",
                            status="ok",
                            tool_name=name,
                            duration_ms=elapsed,
                            output_summary=summary,
                        )
                        tool_payload = {"ok": True, "result": result}
                    except Exception as exc:
                        elapsed = round((perf_counter() - started) * 1000)
                        safe_error = safe_tool_error_summary(exc)
                        if name == "save_repair_proposal":
                            validation_failures.append(safe_error)
                            if save_attempt_count >= MAX_SAVE_ATTEMPTS:
                                fallback_reason = "two_invalid_save_attempts"
                        self._record(
                            trace_id=trace_id,
                            trip_id=trip_id,
                            event_type="TOOL_COMPLETED",
                            status="error",
                            tool_name=name,
                            duration_ms=elapsed,
                            output_summary={
                                "error_type": type(exc).__name__,
                                "validation_summary": safe_error,
                            },
                        )
                        tool_payload = {"ok": False, "error": safe_error}
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": name,
                            "content": json.dumps(tool_payload, ensure_ascii=False),
                        }
                    )
                    if fallback_reason:
                        break
                if proposal is not None:
                    break
                if fallback_reason:
                    break

            if proposal is None:
                fallback_reason = fallback_reason or "agent_did_not_save_valid_proposal"
                self._record(
                    trace_id=trace_id,
                    trip_id=trip_id,
                    event_type="DETERMINISTIC_FALLBACK_STARTED",
                    status="started",
                    input_summary={
                        "reason": fallback_reason,
                        "save_attempt_count": save_attempt_count,
                    },
                )
                fallback_started = perf_counter()
                proposal = self.toolbox.save_guarded_fallback(
                    trip_id=trip_id,
                    scenario=normalized_scenario,
                    trace_id=trace_id,
                )
                used_deterministic_fallback = True
                self._record(
                    trace_id=trace_id,
                    trip_id=trip_id,
                    repair_run_id=proposal["repair_id"],
                    event_type="DETERMINISTIC_FALLBACK_COMPLETED",
                    status="ok",
                    duration_ms=round((perf_counter() - fallback_started) * 1000),
                    output_summary={
                        "reason": fallback_reason,
                        "repair_id": proposal["repair_id"],
                        "action_count": len(proposal["actions"]),
                    },
                )
            self._record(
                trace_id=trace_id,
                trip_id=trip_id,
                event_type="AGENT_COMPLETED",
                status="ok",
                output_summary={
                    "repair_id": proposal["repair_id"],
                    "tool_calls": tool_call_count,
                    "model_calls": model_call_count,
                    "save_attempts": save_attempt_count,
                    "deterministic_fallback": used_deterministic_fallback,
                },
            )
            return {
                "trace_id": trace_id,
                "proposal": proposal,
                "tool_call_count": tool_call_count,
                "model_call_count": model_call_count,
                "save_attempt_count": save_attempt_count,
                "validation_failures": validation_failures,
                "used_deterministic_fallback": used_deterministic_fallback,
            }
        except Exception as exc:
            logger.warning("repair_agent_failed trip_id=%d error_type=%s", trip_id, type(exc).__name__)
            self._record(
                trace_id=trace_id,
                trip_id=trip_id,
                event_type="AGENT_ERROR",
                status="error",
                output_summary={"error_type": type(exc).__name__},
            )
            raise
