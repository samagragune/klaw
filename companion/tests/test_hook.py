"""Unit tests for the Claude Code hook forwarder (no network, no Claude Code).

Live behaviour against real Claude Code sessions is covered by
integrations_tests/claude_code_live.py; these pin the pure translation and
the fail-open guarantees.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from reliability_companion import hook  # noqa: E402

CAPTURE = {"tool_input": True, "prompts": True, "assistant_messages": True}


def data(event: str, **extra) -> dict:
    return {
        "hook_event_name": event,
        "session_id": "sess-1",
        "prompt_id": "prompt-1",
        "cwd": "/work/proj",
        "permission_mode": "default",
        **extra,
    }


def test_pre_tool_use_becomes_tool_call_started_with_fingerprint():
    event = hook.translate(data("PreToolUse", tool_name="Bash",
                                tool_input={"command": "ls", "api_key": "sk-123"},
                                tool_use_id="tu-1"), CAPTURE)
    assert event["event_type"] == "TOOL_CALL_STARTED"
    assert event["platform"] == "claude_code"
    assert event["session_id"] == "sess-1" and event["task_id"] == "prompt-1"
    p = event["payload"]
    assert p["tool_call_id"] == "tu-1" and len(p["args_fingerprint"]) == 32
    # Secret-looking keys never leave the machine.
    assert p["tool_input"]["api_key"] == "[redacted]"


def test_tool_input_capture_can_be_turned_off_but_fingerprint_stays():
    event = hook.translate(data("PreToolUse", tool_name="Bash", tool_input={"command": "ls"}),
                           {"tool_input": False})
    assert "tool_input" not in event["payload"]
    assert event["payload"]["args_fingerprint"]


def test_identical_inputs_fingerprint_identically_regardless_of_key_order():
    a = hook.fingerprint({"a": 1, "b": [1, 2]})
    b = hook.fingerprint({"b": [1, 2], "a": 1})
    assert a == b


def test_post_tool_use_sends_a_result_fingerprint_not_the_output():
    event = hook.translate(data("PostToolUse", tool_name="Read", tool_use_id="tu-1",
                                tool_response={"content": "secret file body"}), CAPTURE)
    assert event["event_type"] == "TOOL_CALL_COMPLETED"
    assert event["payload"]["result_fingerprint"]
    assert "result_preview" not in event["payload"]


def test_stop_reports_output_and_background_tasks():
    event = hook.translate(data("Stop", last_assistant_message="Done.", stop_hook_active=False,
                                background_tasks=[{"id": "b1"}]), CAPTURE)
    assert event["event_type"] == "TASK_COMPLETED"
    assert event["payload"]["output"] == "Done."
    assert event["payload"]["background_tasks"] == 1


def test_subagent_events_carry_the_actor():
    event = hook.translate(data("SubagentStart", agent_id="a1", agent_type="Explore"), CAPTURE)
    assert event["event_type"] == "SUBAGENT_STARTED"
    assert event["execution_context"]["actor"] == "Explore"


@pytest.mark.parametrize("action,expected", [
    ("ALLOW", None),
    ("BLOCK", "deny"),
    ("REDIRECT", "deny"),
])
def test_pre_tool_decisions_map_to_permission_decisions(action, expected):
    out = hook.pre_tool_output({"action": action, "reason": "r", "feedback": "f", "rule": "x"}, {}, {"timeouts": {}})
    if expected is None:
        # ALLOW never widens Claude Code's own permissions.
        assert out is None
    else:
        assert out["hookSpecificOutput"]["permissionDecision"] == expected
        assert "f" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_warn_adds_context_without_deciding():
    out = hook.pre_tool_output({"action": "WARN", "reason": "risky", "feedback": "careful", "rule": "r"}, {}, {"timeouts": {}})
    assert "permissionDecision" not in out["hookSpecificOutput"]
    assert "careful" in out["hookSpecificOutput"]["additionalContext"]


def test_stop_decision_ends_the_turn():
    out = hook.pre_tool_output({"action": "STOP", "reason": "operator", "rule": "operator.stop"}, {}, {"timeouts": {}})
    assert out["continue"] is False


def test_redirect_at_completion_blocks_stopping_with_feedback():
    out = hook.stop_output({"action": "REDIRECT", "feedback": "run the tests", "rule": "review"})
    assert out == {"decision": "block", "reason": "[Reliability supervisor · review] run the tests"}
    assert hook.stop_output({"action": "ALLOW"}) is None


def test_transcript_usage_is_deduplicated_and_read_incrementally(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "STATE", str(tmp_path / "state"))
    transcript = tmp_path / "t.jsonl"
    entry = lambda rid, out: json.dumps({  # noqa: E731
        "type": "assistant", "requestId": rid,
        "message": {"model": "claude-x", "usage": {
            "input_tokens": 2, "output_tokens": out,
            "cache_read_input_tokens": 100, "cache_creation_input_tokens": 10}},
    })
    # The same request appears once per content block - count it once.
    transcript.write_text("\n".join([entry("r1", 50), entry("r1", 50), entry("r2", 7)]) + "\n")
    d = data("Stop", transcript_path=str(transcript))
    calls = hook.transcript_model_calls(d)
    assert sorted(c["payload"]["completion_tokens"] for c in calls) == [7, 50]
    assert calls[0]["payload"]["prompt_tokens"] == 112
    # Nothing new: nothing reported again.
    assert hook.transcript_model_calls(d) == []
    with transcript.open("a") as fh:
        fh.write(entry("r3", 1) + "\n")
    assert [c["payload"]["request_id"] for c in hook.transcript_model_calls(d)] == ["r3"]


def test_unreadable_transcript_yields_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "STATE", str(tmp_path / "state"))
    assert hook.transcript_model_calls(data("Stop", transcript_path=str(tmp_path / "missing"))) == []


def test_unconfigured_hook_fails_open(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RELIABILITY_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.delenv("RELIABILITY_API_URL", raising=False)
    monkeypatch.delenv("RELIABILITY_INTEGRATION_KEY", raising=False)
    monkeypatch.setattr(hook, "HOME", str(tmp_path))
    monkeypatch.setattr(hook, "LOG", str(tmp_path / "hook.log"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data("PreToolUse", tool_name="Bash"))))
    assert hook.main() == 0
    assert capsys.readouterr().out == ""


def test_unreachable_supervisor_fails_open_and_spools(monkeypatch, tmp_path, capsys):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"api_url": "http://127.0.0.1:9", "integration_key": "rl_int_x"}))
    monkeypatch.setenv("RELIABILITY_CONFIG", str(config))
    monkeypatch.setattr(hook, "HOME", str(tmp_path))
    monkeypatch.setattr(hook, "LOG", str(tmp_path / "hook.log"))
    monkeypatch.setattr(hook, "SPOOL", str(tmp_path / "spool.jsonl"))
    for event in ("PreToolUse", "PostToolUse"):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data(event, tool_name="Bash", tool_input={"command": "ls"}))))
        assert hook.main() == 0
        assert capsys.readouterr().out == ""
    spooled = (tmp_path / "spool.jsonl").read_text().splitlines()
    assert len(spooled) == 2  # delivered later, when the supervisor is back


def test_garbage_input_fails_open(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
    assert hook.main() == 0
    assert capsys.readouterr().out == ""


def test_installer_only_touches_its_own_hooks():
    from reliability_companion import cli

    mine = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "my-guard.sh"}]}]}}
    installed = cli.with_hooks(mine)
    assert any("my-guard.sh" in json.dumps(g) for g in installed["hooks"]["PreToolUse"])
    assert set(cli.CLAUDE_CODE_EVENTS) <= set(installed["hooks"])
    # Installing twice changes nothing.
    assert cli.with_hooks(installed) == installed
    removed = cli.without_hooks(installed)
    assert removed == mine
