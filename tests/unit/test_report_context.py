"""Spec 019 US1: the audit-report reader — budget + external-only guard."""
from __future__ import annotations

import os

os.environ.setdefault("SR_SECRET_KEY", "00" * 32)

from pathlib import Path

import pytest

from frontend.backend.sessions import REPORT_BUDGET_CHARS, _AGENT_ROOT, _read_report


def test_reads_external_report(tmp_path):
    p = tmp_path / "audit.md"
    p.write_text("# H-01\nsame-block padding bypass", encoding="utf-8")
    assert "H-01" in _read_report(str(p))


def test_over_budget_is_truncated_with_marker(tmp_path):
    p = tmp_path / "big.md"
    p.write_text("x" * (REPORT_BUDGET_CHARS + 5000), encoding="utf-8")
    out = _read_report(str(p))
    assert out.endswith("…[report truncated]…")
    assert len(out) <= REPORT_BUDGET_CHARS + len("\n…[report truncated]…")


def test_missing_file_raises(tmp_path):
    with pytest.raises(ValueError):
        _read_report(str(tmp_path / "nope.md"))


def test_directory_is_not_a_file(tmp_path):
    with pytest.raises(ValueError):
        _read_report(str(tmp_path))


def test_report_inside_agent_repo_is_rejected():
    # A real file inside the agent repo — target/report material must stay external.
    inside = _AGENT_ROOT / "README.md"
    if not inside.is_file():
        inside = Path(__file__)  # any real file within the repo
    with pytest.raises(ValueError):
        _read_report(str(inside))
