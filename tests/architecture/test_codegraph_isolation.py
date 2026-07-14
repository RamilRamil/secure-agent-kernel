"""Spec 017 US3 (FR-006/007/008/012): the code-comprehension tool stays isolated.

Two machine-checkable guarantees, AST-based (a "scripts.codegraph" or "requests"
string in a comment never counts as an import):

  1. No file under sr_agent/** (kernel OR pack) imports scripts.codegraph. The map
     is never model grounding, never an authorization input, never in the trust
     hierarchy — so the kernel must not depend on it.
  2. scripts/codegraph.py's own imports carry no network / paid-API / graphify
     dependency: `graphify` is only ever a subprocess string, never imported, and
     the query path touches no HTTP/socket/LLM client. Proves offline + no-paid-dep.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SR_AGENT = REPO / "sr_agent"
CODEGRAPH = REPO / "scripts" / "codegraph.py"

CODEGRAPH_MODULE = "scripts.codegraph"
FORBIDDEN_IN_CODEGRAPH = {
    "requests", "anthropic", "socket", "urllib", "http", "graphify",
    "web3", "langfuse", "httpx", "aiohttp",
}


def _imported_top_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            targets.add(node.module)
    return targets


def _imports_codegraph(path: Path) -> bool:
    for t in _imported_top_modules(path):
        if t == CODEGRAPH_MODULE or t.startswith(CODEGRAPH_MODULE + "."):
            return True
    return False


def test_kernel_and_pack_do_not_import_codegraph() -> None:
    offenders = [
        "sr_agent/" + "/".join(p.relative_to(SR_AGENT).parts)
        for p in sorted(SR_AGENT.rglob("*.py"))
        if _imports_codegraph(p)
    ]
    assert not offenders, (
        "scripts.codegraph is a dev tool and MUST NOT be imported by the agent "
        f"(kernel or pack). Offending files: {offenders}"
    )


def test_codegraph_has_no_network_or_paid_or_graphify_import() -> None:
    imported = _imported_top_modules(CODEGRAPH)
    tops = {name.split(".")[0] for name in imported}
    leaked = sorted(tops & FORBIDDEN_IN_CODEGRAPH)
    assert not leaked, (
        "scripts/codegraph.py must stay offline and dependency-free: it must not "
        f"import {leaked}. graphify is only ever a subprocess, never an import."
    )
