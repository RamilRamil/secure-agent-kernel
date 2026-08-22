"""Project-global append order (feature 003, FR-006a / D37 / SC-014c, SC-014i).

`004-memory-composition-integrity` already gives every record a `seq` (position
*within one target file*) and a `chain_prev`. Those authenticate composition per
file and cannot order records across targets -- which is exactly what a snapshot
watermark needs, since a session's findings and its dispatch commits live in
different target files. So a record carries **both**: `seq` / `chain_prev` for the
per-target chain, and `log_sequence` for the project-wide append order.

Both are kernel-set and both are inside the signed shape, so neither can be moved
or renumbered without the key.
"""
import json

import pytest

from sr_agent.memory.episodic import EpisodicMemory, MemoryWriteError
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


# ── Allocation ──────────────────────────────────────────────────────────────


def test_empty_project_starts_at_one(memory):
    written = memory.write(make_record("Vault.sol"))
    assert written.log_sequence == 1


def test_monotonic_and_gap_free_across_two_targets(memory):
    """The watermark orders the project log, not one file (D37).

    Interleaving two targets is the case a per-target `seq` cannot serve: both
    files restart their own numbering at 0, so only `log_sequence` can say which
    record was appended first.
    """
    written = [
        memory.write(make_record(target))
        for target in ("Vault.sol", "Token.sol", "Vault.sol", "Token.sol")
    ]
    assert [r.log_sequence for r in written] == [1, 2, 3, 4]
    assert [r.seq for r in written] == [0, 0, 1, 1]


def test_duplicate_sequence_fails_closed(memory, tmp_path):
    """Simulates what an unleased second writer would produce: a well-formed,
    correctly chained record that reuses a number already handed out."""
    memory.write(make_record("Vault.sol"))
    memory.write(make_record("Token.sol"))
    _append_chained(tmp_path / PROJECT / "Token.sol.jsonl", log_sequence=1)

    with pytest.raises(MemoryWriteError) as excinfo:
        memory.write(make_record("Vault.sol"))
    assert "duplicate" in str(excinfo.value)


def test_missing_sequence_fails_closed(memory, tmp_path):
    memory.write(make_record("Vault.sol"))
    _append_chained(tmp_path / PROJECT / "Vault.sol.jsonl", log_sequence=9)

    with pytest.raises(MemoryWriteError) as excinfo:
        memory.write(make_record("Vault.sol"))
    assert "not contiguous" in str(excinfo.value)


def test_a_broken_project_does_not_read_as_an_empty_one(memory, tmp_path):
    """Allocating from a log that does not verify could hand out a number that is
    already in use in the part we cannot read."""
    memory.write(make_record("Vault.sol"))
    memory.write(make_record("Token.sol"))

    path = tmp_path / PROJECT / "Token.sol.jsonl"
    path.write_text("", encoding="utf-8")

    with pytest.raises(MemoryWriteError):
        memory.write(make_record("Vault.sol"))


# ── Coexistence with 004 ────────────────────────────────────────────────────


def test_record_carries_both_orders(memory):
    written = memory.write(make_record("Vault.sol"))
    assert written.seq == 0 and written.log_sequence == 1
    assert "log_sequence" in written.fields_for_hmac()


def test_llm_context_strips_all_three(memory):
    """`log_sequence` joins `hmac` / `seq` / `chain_prev` behind the context wall.

    It is kernel bookkeeping; surfacing it would let a turn reason about, and
    then argue about, its own position in the log.
    """
    written = memory.write(make_record("Vault.sol"))
    context = written.for_llm_context()
    for stripped in ("hmac", "seq", "chain_prev", "log_sequence"):
        assert stripped not in context


def _append_chained(path, *, log_sequence: int) -> None:
    """Append a correctly signed, correctly linked record with a chosen sequence.

    The chain and the signed head both stay intact — the head attests a prefix and
    accepts growth — so the only thing wrong with the resulting log is the
    `log_sequence` itself. That isolates what the allocation check is meant to
    catch from what the composition check already catches.
    """
    from sr_agent.memory import hmac as hmac_module

    lines = path.read_text(encoding="utf-8").splitlines()
    last = MemoryRecord.model_validate(json.loads(lines[-1]))
    record = make_record(last.target, "injected").model_copy(
        update={
            "seq": (last.seq or 0) + 1,
            "chain_prev": last.hmac,
            "log_sequence": log_sequence,
        }
    )
    signed = record.model_copy(
        update={"hmac": hmac_module.sign(record.fields_for_hmac(), SECRET)}
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(signed.model_dump_json() + "\n")
