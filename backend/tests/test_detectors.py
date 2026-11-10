"""Unit tests for the deterministic detectors."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.pricing import estimate_cost
from app.schemas import PolicyConfiguration
from app.supervision.detectors import (
    detect_cost_budget,
    detect_execution_timeout,
    detect_repeated_errors,
    detect_repeated_tool_calls,
    detect_token_budget,
    run_detectors,
)

CONFIG = PolicyConfiguration().model_dump()


def make_execution(**overrides):
    base = dict(
        id="exec-1",
        status="running",
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        event_count=0,
        tool_call_count=0,
        error_count=0,
        prompt_tokens=0,
        completion_tokens=0,
        token_usage=0,
        estimated_cost=None,
        extra={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def make_event(seq: int, event_type: str, payload: dict, *, naive: bool = False):
    stamp = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=f"ev-{seq}",
        seq=seq,
        event_type=event_type,
        payload=payload,
        timestamp=stamp.replace(tzinfo=None) if naive else stamp,
    )


def tool_events(count: int, fingerprint: str = "same", *, naive: bool = False):
    return [
        make_event(
            i, "tool.started", {"tool_name": "search", "args_fingerprint": fingerprint},
            naive=naive,
        )
        for i in range(count)
    ]


def test_repetition_below_threshold_is_not_an_incident():
    assert detect_repeated_tool_calls(make_execution(), tool_events(2), CONFIG) is None


def test_repetition_at_threshold_is_flagged_with_evidence():
    result = detect_repeated_tool_calls(make_execution(), tool_events(3), CONFIG)
    assert result is not None
    assert result.category == "repeated_tool_call"
    assert result.evidence["occurrences"] == 3
    assert len(result.evidence["events"]) == 3
    assert result.suggested_intervention == "pause"


def test_severity_escalates_with_the_number_of_repeats():
    moderate = detect_repeated_tool_calls(make_execution(), tool_events(3), CONFIG)
    extreme = detect_repeated_tool_calls(make_execution(), tool_events(20), CONFIG)
    assert moderate.severity == "high"
    assert extreme.severity == "critical"


def test_naive_timestamps_from_sqlite_do_not_break_arithmetic():
    result = detect_repeated_tool_calls(
        make_execution(), tool_events(4, naive=True), CONFIG
    )
    assert result is not None
    assert result.evidence["window_seconds"] >= 0


def test_a_disabled_threshold_turns_the_detector_off():
    config = {**CONFIG, "repeated_tool_call_threshold": None}
    assert detect_repeated_tool_calls(make_execution(), tool_events(50), config) is None


def test_timeout_fires_only_past_the_limit():
    fresh = make_execution(started_at=datetime.now(timezone.utc) - timedelta(seconds=5))
    assert detect_execution_timeout(fresh, [], {**CONFIG, "max_duration_seconds": 60}) is None

    stale = make_execution(started_at=datetime.now(timezone.utc) - timedelta(seconds=120))
    result = detect_execution_timeout(stale, [], {**CONFIG, "max_duration_seconds": 60})
    assert result is not None
    assert result.evidence["limit_seconds"] == 60
    assert result.suggested_intervention == "cancel"


def test_timeout_ignores_executions_that_already_finished():
    finished = make_execution(
        status="completed",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=600),
    )
    assert detect_execution_timeout(finished, [], CONFIG) is None


def test_token_and_cost_budgets():
    over_tokens = make_execution(token_usage=500, prompt_tokens=400, completion_tokens=100)
    assert detect_token_budget(over_tokens, [], {**CONFIG, "max_tokens": 100}) is not None

    over_cost = make_execution(estimated_cost=2.5)
    result = detect_cost_budget(over_cost, [], {**CONFIG, "max_cost_usd": 1.0})
    assert result.evidence["estimated_cost_usd"] == 2.5


def test_cost_detector_flags_partial_figures_as_understated():
    execution = make_execution(
        estimated_cost=2.0, extra={"unpriced_models": ["mystery-model"]}
    )
    result = detect_cost_budget(execution, [], {**CONFIG, "max_cost_usd": 1.0})
    assert "actual spend is higher" in result.description
    assert result.evidence["unpriced_models"] == ["mystery-model"]


def test_repeated_errors_group_by_target_and_type():
    events = [
        make_event(i, "tool.failed", {"tool_name": "api", "error_type": "Timeout", "error": "t/o"})
        for i in range(3)
    ]
    events.append(
        make_event(9, "tool.failed", {"tool_name": "other", "error_type": "Boom", "error": "b"})
    )
    result = detect_repeated_errors(make_execution(), events, CONFIG)
    assert result.evidence["target"] == "api"
    assert result.evidence["failures"] == 3
    assert result.evidence["last_error"] == "t/o"


def test_mixed_errors_below_threshold_are_not_flagged():
    events = [
        make_event(0, "tool.failed", {"tool_name": "a", "error_type": "X"}),
        make_event(1, "tool.failed", {"tool_name": "b", "error_type": "Y"}),
    ]
    assert detect_repeated_errors(make_execution(), events, CONFIG) is None


def test_run_detectors_can_return_several_findings():
    execution = make_execution(
        token_usage=1_000_000,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=9999),
    )
    categories = {d.category for d in run_detectors(execution, tool_events(10), CONFIG)}
    assert {"repeated_tool_call", "token_budget", "execution_timeout"} <= categories


def test_unknown_models_have_no_price_rather_than_a_guessed_one():
    assert estimate_cost("not-a-real-model", 1000, 1000) is None
    assert estimate_cost(None, 1000, 1000) is None
    assert estimate_cost("demo-model", 1_000_000, 0) == 0.5
