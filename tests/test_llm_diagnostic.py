import json
from types import SimpleNamespace

from detour.llm import LLMService, run_function_call_diagnostic


class FakeTraceService:
    def __init__(self):
        self.events = []

    def record_safe(self, **event):
        self.events.append(event)
        return True


class FakeLLM:
    model = "system.ai.meta-llama-3-3-70b-instruct"

    def __init__(self):
        self.calls = 0

    def create_message(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            function = SimpleNamespace(
                name="get_demo_weather_summary",
                arguments=json.dumps({"city": "Boulder, Colorado"}),
            )
            call = SimpleNamespace(id="call-1", function=function)
            return SimpleNamespace(content=None, tool_calls=[call]), 12
        assert messages[-1]["role"] == "tool"
        return SimpleNamespace(content="The diagnostic tool completed.", tool_calls=[]), 9


def test_explicit_function_call_loop_executes_tool_and_records_safe_events():
    traces = FakeTraceService()

    result = run_function_call_diagnostic(FakeLLM(), traces)

    assert result["tool_name"] == "get_demo_weather_summary"
    assert result["tool_result"]["source"] == "phase1_diagnostic_fixture"
    assert result["final_response"] == "The diagnostic tool completed."
    event_types = [event["event_type"] for event in traces.events]
    assert "TOOL_CALLED" in event_types
    assert "TOOL_COMPLETED" in event_types
    assert event_types[-1] == "AGENT_COMPLETED"


def test_llm_service_basic_chat_uses_configured_model():
    message = SimpleNamespace(content="Detour integration ready.")
    completions = SimpleNamespace(create=lambda **_: SimpleNamespace(choices=[SimpleNamespace(message=message)]))
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    service = LLMService(
        base_url="https://workspace.example/ai-gateway/mlflow/v1",
        token="not-logged",
        model="system.ai.meta-llama-3-3-70b-instruct",
        client=client,
    )

    assert service.chat("Confirm the diagnostic") == "Detour integration ready."
