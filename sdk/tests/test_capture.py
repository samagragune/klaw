"""Capture and redaction: what leaves the process, and what must not."""
from __future__ import annotations

from reliability.capture import (
    REDACTED,
    CaptureConfig,
    arg_shape,
    canonical_json,
    fingerprint,
    preview,
    redact,
)


def test_defaults_do_not_capture_payloads():
    config = CaptureConfig()
    assert config.capture_tool_args is False
    assert config.capture_tool_results is False
    assert config.capture_prompts is False
    assert config.capture_completions is False
    # Error text is the one thing on by default: it is what makes a failure
    # diagnosable, and it is the developer's own exception message.
    assert config.capture_error_messages is True


def test_fingerprint_is_stable_across_key_order():
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})


def test_fingerprint_separates_different_arguments():
    assert fingerprint({"q": "one"}) != fingerprint({"q": "two"})


def test_fingerprint_covers_secret_values_so_calls_do_not_collide():
    # Hashing happens before redaction, so two calls differing only by a
    # secret are still distinguishable by the loop detector.
    assert fingerprint({"token": "a"}) != fingerprint({"token": "b"})


def test_redaction_removes_sensitive_keys_at_any_depth():
    config = CaptureConfig()
    value = redact(
        {
            "query": "ok",
            "api_key": "sk-secret",
            "nested": {"Authorization": "Bearer x", "fine": 1},
            "list": [{"password": "hunter2"}],
        },
        config,
    )
    assert value["query"] == "ok"
    assert value["api_key"] == REDACTED
    assert value["nested"]["Authorization"] == REDACTED
    assert value["nested"]["fine"] == 1
    assert value["list"][0]["password"] == REDACTED


def test_non_serialisable_values_degrade_instead_of_raising():
    class Opaque:
        pass

    assert redact({"thing": Opaque()}, CaptureConfig())["thing"] == "<Opaque>"
    assert "Opaque" in canonical_json({"thing": Opaque()})


def test_preview_truncates_and_reports_the_real_length():
    config = CaptureConfig(max_preview_chars=20)
    text = preview("x" * 100, config)
    assert text.startswith("x" * 20)
    assert "100 chars" in text


def test_arg_shape_describes_structure_without_values():
    shape = arg_shape({"q": "secret query", "limit": 5})
    assert shape == {"keys": ["limit", "q"], "count": 2}
    assert "secret" not in str(shape)


def test_metadata_only_preset_captures_nothing():
    config = CaptureConfig.metadata_only()
    assert not any(
        (
            config.capture_tool_args,
            config.capture_tool_results,
            config.capture_prompts,
            config.capture_completions,
            config.capture_output_summary,
            config.capture_error_messages,
        )
    )
