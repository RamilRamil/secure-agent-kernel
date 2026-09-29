"""Whole-directory rollback detection (feature 006).

The faithful reproduction of the attack 004 left out of scope: an adversary with write
access to memory/<project>/ (but not the key) restores an older, genuinely-signed copy of
the whole directory — records + _chain_head + _writer_lease together. Everything 004 checks
still passes; the out-of-store anchor, which the adversary cannot reach, is what exposes it.

anchor_root is kept in a SEPARATE tmp subtree that the "rollback" never touches — that is
exactly the trust boundary the feature assumes (adversary owns memory/, not the anchor).
"""
import shutil
from pathlib import Path

import pytest

from sr_agent.memory.episodic import EpisodicMemory, MemoryRollbackDetected
from sr_agent.models.memory import MemoryRecord, SourceType
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.lease import WriterLease


SECRET = bytes.fromhex("cd" * 32)
PROJECT = "proj1"


def _principal(project_id: str = PROJECT) -> Principal:
    return Principal(user_id="u", platform="cli", project_id=project_id)


def _finding(i: int, project_id: str = PROJECT, session_id: str = "sess") -> MemoryRecord:
    return MemoryRecord(
        project_id=project_id,
        target="Vault.sol",
        source_type=SourceType.tool_output,
        tool="orchestrator",
        session_id=session_id,
        payload_kind="finding",
        finding={"finding_id": f"H-{i}", "title": "n"},
    )


def _make(tmp_path: Path) -> tuple[EpisodicMemory, WriterLease, Path, Path]:
    memory_root = tmp_path / "mem"
    anchor_root = tmp_path / "anchor"          # the adversary never touches this
    lease = WriterLease(memory_root, SECRET)
    memory = EpisodicMemory(memory_root, SECRET, lease=lease, anchor_root=anchor_root)
    lease.acquire(PROJECT, "sess")
    return memory, lease, memory_root, anchor_root


def _rollback(memory_root: Path, project_id: str, backup: Path) -> None:
    """Restore an older whole copy of memory/<project>/ over the live one."""
    live = memory_root / project_id
    shutil.rmtree(live)
    shutil.copytree(backup, live)


# ── T008 / US1: snapshot fails closed on a rolled-back store ─────────────────────


def test_rollback_is_detected_at_the_snapshot_seam(tmp_path: Path):
    memory, _, memory_root, _ = _make(tmp_path)
    p = _principal()
    for i in range(4):
        memory.write(_finding(i), principal=p)

    # adversary snapshots the project dir at this earlier point (seq 4)
    backup = tmp_path / "backup"
    shutil.copytree(memory_root / PROJECT, backup)

    # the session keeps working; the anchor advances past the backup
    for i in range(4, 8):
        memory.write(_finding(i), principal=p)          # log_max now 8, anchor 8
    memory._invalidate_cache(PROJECT)

    # adversary rolls the whole directory back to seq 4 (records + head + lease)
    _rollback(memory_root, PROJECT, backup)
    memory._invalidate_cache(PROJECT)

    with pytest.raises(MemoryRollbackDetected):
        memory.snapshot(project_id=PROJECT, session_id="sess")


# ── T009 / US1: a store that only grew snapshots normally ───────────────────────


def test_healthy_store_snapshots_without_raising(tmp_path: Path):
    memory, _, _, _ = _make(tmp_path)
    p = _principal()
    for i in range(6):
        memory.write(_finding(i), principal=p)
    snap = memory.snapshot(project_id=PROJECT, session_id="sess")
    assert len(snap.items) == 6


# ── T012 / US2: write is refused onto a rolled-back log, nothing appended ────────


def test_write_is_refused_onto_a_rolled_back_log(tmp_path: Path):
    memory, _, memory_root, _ = _make(tmp_path)
    p = _principal()
    for i in range(4):
        memory.write(_finding(i), principal=p)
    backup = tmp_path / "backup"
    shutil.copytree(memory_root / PROJECT, backup)
    for i in range(4, 8):
        memory.write(_finding(i), principal=p)
    memory._invalidate_cache(PROJECT)

    _rollback(memory_root, PROJECT, backup)
    memory._invalidate_cache(PROJECT)

    target_file = next((memory_root / PROJECT).glob("*.jsonl"))
    before = target_file.stat().st_size
    with pytest.raises(MemoryRollbackDetected):
        memory.write(_finding(99), principal=p)
    assert target_file.stat().st_size == before   # nothing appended


# ── T016 / US3: a forged anchor cannot lock a project out ───────────────────────


def test_forged_anchor_is_ignored_and_does_not_lock_out(tmp_path: Path):
    import json

    memory, _, _, _ = _make(tmp_path)
    p = _principal()
    for i in range(3):
        memory.write(_finding(i), principal=p)

    # an adversary without the key inflates the watermark with a bogus signature
    path = memory._anchor_path(PROJECT)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["watermark"] = 10 ** 9
    doc["hmac"] = "bogus"
    path.write_text(json.dumps(doc), encoding="utf-8")
    memory._invalidate_cache(PROJECT)

    # the forged value never verifies -> treated as absent -> no permanent lockout
    memory.snapshot(project_id=PROJECT, session_id="sess")
    memory.write(_finding(3), principal=p)


# ── T017 / US3: per-project isolation ───────────────────────────────────────────


def test_rollback_of_one_project_does_not_affect_another(tmp_path: Path):
    memory, lease, memory_root, _ = _make(tmp_path)
    other = "proj2"

    for i in range(4):
        memory.write(_finding(i), principal=_principal())
    backup = tmp_path / "backup"
    shutil.copytree(memory_root / PROJECT, backup)
    for i in range(4, 7):
        memory.write(_finding(i), principal=_principal())

    # a second project, its own writer session
    lease.complete(PROJECT, "sess")
    lease.acquire(other, "sess2")
    for i in range(3):
        memory.write(_finding(i, other, "sess2"), principal=_principal(other))

    # roll PROJECT back; proj2 must stay fully usable
    memory._invalidate_cache(PROJECT)
    _rollback(memory_root, PROJECT, backup)
    memory._invalidate_cache(PROJECT)

    with pytest.raises(MemoryRollbackDetected):
        memory.snapshot(project_id=PROJECT, session_id="sess")
    snap = memory.snapshot(project_id=other, session_id="sess2")   # unaffected
    assert len(snap.items) == 3
    memory.write(_finding(9, other, "sess2"), principal=_principal(other))  # still writable


# ── T023 / US3: no anchor material reaches the model ────────────────────────────


def test_watermark_never_appears_in_snapshot_items(tmp_path: Path):
    memory, _, _, _ = _make(tmp_path)
    p = _principal()
    for i in range(3):
        memory.write(_finding(i), principal=p)
    snap = memory.snapshot(project_id=PROJECT, session_id="sess")
    for item in snap.items:
        dumped = item.model_dump()
        assert "watermark" not in dumped
        assert "anchor" not in dumped


# ── T021 / US3: the operator scan reports a rollback, distinct from chain breaks ─


def test_verify_integrity_reports_a_rollback(tmp_path: Path, caplog):
    import logging

    memory, _, memory_root, anchor_root = _make(tmp_path)
    p = _principal()
    for i in range(4):
        memory.write(_finding(i), principal=p)
    backup = tmp_path / "backup"
    shutil.copytree(memory_root / PROJECT, backup)
    for i in range(4, 8):
        memory.write(_finding(i), principal=p)
    memory._invalidate_cache(PROJECT)
    _rollback(memory_root, PROJECT, backup)

    # an operator-run scan (reader role is fine, but keep the anchor_root)
    scanner = EpisodicMemory(memory_root, SECRET, anchor_root=anchor_root)
    with caplog.at_level(logging.WARNING):
        report = scanner.verify_integrity(PROJECT)

    assert report.has_rollback
    assert PROJECT in report.rollbacks
    assert not report.chain_breaks          # the chain itself is intact — this is a rollback
    assert not report.has_invalid           # every record still verifies
    assert any("rollback" in r.message.lower() for r in caplog.records)
