"""What leaves the agent process, and what never does.

Defaults are deliberately conservative: arguments, results and prompts stay
local unless the developer opts in. A stable fingerprint of tool arguments is
always computed *locally* and only the hash is transmitted, which is what lets
the backend detect identical repeated calls without ever seeing the arguments.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

#: Key names whose values are dropped before anything is serialised, matched
#: case-insensitively against a substring of the key.
DEFAULT_REDACT_KEYS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "private_key",
    "access_key",
    "session",
    "cookie",
)

REDACTED = "[redacted]"
MAX_PREVIEW_CHARS = 500


@dataclass
class CaptureConfig:
    """Per-Supervisor privacy settings.

    ``metadata_only()`` is the strictest preset: names, counts, timings and
    fingerprints, with no payload content at all.
    """

    capture_tool_args: bool = False
    capture_tool_results: bool = False
    capture_prompts: bool = False
    capture_completions: bool = False
    capture_output_summary: bool = False
    capture_error_messages: bool = True
    redact_keys: tuple[str, ...] = DEFAULT_REDACT_KEYS
    max_preview_chars: int = MAX_PREVIEW_CHARS

    @classmethod
    def metadata_only(cls) -> "CaptureConfig":
        return cls(capture_error_messages=False)

    @classmethod
    def full(cls) -> "CaptureConfig":
        """Everything except keys matching ``redact_keys``.

        Use only where the trace store is as trusted as the agent itself.
        """
        return cls(
            capture_tool_args=True,
            capture_tool_results=True,
            capture_prompts=True,
            capture_completions=True,
            capture_output_summary=True,
        )

    def should_redact(self, key: str) -> bool:
        lowered = key.lower()
        return any(marker in lowered for marker in self.redact_keys)


def _coerce(value: Any) -> Any:
    """Make a value JSON-serialisable without raising on exotic objects."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_coerce(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _coerce(v) for k, v in value.items()}
    return f"<{type(value).__name__}>"


def redact(value: Any, config: CaptureConfig) -> Any:
    """Recursively drop values under sensitive keys."""
    if isinstance(value, dict):
        return {
            str(k): (REDACTED if config.should_redact(str(k)) else redact(v, config))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v, config) for v in value]
    return _coerce(value)


def canonical_json(value: Any) -> str:
    """Stable serialisation, so equal arguments always hash equally."""
    return json.dumps(_coerce(value), sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    """Hash of the *unredacted* arguments, computed in the agent process.

    Redaction happens before transmission, not before hashing - otherwise two
    calls differing only in a secret would collide. The hash itself is not
    reversible and is all the backend needs for repetition detection.
    """
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:32]


def preview(value: Any, config: CaptureConfig) -> str:
    """Short, redacted, truncated rendering of a value."""
    if isinstance(value, str):
        text = value
    else:
        text = canonical_json(redact(value, config))
    limit = config.max_preview_chars
    return text if len(text) <= limit else text[:limit] + f"... [{len(text)} chars]"


def arg_shape(args: Any) -> dict[str, Any]:
    """Structural description of arguments, safe under metadata-only capture."""
    if isinstance(args, dict):
        return {"keys": sorted(str(k) for k in args), "count": len(args)}
    if isinstance(args, (list, tuple)):
        return {"type": "sequence", "count": len(args)}
    return {"type": type(args).__name__}
