"""Crash consistency of the durable append (feature 003, FR-007a / D15 / SC-010).

The store is append-only JSONL, so a process killed mid-write leaves a partial
line with no newline. Nothing repaired it before: the next append glued itself
onto the torn tail, producing one line that parses as neither record. The retry
then looked identical to a corrupt write, so a resumed session lost a commit it
had no way to know it had lost.

These tests name their kill points explicitly rather than asserting durability in
the abstract -- the contract is only worth what the reproduction is worth.
"""
import os

import pytest

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.memory import MemoryRecord, SourceType

SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"


@pytest.fixture
def memory(tmp_path):
    return EpisodicMemory(memory_root=tmp_path, secret_key=SECRET)


def make_record(target: str, note: str = "n") -> MemoryRecord:
    return MemoryRecord(
        project_id=PROJECT,
        target=target,
        source_type=SourceType.tool_output,
        tool="slither",
        session_id="sess-1",
        payload={"note": note},
        payload_kind="dispatch_payload",
    )


def tear(path):
    """Simulate a process killed after a partial write: no trailing newline."""
    with path.open("a", encoding="utf-8") as f:
        f.write('{"record_id":"half","project_id":"proj1","tar')


def test_torn_tail_is_truncated_and_the_retry_survives(memory, tmp_path):
    """Kill point: mid-write. The retry must be a record, not half of one."""
    memory.write(make_record("Vault.sol", "first"))
    path = tmp_path / PROJECT / "Vault.sol.jsonl"
    tear(path)

    memory.write(make_record("Vault.sol", "retry"))

    loaded = memory.load(PROJECT, "Vault.sol")
    assert [r.payload["note"] for r in loaded] == ["first", "retry"]
    assert [r.log_sequence for r in loaded] == [1, 2]


def test_torn_sequence_is_reused_not_burned(memory, tmp_path):
    """A sequence allocated by a write that never completed must not leave a gap.

    Gap-free is not cosmetic: the snapshot pipeline fails closed on a gap, so a
    burned number would take the whole project out of service after any crash.
    """
    memory.write(make_record("Vault.sol"))
    tear(tmp_path / PROJECT / "Vault.sol.jsonl")

    assert memory.write(make_record("Token.sol")).log_sequence == 2


def test_completed_line_counts_as_committed_when_fsync_fails(memory, tmp_path, monkeypatch):
    """Kill point: line written and flushed, `fsync` failed.

    The bytes are in the file and the signature verifies, so the next start must
    treat the record as committed. Re-appending it would duplicate a commit that
    an exactly-once caller has already been told about.
    """
    memory.write(make_record("Vault.sol", "first"))

    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", _failing_fsync)
    with pytest.raises(OSError):
        memory.write(make_record("Vault.sol", "second"))
    monkeypatch.setattr(os, "fsync", real_fsync)

    fresh = EpisodicMemory(memory_root=tmp_path, secret_key=SECRET)
    loaded = fresh.load(PROJECT, "Vault.sol")
    assert [r.payload["note"] for r in loaded] == ["first", "second"]
    assert fresh.write(make_record("Vault.sol", "third")).log_sequence == 3


def test_torn_append_repaired_before_a_different_target_is_written(memory, tmp_path):
    """Torn tail in one target, restart, append to another (SC-014c).

    Allocation scans the union of the project's files, so a torn tail in a file
    the caller is not writing still has to be repaired first -- otherwise the
    partial line either poisons the scan or hides the number it consumed.
    """
    memory.write(make_record("Vault.sol", "first"))
    tear(tmp_path / PROJECT / "Vault.sol.jsonl")

    fresh = EpisodicMemory(memory_root=tmp_path, secret_key=SECRET)
    written = fresh.write(make_record("Token.sol", "other"))

    assert written.log_sequence == 2
    vault_text = (tmp_path / PROJECT / "Vault.sol.jsonl").read_text(encoding="utf-8")
    assert vault_text.endswith("\n")
    assert vault_text.count("\n") == 1               # the torn tail is gone
    assert [r.payload["note"] for r in fresh.load(PROJECT, "Vault.sol")] == ["first"]


def _failing_fsync(fd):
    raise OSError("simulated fsync failure")
