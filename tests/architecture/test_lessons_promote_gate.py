"""Feature 014 US1 / SC-004: the promotion gate is out-of-band only.

A lesson enters the retrievable corpus ONLY via the human's `sr-agent lessons approve`
CLI. The harness and the orchestrator may `capture`/`retrieve`, but MUST NOT reference or
call `promote` — mirroring the confirmation gate ("the agent process never writes
'approved' — only the out-of-band CLI does") and spec 013's AST-invariant style. A future
edit that wired a `store.promote(...)` into the agent surface fails here.
"""
import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# The KERNEL agent surface — none of these may promote a lesson. The audit harness
# (scripts/poc_queue_runner.py) is guarded separately in Repo B by
# tests/audit/architecture/test_lessons_promote_gate_audit.py; it is not in the kernel
# carve, so it is filtered out here when absent (feature 048).
_GUARDED = [
    p for p in (
        _ROOT / "scripts" / "poc_queue_runner.py",
        *sorted((_ROOT / "sr_agent" / "orchestrator").glob("*.py")),
    ) if p.exists()
]


def _promote_calls(source: str) -> list[int]:
    """Line numbers of any `<expr>.promote(...)` call — the gate the agent must not cross."""
    tree = ast.parse(source)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "promote"
    ]


def test_no_promote_call_on_the_agent_surface():
    for path in _GUARDED:
        calls = _promote_calls(path.read_text(encoding="utf-8"))
        assert calls == [], (
            f"{path.relative_to(_ROOT)} calls .promote() at lines {calls} — lesson "
            f"promotion must be out-of-band (sr-agent lessons approve) only (SC-004)")


def test_guard_would_catch_an_injected_promote():
    """The guard must FAIL on a hypothetical `store.promote(...)` added to the surface —
    otherwise it protects nothing."""
    assert _promote_calls("store.promote('abc123')\n") == [1]
