"""Spec 018 US3 (FR-007): the Gemini provider is OPTIONAL and non-breaking.

The google-genai SDK must be a soft, lazily-imported dependency: the module and
the core packages import with the SDK absent, and there is NO top-level `google`
import (only inside the lazy `_sdk()` helper). This env has google-genai absent,
so importing here already proves it; the AST check locks it against regressions.
"""
from __future__ import annotations

import os

os.environ.setdefault("SR_SECRET_KEY", "00" * 32)

import ast
import importlib
from pathlib import Path

from sr_agent.llm_core.gemini_client import SIMPLE_MODELS, GeminiClient

CLIENT = Path(__file__).resolve().parents[2] / "sr_agent" / "llm_core" / "gemini_client.py"


def _top_level_imports(path: Path) -> set[str]:
    """Module-scope imports only (imports nested in a function/def don't count)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in tree.body:  # top level ONLY
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_no_top_level_google_import() -> None:
    tops = {name.split(".")[0] for name in _top_level_imports(CLIENT)}
    assert "google" not in tops, (
        "gemini_client.py must import the google-genai SDK LAZILY (inside _sdk()), "
        "never at module top level — the kernel imports with the SDK absent."
    )


def test_core_modules_import_without_sdk() -> None:
    # These import fine even though google-genai is not installed in this env.
    for mod in ("sr_agent.llm_core.gemini_client",
                "sr_agent.llm_core.chat_reasoning",
                "frontend.backend.model_config"):
        assert importlib.import_module(mod) is not None


def test_client_not_ready_without_key() -> None:
    assert GeminiClient(api_key="", model=SIMPLE_MODELS[0]).ready() is False
