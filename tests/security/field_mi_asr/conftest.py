"""Opt-in gate for live field ASR (skipped in normal pytest runs)."""
from __future__ import annotations

import os

import pytest

# Ensure kernel config import succeeds during collection.
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("SR_SECRET_KEY", os.environ.get("SR_SECRET_KEY", "a" * 64))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "field_asr: live OpenRouter field ASR (opt-in; needs OPENROUTER_API_KEY)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    enabled = os.environ.get("FIELD_ASR", "").strip() in {"1", "true", "yes"}
    has_key = bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
    if enabled and has_key:
        return
    skip = pytest.mark.skip(
        reason="field ASR disabled (set FIELD_ASR=1 and OPENROUTER_API_KEY to run)",
    )
    for item in items:
        if "field_asr" in item.keywords:
            item.add_marker(skip)
