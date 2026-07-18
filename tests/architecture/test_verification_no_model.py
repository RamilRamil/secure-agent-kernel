"""Invariant: the falsification-verification path performs NO model call (feature 025 FR-011).

This is a principle, not a preference. `mutation_verify` exists to be trustworthy exactly when the
model is NOT — it is the differential check that catches a proof the model was happy with. A model
anywhere in this path would defeat its reason to exist, the same reason model-as-judge was rejected
for the discovery benchmark. Principles in this repo are guarded by a test; `test_harness_sandbox_only.py`
sets the precedent for this exact shape (AST over the source, not a runtime probe).

Scope: `scripts/patch_reconstruct.py` (the reconstruction module) must import no LLM client and call
no `generate`; and `mutation_verify` / `_resolve_fix` in `scripts/poc_queue_runner.py` must not reach
a generation client. The check is deliberately structural and offline.
"""
from __future__ import annotations

import ast
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

# Names that would indicate a model is being invoked.
_MODEL_CALLS = {"generate", "warm", "available"}
_CLIENT_IMPORTS = {"LocalClient", "GeminiClient", "OpenRouterClient",
                   "build_generation_client", "_generate_with_tool_calls"}


def test_patch_reconstruct_imports_no_model_client():
    """The reconstruction module is pure text processing — it must not even import a client."""
    src = (_SCRIPTS / "patch_reconstruct.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name.split(".")[-1] for a in node.names)
    leaked = imported & _CLIENT_IMPORTS
    assert not leaked, f"patch_reconstruct.py imports model client(s): {leaked}"
    # and calls no generate/warm/available anywhere
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not (called & _MODEL_CALLS), f"patch_reconstruct.py calls model method(s): {called & _MODEL_CALLS}"


def test_verification_functions_reach_no_generation_call():
    """`mutation_verify` and `_resolve_fix` (and the helpers they call directly) must not invoke a
    generation client. We walk their bodies for any `<x>.generate(...)` / `build_generation_client`."""
    src = (_SCRIPTS / "poc_queue_runner.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name in {"mutation_verify", "_resolve_fix"}}
    assert set(funcs) == {"mutation_verify", "_resolve_fix"}, "verification functions not found"
    for name, fn in funcs.items():
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                # <obj>.generate(...) / .warm(...) / .available(...)
                if isinstance(node.func, ast.Attribute) and node.func.attr in _MODEL_CALLS:
                    raise AssertionError(f"{name} calls .{node.func.attr}() — a model in the verify path")
                # build_generation_client(...) / _generate_with_tool_calls(...)
                if isinstance(node.func, ast.Name) and node.func.id in _CLIENT_IMPORTS:
                    raise AssertionError(f"{name} calls {node.func.id}() — a model in the verify path")
