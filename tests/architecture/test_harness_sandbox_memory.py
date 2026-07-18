"""Feature 027 US1/US3: the STANDALONE harness raises its sandbox memory above the kernel default,
while the SECURE interactive agent does not.

A live proof-eval run OOM-killed solc (`signal: 9`) because the target compiles with `via_ir` (needs
GBs) but the sandbox capped memory at 512m. The harness now builds its sandbox with an env-tunable,
higher ceiling — ONLY at its own construction site. This guards both directions: the harness must
stay raised (a silent revert reintroduces the OOM), and the secure agent must stay at 512m (its tight
posture is deliberate). Memory is a DoS knob, not an isolation invariant — no security property moves
(that is guarded separately by test_harness_sandbox_only.py).

Offline, structural — mirrors test_harness_sandbox_only.py.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import scripts.poc_queue_runner as pqr
from scripts.poc_queue_runner import _harness_sandbox
from sr_agent.tools.sandbox import DockerSandbox

_ROOT = Path(__file__).resolve().parents[2]
_SECURE_AGENT_FILES = [
    _ROOT / "sr_agent" / "packs" / "audit" / "pipeline.py",
    _ROOT / "sr_agent" / "orchestrator" / "loop.py",
]


def _mem_bytes(s: str) -> int:
    """Parse a Docker memory string like '512m' / '6g' to bytes."""
    s = s.strip().lower()
    units = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
    if s[-1] in units:
        return int(float(s[:-1]) * units[s[-1]])
    return int(s)


# ── US1: the harness is raised above the kernel default ──────────────────────

def test_harness_sandbox_raised_above_kernel_default():
    kernel_default = DockerSandbox().memory_limit          # the kernel default, unchanged
    harness = _harness_sandbox().memory_limit
    assert _mem_bytes(harness) > _mem_bytes(kernel_default), (
        f"harness sandbox ({harness}) must exceed the kernel default ({kernel_default}) — "
        f"a cold via_ir build OOM-kills solc at the default")


def test_harness_memory_is_env_tunable(monkeypatch):
    monkeypatch.delenv("SR_SANDBOX_MEMORY", raising=False)
    assert _harness_sandbox().memory_limit == pqr._HARNESS_SANDBOX_MEMORY_DEFAULT  # default when unset
    monkeypatch.setenv("SR_SANDBOX_MEMORY", "8g")
    assert _harness_sandbox().memory_limit == "8g"                                  # honored when set


# ── US3: the secure agent + kernel default are NOT raised ────────────────────

def test_kernel_default_unchanged():
    # the secure agent constructs bare DockerSandbox() → gets this default
    assert DockerSandbox().memory_limit == "512m"


def test_secure_agent_does_not_raise_memory():
    """AST: every DockerSandbox(...) in the secure agent passes NO memory_limit override."""
    for f in _SECURE_AGENT_FILES:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "DockerSandbox"):
                kwargs = {k.arg for k in node.keywords}
                assert "memory_limit" not in kwargs, (
                    f"{f.name}: the secure agent must NOT raise sandbox memory (feature 027 keeps it "
                    f"at the 512m default)")


def test_guard_has_teeth():
    """Sanity: the byte parser orders the two ceilings correctly, so a revert to the default WOULD
    fail test_harness_sandbox_raised_above_kernel_default."""
    assert _mem_bytes("6g") > _mem_bytes("512m")
    assert _mem_bytes("512m") == _mem_bytes("512m")
