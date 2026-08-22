"""The sole production caller of pack.dispatch is KernelActionExecutor (FR-018).

A second caller is a second place the commit/pending/checkpoint protocol can be
got wrong, which is how chat and batch drifted apart. The scan is AST-based so a
comment mentioning `.dispatch(` does not count, and a new helper that calls it
fails this test without anyone editing a file list.
"""
from __future__ import annotations

import ast
from pathlib import Path

SR_AGENT = Path(__file__).resolve().parents[2] / "sr_agent"
ALLOWED = {"sr_agent/orchestrator/executor.py"}


def _production_files() -> list[Path]:
    out = []
    for path in SR_AGENT.rglob("*.py"):
        parts = path.relative_to(SR_AGENT).parts
        if parts[0] == "packs" or path.name.startswith("test_"):
            continue
        out.append(path)
    return out


def _dispatch_callers() -> list[str]:
    found = []
    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "dispatch":
                rel = "sr_agent/" + "/".join(path.relative_to(SR_AGENT).parts)
                found.append(f"{rel}:{node.lineno}")
    return found


def test_only_the_executor_calls_pack_dispatch():
    callers = _dispatch_callers()
    unexpected = [c for c in callers if not c.startswith(tuple(ALLOWED))]
    assert unexpected == [], (
        "CapabilityPack.dispatch may only be called from KernelActionExecutor; "
        f"found {unexpected}"
    )
    assert any(c.startswith("sr_agent/orchestrator/executor.py") for c in callers), (
        "KernelActionExecutor must be the one production dispatch caller"
    )
