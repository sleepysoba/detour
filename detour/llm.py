"""Databricks Llama chat client and explicit Phase 1 tool-call diagnostic."""

from __future__ import annotations

import json
import logging
from time import perf_counter
from typing import Any
from uuid import uuid4

from detour.tracing import TraceService

logger = logging.getLogger(__name__)

DEMO_TOOL_NAME = "get_demo_weather_summary"
DEMO_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": DEMO_TOOL_NAME,
        "description": "Return a deterministic Phase 1 diagnostic weather summary for a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City to summarize."},
            },
            "required": ["city"],
            "additionalProperties": False,
        },
    },
}


class LLMError(RuntimeError):
    """Normalized model configuration or request failure."""


class ToolCallError(RuntimeError):
    """Raised when the diagnostic tool-call contract is not followed."""


class LLMService:
    """Small OpenAI-compatible client for the configured Databricks chat model."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        model: str,
        timeout_seconds: int = 120,
        client: Any | None = None,
    ):
        if not base_url.strip():
            raise LLMError("DATABRICKS_AI_BASE_URL is required.")
        if not token.strip():
            raise LLMError("DATABRICKS_TOKEN is required.")
        if not model.strip():
            raise LLMError("DATABRICKS_CHAT_MODEL is required.")

        self.model = model.strip()
        if client is None:
            try:
                from openai import OpenAI

                client = OpenAI(
                    api_key=token.strip(),
                    base_url=base_url.rstrip("/"),
                    timeout=timeout_seconds,
                )
            except Exception as exc:
                raise LLMError("Could not configure the Databricks Llama client.") from exc
        self.client = client

    def create_message(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
    ) -> tuple[Any, int]:
        """Create one chat message and return it with observed duration."""
        request: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
        if tools is not None:
            request["tools"] = tools
        if tool_choice is not None:
            request["tool_choice"] = tool_choice

        started = perf_counter()
        try:
            completion = self.client.chat.completions.create(**request)
            message = completion.choices[0].message
        except Exception as exc:
            raise LLMError("Databricks Llama request failed.") from exc
        duration_ms = round((perf_counter() - started) * 1000)
        logger.info("model_request_completed model=%s duration_ms=%d", self.model, duration_ms)
        return message, duration_ms

    def chat(self, user_message: str) -> str:
        """Run a minimal chat completion without exposing configuration."""
        if not isinstance(user_message, str) or not user_message.strip():
            raise LLMError("A non-empty user message is required.")
        message, _ = self.create_message(
            [
                {"role": "system", "content": "Respond concisely and do not reveal hidden reasoning."},
                {"role": "user", "content": user_message.strip()},
            ]
        )
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise LLMError("Databricks Llama returned an empty chat response.")
        return content.strip()


def get_demo_weather_summary(city: str) -> dict[str, Any]:
    """Harmless deterministic tool used only to prove the function-call loop."""
    normalized_city = (city or "").strip()
    if not normalized_city or len(normalized_city) > 120:
        raise ToolCallError("The diagnostic city must be a short non-empty string.")
    return {
        "city": normalized_city,
        "summary": "The deterministic Phase 1 diagnostic tool executed successfully.",
        "source": "phase1_diagnostic_fixture",
    }


def _assistant_tool_message(message: Any, tool_calls: list[Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": getattr(message, "content", None),
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in tool_calls
        ],
    }


def run_function_call_diagnostic(
    llm: LLMService,
    traces: TraceService,
    *,
    city: str = "Boulder, Colorado",
) -> dict[str, Any]:
    """Run an explicit two-model-call tool loop and persist observable events."""
    trace_id = str(uuid4())
    persisted_events = 0

    def record(**event: Any) -> None:
        nonlocal persisted_events
        if traces.record_safe(trace_id=trace_id, model=llm.model, **event):
            persisted_events += 1

    record(
        event_type="AGENT_STARTED",
        status="started",
        input_summary={"diagnostic": "phase1_function_call", "city": city},
    )
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are running an integration diagnostic. Call the supplied weather summary tool "
                "exactly once, then use its result to give a short final confirmation."
            ),
        },
        {"role": "user", "content": f"Please run the diagnostic weather tool for {city}."},
    ]

    try:
        record(
            event_type="MODEL_REQUEST",
            status="started",
            input_summary={"message_count": len(messages), "tool_count": 1},
        )
        first_message, first_duration = llm.create_message(
            messages,
            tools=[DEMO_TOOL_DEFINITION],
            tool_choice={"type": "function", "function": {"name": DEMO_TOOL_NAME}},
        )
        tool_calls = list(getattr(first_message, "tool_calls", None) or [])
        record(
            event_type="MODEL_RESPONSE",
            status="ok",
            output_summary={"tool_call_count": len(tool_calls), "has_text": bool(first_message.content)},
            duration_ms=first_duration,
        )
        if len(tool_calls) != 1 or tool_calls[0].function.name != DEMO_TOOL_NAME:
            raise ToolCallError("Llama did not request the expected diagnostic tool exactly once.")

        tool_call = tool_calls[0]
        try:
            arguments = json.loads(tool_call.function.arguments)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ToolCallError("Llama returned invalid diagnostic tool arguments.") from exc
        if not isinstance(arguments, dict) or set(arguments) != {"city"}:
            raise ToolCallError("Llama returned unexpected diagnostic tool arguments.")

        record(
            event_type="TOOL_CALLED",
            status="started",
            tool_name=DEMO_TOOL_NAME,
            input_summary={"city": arguments["city"]},
        )
        tool_started = perf_counter()
        tool_result = get_demo_weather_summary(arguments["city"])
        tool_duration = round((perf_counter() - tool_started) * 1000)
        record(
            event_type="TOOL_COMPLETED",
            status="ok",
            tool_name=DEMO_TOOL_NAME,
            output_summary={"city": tool_result["city"], "source": tool_result["source"]},
            duration_ms=tool_duration,
        )

        messages.append(_assistant_tool_message(first_message, tool_calls))
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": DEMO_TOOL_NAME,
                "content": json.dumps(tool_result),
            }
        )
        record(
            event_type="MODEL_REQUEST",
            status="started",
            input_summary={"message_count": len(messages), "tool_result_count": 1},
        )
        final_message, final_duration = llm.create_message(messages)
        final_response = getattr(final_message, "content", None)
        if not isinstance(final_response, str) or not final_response.strip():
            raise ToolCallError("Llama did not return a final response after the tool result.")
        record(
            event_type="MODEL_RESPONSE",
            status="ok",
            output_summary={"has_text": True, "response_characters": len(final_response)},
            duration_ms=final_duration,
        )
        record(
            event_type="AGENT_COMPLETED",
            status="ok",
            output_summary={"tool_calls_executed": 1},
        )
        return {
            "trace_id": trace_id,
            "tool_name": DEMO_TOOL_NAME,
            "tool_result": tool_result,
            "final_response": final_response.strip(),
            "persisted_events": persisted_events,
        }
    except Exception as exc:
        record(
            event_type="AGENT_ERROR",
            status="error",
            output_summary={"error_type": type(exc).__name__},
        )
        raise
