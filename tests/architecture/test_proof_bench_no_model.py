"""Invariant: the proof-eval SCORING path performs no model call (feature 026 FR-007/FR-013).

The instrument must never inflate its own number. A model anywhere in scoring would let the verified
fraction be argued up — the exact failure model-as-judge represents, rejected for the discovery
benchmark for the same reason. Only `run_case` (the harness subprocess seam) may reach a model, and it
does so via `subprocess`, never an in-process client.

Structural + offline, mirroring `test_verification_no_model.py`.
"""
from __future__ import annotations

import ast
from pathlib import Path

_MODULE = Path(__file__).resolve().parents[2] / "scripts" / "proof_bench.py"

_CLIENT_NAMES = {"LocalClient", "GeminiClient", "OpenRouterClient",
                 "build_generation_client", "_generate_with_tool_calls"}
_MODEL_CALLS = {"generate", "warm", "available"}

# The pure scoring functions — none of these may touch a model.
_SCORING_FUNCS = {"credible_interval", "_betai", "_betacf", "_beta_ppf",
                  "build_funnel", "_stage_of", "compare", "score", "render"}


def _tree():
    return ast.parse(_MODULE.read_text(encoding="utf-8"))


def test_module_imports_no_model_client():
    """proof_bench imports no LLM client at module level — scoring is pure arithmetic + text."""
    tree = _tree()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name.split(".")[-1] for a in node.names)
    leaked = imported & _CLIENT_NAMES
    assert not leaked, f"proof_bench.py imports model client(s): {leaked}"


def test_scoring_functions_make_no_model_call():
    """Each pure scoring function's body invokes no generation client — only `run_case` may (via
    subprocess). A client import or a `.generate()` in scoring must fail this test."""
    tree = _tree()
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name in _SCORING_FUNCS}
    assert set(funcs) == _SCORING_FUNCS, f"scoring functions not found: {_SCORING_FUNCS - set(funcs)}"
    for name, fn in funcs.items():
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr in _MODEL_CALLS:
                    raise AssertionError(f"{name} calls .{node.func.attr}() — a model in scoring")
                if isinstance(node.func, ast.Name) and node.func.id in _CLIENT_NAMES:
                    raise AssertionError(f"{name} calls {node.func.id}() — a model in scoring")
