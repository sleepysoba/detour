from unittest.mock import patch

from detour.tracing import TraceService, safe_summary


def test_safe_summary_removes_secret_and_reasoning_fields_and_bounds_text():
    summary = safe_summary(
        {
            "city": "Boulder",
            "token": "never-store-this",
            "chain_of_thought": "never-store-this-either",
            "result": "x" * 800,
        }
    )

    assert summary["city"] == "Boulder"
    assert "token" not in summary
    assert "chain_of_thought" not in summary
    assert len(summary["result"]) == 500


def test_trace_insert_is_parameterized():
    with patch("detour.tracing.run_write", return_value=1) as run_write:
        TraceService(database_url="postgresql://configured").record(
            trace_id="trace-1",
            event_type="MODEL_REQUEST",
            status="started",
            input_summary={"message_count": 2},
            model="test-model",
        )

    sql, params = run_write.call_args.args
    assert "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)" in sql
    assert params[0] == "trace-1"
    assert params[3] == "MODEL_REQUEST"


def test_safe_summary_never_keeps_hidden_reasoning_fields():
    result = safe_summary(
        {
            "tool_name": "search_attractions",
            "reasoning": "private model reasoning",
            "chain_of_thought": "private",
            "output": {"count": 8},
        }
    )

    assert result == {"tool_name": "search_attractions", "output": {"count": 8}}
