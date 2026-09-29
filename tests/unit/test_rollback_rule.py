"""Rollback anchor — primitive, monotonic bump, and the asymmetric rule (feature 006).

Covers Foundational (T004 primitive, T006 bump) and US3's no-false-positive rule table
(T014). The full-directory rollback attack itself lives in
tests/security/test_memory_rollback.py; here we pin the mechanism and its non-attack
behaviour.
"""
from pathlib import Path

import pytest

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.memory import MemoryRecord, SourceType
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.lease import WriterLease


SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"


def _record(i: int) -> MemoryRecord:
    return MemoryRecord(
        project_id=PROJECT,
        target="Vault.sol",
        source_type=SourceType.tool_output,
        tool="orchestrator",
        session_id="sess",
        payload_kind="finding",
        finding={"finding_id": f"H-{i}", "title": "n"},
    )


def _writer(tmp_path: Path) -> tuple[EpisodicMemory, Principal, WriterLease, Path]:
    memory_root = tmp_path / "mem"
    anchor_root = tmp_path / "anchor"          # OUTSIDE memory_root
    lease = WriterLease(memory_root, SECRET)
    memory = EpisodicMemory(memory_root, SECRET, lease=lease, anchor_root=anchor_root)
    principal = Principal(user_id="u", platform="cli", project_id=PROJECT)
    lease.acquire(PROJECT, "sess")
    return memory, principal, lease, anchor_root


# ── T004: the anchor primitive ─────────────────────────────────────────────────


def test_anchor_signed_value_round_trips(tmp_path: Path):
    memory, _, _, _ = _writer(tmp_path)
    memory._write_anchor(PROJECT, 7)
    assert memory._read_anchor(PROJECT) == 7


def test_absent_anchor_reads_as_none(tmp_path: Path):
    memory, _, _, _ = _writer(tmp_path)
    assert memory._read_anchor(PROJECT) is None


def test_forged_anchor_reads_as_none(tmp_path: Path):
    memory, _, _, anchor_root = _writer(tmp_path)
    memory._write_anchor(PROJECT, 5)
    path = memory._anchor_path(PROJECT)
    import json

    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["watermark"] = 10 ** 9          # inflate without a valid key
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert memory._read_anchor(PROJECT) is None   # bad signature -> absent, never authoritative


def test_anchor_root_inside_memory_root_is_refused(tmp_path: Path):
    memory_root = tmp_path / "mem"
    with pytest.raises(ValueError, match="anchor_root"):
        EpisodicMemory(memory_root, SECRET, anchor_root=memory_root / "inside")


# ── T006: the monotonic bump ────────────────────────────────────────────────────


def test_anchor_tracks_log_max_and_is_monotonic(tmp_path: Path):
    memory, principal, _, _ = _writer(tmp_path)
    for i in range(4):
        memory.write(_record(i), principal=principal)
    # log_max after 4 writes is 4 (log_sequence starts at 1)
    assert memory._read_anchor(PROJECT) == 4
    memory.write(_record(99), principal=principal)
    assert memory._read_anchor(PROJECT) == 5   # advanced, never decreased


# ── T014: the rule never fires on legitimate operation ──────────────────────────


def test_steady_growth_never_raises(tmp_path: Path):
    memory, principal, _, _ = _writer(tmp_path)
    for i in range(50):
        memory.write(_record(i), principal=principal)
        memory.snapshot(project_id=PROJECT, session_id="sess")   # must never raise


def test_crash_lag_anchor_below_log_is_tolerated_and_caught_up(tmp_path: Path):
    memory, principal, _, _ = _writer(tmp_path)
    for i in range(3):
        memory.write(_record(i), principal=principal)          # log_max 3, anchor 3
    # simulate a crash that landed the record+head but not the anchor bump
    memory._write_anchor(PROJECT, 2)                            # anchor lags by one
    memory._invalidate_cache(PROJECT)
    # a read does not raise on V < L
    memory.snapshot(project_id=PROJECT, session_id="sess")
    # and the next write catches the anchor up to the new log_max
    memory.write(_record(3), principal=principal)
    assert memory._read_anchor(PROJECT) == 4
