"""Spec 020 US3 (FR-007/008): OpenRouter adds NO new dependency and is optional.

The client must reach OpenRouter with the standard library only — no `openai`/
`requests`/`httpx` SDK, nothing new in pyproject. AST-checked at module scope so a
string in a comment never counts.
"""
from __future__ import annotations

import os

os.environ.setdefault("SR_SECRET_KEY", "00" * 32)

import ast
import importlib
from pathlib import Path

from sr_agent.llm_core.openrouter_client import OPENROUTER_MODELS, OpenRouterClient

CLIENT = Path(__file__).resolve().parents[2] / "sr_agent" / "llm_core" / "openrouter_client.py"
FORBIDDEN = {"openai", "requests", "httpx", "anthropic", "google", "aiohttp"}


def _top_level_import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_client_imports_no_new_package() -> None:
    leaked = _top_level_import_roots(CLIENT) & FORBIDDEN
    assert not leaked, f"openrouter_client must be stdlib-only; found {leaked}"


def test_core_module_imports() -> None:
    assert importlib.import_module("sr_agent.llm_core.openrouter_client") is not None


def test_client_not_ready_without_key() -> None:
    assert OpenRouterClient(api_key="", model=OPENROUTER_MODELS[0]).ready() is False
