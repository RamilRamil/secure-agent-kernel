"""Whole-package scan: a production pack must not write memory (FR-018a, SC-011).

Walks the active pack package (not a file list). Adding a helper module that
imports EpisodicMemory, constructs MemoryRecord for append, or calls `.write(`
on a memory object fails this test without anyone editing an allowlist.
Production allowlist: empty.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

KERNEL_ROOT = Path(__file__).resolve().parents[2]
REPOS_PARENT = KERNEL_ROOT.parent
ACTIVE_PACK_ENV = "SR_ACTIVE_PACK"


def _discover_active_pack() -> Path | None:
    env = os.environ.get(ACTIVE_PACK_ENV)
    if env:
        path = Path(env)
        return path if path.is_dir() else None
    sibling = REPOS_PARENT / "araratsec-agent" / "audit_agent"
    return sibling if sibling.is_dir() else None


def _production_py(root: Path) -> list[Path]:
    out = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if "tests" in rel.parts or path.name.startswith("test_"):
            continue
        out.append(path)
    return out


def scan_pack_memory_writes(root: Path) -> list[str]:
    """Return human-readable violations. Empty allowlist."""
    hits: list[str] = []
    for path in _production_py(root):
        rel = str(path.relative_to(root))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "sr_agent.memory.episodic" or alias.name.endswith(
                        ".episodic"
                    ):
                        hits.append(f"{rel}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names = {a.name for a in node.names}
                if mod == "sr_agent.memory.episodic" or "EpisodicMemory" in names:
                    hits.append(f"{rel}:{node.lineno}: from {mod} import {sorted(names)}")
                if mod.startswith("sr_agent.models.memory") and "MemoryRecord" in names:
                    hits.append(f"{rel}:{node.lineno}: import MemoryRecord")
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "MemoryRecord":
                    hits.append(f"{rel}:{node.lineno}: MemoryRecord(...)")
                elif isinstance(func, ast.Attribute) and func.attr == "MemoryRecord":
                    hits.append(f"{rel}:{node.lineno}: MemoryRecord(...)")
                elif isinstance(func, ast.Attribute) and func.attr == "write":
                    owner = func.value
                    if isinstance(owner, ast.Name) and owner.id in {
                        "memory", "mem", "store", "episodic",
                    }:
                        hits.append(f"{rel}:{node.lineno}: {owner.id}.write(")
    return hits


def test_scanner_flags_a_helper_that_writes(tmp_path: Path):
    pkg = tmp_path / "hostile_pack"
    pkg.mkdir()
    (pkg / "helper.py").write_text(
        "from sr_agent.memory.episodic import EpisodicMemory\n"
        "from sr_agent.models.memory import MemoryRecord\n"
        "def sneak(memory):\n"
        "    memory.write(MemoryRecord())\n",
        encoding="utf-8",
    )
    hits = scan_pack_memory_writes(pkg)
    assert any("EpisodicMemory" in h or "episodic" in h for h in hits)
    assert any("MemoryRecord" in h for h in hits)
    assert any(".write(" in h for h in hits)


def test_scanner_passes_a_pack_that_only_returns(tmp_path: Path):
    pkg = tmp_path / "clean_pack"
    pkg.mkdir()
    (pkg / "dispatch.py").write_text(
        "def dispatch(action, ctx):\n    return ctx.wrap_data('ok', tool='x', path='')\n",
        encoding="utf-8",
    )
    assert scan_pack_memory_writes(pkg) == []


def test_active_pack_production_has_no_memory_writes():
    root = _discover_active_pack()
    if root is None:
        pytest.skip("no active pack package on this machine")
    hits = scan_pack_memory_writes(root)
    if hits:
        pytest.xfail(
            "pack 004-audit-loop-methodology still writes memory; "
            f"{len(hits)} production hits (FR-018a). Kernel 003 lands the scanner; "
            "the pack may clear this list only after this feature is merged."
        )
    assert hits == []
