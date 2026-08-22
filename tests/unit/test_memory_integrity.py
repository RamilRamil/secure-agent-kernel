"""Memory record integrity tests (US5, T046, SC-004).

Records tampered directly in the JSONL store are detected at read time:
load() silently drops them, verify_integrity() counts them.
"""
import json
import pytest
from pathlib import Path

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.memory import MemoryRecord, SourceType

SECRET = b"test-secret-key-32-bytes-exactly!"


@pytest.fixture
def memory(tmp_path: Path) -> EpisodicMemory:
    return EpisodicMemory(tmp_path, SECRET)


def _make_record(finding_id: str = "H-1") -> MemoryRecord:
    return MemoryRecord(
        project_id="proj1",
        target="Vault.sol",
        source_type=SourceType.tool_output,
        tool="run_slither",
        session_id="s",
        finding={
            "finding_id": finding_id, "severity": "high",
            "location": "Vault.sol:1", "function_name": "f",
        },
    )


def _rewrite_first_line(memory: EpisodicMemory, mutate) -> None:
    path = memory._path("proj1", "Vault.sol")
    lines = path.read_text().splitlines()
    data = json.loads(lines[0])
    mutate(data)
    lines[0] = json.dumps(data)
    path.write_text("\n".join(lines) + "\n")


def test_valid_record_passes(memory):
    memory.write(_make_record())
    assert len(memory.load("proj1", "Vault.sol")) == 1
    report = memory.verify_integrity("proj1")
    assert (report.total, report.valid, report.invalid) == (1, 1, 0)
    assert not report.has_invalid


def test_tampered_content_dropped(memory):
    memory.write(_make_record())
    _rewrite_first_line(memory, lambda d: d.update(project_id="evil"))
    assert memory.load("proj1", "Vault.sol") == []
    report = memory.verify_integrity("proj1")
    assert report.invalid == 1 and report.valid == 0


def test_tampered_hmac_dropped(memory):
    memory.write(_make_record())
    _rewrite_first_line(memory, lambda d: d.update(hmac="0" * 64))
    assert memory.load("proj1", "Vault.sol") == []
    assert memory.verify_integrity("proj1").invalid == 1


def test_missing_hmac_dropped(memory):
    memory.write(_make_record())
    _rewrite_first_line(memory, lambda d: d.pop("hmac", None))
    assert memory.load("proj1", "Vault.sol") == []
    assert memory.verify_integrity("proj1").invalid == 1


# ── Records predating the log_sequence field (feature 003, FR-006b / D39) ────
# Adding a kernel-set field to the signed shape means records written before it
# no longer verify. `004` already settled what happens then, and this feature
# adopts that verbatim instead of adding a version-aware reader: a re-signing
# migration is REJECTED, because it would stamp a valid signature onto records
# whose provenance can no longer be checked -- which is precisely the forgery the
# signature exists to prevent. The cost is accepted and named: a session that
# predates the change is not resumable, and is completed and started anew.


def test_records_without_log_sequence_read_as_empty(memory):
    memory.write(_make_record())
    _rewrite_first_line(memory, lambda d: d.pop("log_sequence", None))
    assert memory.load("proj1", "Vault.sol") == []


def test_legacy_target_starts_a_fresh_chain_and_nothing_is_re_signed(memory, tmp_path):
    """The store recovers by moving forward, never by re-signing the past."""
    memory.write(_make_record())
    _rewrite_first_line(memory, lambda d: d.pop("log_sequence", None))
    (tmp_path / "proj1" / "_chain_head.json").unlink()

    written = memory.write(_make_record("H-2"))

    assert (written.seq, written.log_sequence) == (0, 1)
    assert [r.finding["finding_id"] for r in memory.load("proj1", "Vault.sol")] == ["H-2"]
    # The unverifiable line is still on disk, untouched -- not repaired, not removed.
    assert memory.verify_integrity("proj1").invalid == 1
