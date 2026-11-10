"""Token cost estimation.

Rates are USD per 1M tokens. An unknown model returns ``None`` rather than a
guess: the product must never display a fabricated cost. Executions that
contain any unpriced model are flagged ``cost_complete = False`` so the
dashboard can say the figure is partial instead of implying it is exact.

Override or extend the table with a JSON file at ``backend/pricing.json``:

    {"my-model": {"prompt": 1.0, "completion": 3.0}}
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# USD per 1M tokens.
DEFAULT_RATES: dict[str, dict[str, float]] = {
    "gemini-2.0-flash": {"prompt": 0.10, "completion": 0.40},
    "gemini-2.5-flash": {"prompt": 0.30, "completion": 2.50},
    "gemini-2.5-pro": {"prompt": 1.25, "completion": 10.00},
    "gpt-4o": {"prompt": 2.50, "completion": 10.00},
    "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60},
    "claude-sonnet-4-5": {"prompt": 3.00, "completion": 15.00},
    "claude-haiku-4-5": {"prompt": 1.00, "completion": 5.00},
    # Used by the bundled demo agents so their cost figures are real
    # arithmetic over real token counts rather than invented numbers.
    "demo-model": {"prompt": 0.50, "completion": 1.50},
}

_OVERRIDE_PATH = Path(__file__).resolve().parent.parent / "pricing.json"


def _load_rates() -> dict[str, dict[str, float]]:
    rates = dict(DEFAULT_RATES)
    if _OVERRIDE_PATH.exists():
        try:
            rates.update(json.loads(_OVERRIDE_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            logger.exception("ignoring unreadable pricing override at %s", _OVERRIDE_PATH)
    return rates


RATES = _load_rates()


def estimate_cost(model: str | None, prompt_tokens: int, completion_tokens: int) -> float | None:
    """USD cost for one LLM call, or ``None`` when the model has no known price."""
    if not model:
        return None
    rate = RATES.get(model)
    if rate is None:
        return None
    return (
        prompt_tokens * rate["prompt"] + completion_tokens * rate["completion"]
    ) / 1_000_000
