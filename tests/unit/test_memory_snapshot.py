"""The pack's only read seam (feature 003, FR-009a / FR-009b / FR-007c).

A pack has no memory handle, which leaves it unable to build a projection unless
the kernel hands it prior state. `MemorySnapshot` is that hand-off: an immutable
argument, not a field on `PackContext` and not a callable, because anything
callable is a capability the pack could use at a moment the kernel is not
supervising.

Most of what is tested here is ORDER. The six-step pipeline has two orderings
that look equivalent and are not, and both produce a store that quietly lies:
superseding before cutting lets a later correction rewrite an older snapshot,
and selecting kinds before resolving drops a correction whose own kind the pack
does not consume, so the record it cancels silently survives.
"""
import pytest

from sr_agent.memory.canonical import canonical_bytes
from sr_agent.memory.episodic import (
    EpisodicMemory,
    MemoryCompositionBreak,
    SnapshotCapacityExceeded,
    SnapshotWatermarkError,
)
from sr_agent.models.memory import MemoryRecord, SourceType

SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"


@pytest.fixture
def memory(tmp_path):
    return EpisodicMemory(memory_root=tmp_path, secret_key=SECRET)


def finding(memory, finding_id="F-1", session_id="sess-1", target="Vault.sol"):
    return memory.write(
        MemoryRecord(
            project_id=PROJECT,
            target=target,
            source_type=SourceType.tool_output,
            tool="run_slither",
            session_id=session_id,
            finding={"finding_id": finding_id, "severity": "high"},
        )
    )


def commit(memory, operation_id="op-1", session_id="sess-1", expected_revision=0):
    from sr_agent.models.dispatch import DispatchPayload

    return memory.commit_if_absent(
        project_id=PROJECT,
        target="Vault.sol",
        session_id=session_id,
        tool="run_slither",
        operation_id=operation_id,
        transition_key=f"tk-{operation_id}",
        expected_revision=expected_revision,
        payloads=[DispatchPayload(body={"analyzer": "slither"})],
    )


def response(memory, correlation_id="c-1", session_id="sess-1"):
    return memory.put_external_response_if_absent(
        project_id=PROJECT,
        target="Vault.sol",
        session_id=session_id,
        operation_id="op-9",
        correlation_id=correlation_id,
        body={"decision": "approve"},
    )


def correction(memory, superseded_record_id, session_id="sess-1", target="Vault.sol"):
    """A human correction whose OWN kind the snapshot never hands to the pack.

    This shape is the point of several tests below: if the implementation selects
    kinds before resolving corrections, this record is discarded first and the
    record it cancels survives -- a retraction that silently did nothing.
    """
    return memory.write(
        MemoryRecord(
            project_id=PROJECT,
            target=target,
            source_type=SourceType.human_input,
            session_id=session_id,
            payload_kind="operator_note",
            payload={"reason": "false positive"},
            supersedes=superseded_record_id,
        )
    )


# ── Contents ────────────────────────────────────────────────────────────────


def test_snapshot_carries_all_three_domain_inputs(memory):
    """Bundles alone are not enough: grounding is a finding-to-execution join.

    Findings are separate records, so a snapshot of dispatch commits could never
    let a pack decide whether a finding is grounded.
    """
    finding(memory)
    commit(memory)
    response(memory)

    snapshot = memory.snapshot(project_id=PROJECT, session_id="sess-1")
    assert {item.kind for item in snapshot.items} == {
        "finding", "dispatch_commit", "external_response",
    }


def test_items_are_ordered_by_log_sequence(memory):
    commit(memory)
    finding(memory)
    response(memory)
    snapshot = memory.snapshot(project_id=PROJECT, session_id="sess-1")
    sequences = [item.log_sequence for item in snapshot.items]
    assert sequences == sorted(sequences)


def test_snapshot_is_frozen(memory):
    finding(memory)
    snapshot = memory.snapshot(project_id=PROJECT, session_id="sess-1")
    with pytest.raises(Exception):
        snapshot.as_of_sequence = 99
    assert isinstance(snapshot.items, tuple)


def test_other_sessions_are_excluded(memory):
    finding(memory, "F-1", session_id="sess-1")
    finding(memory, "F-2", session_id="sess-2")
    snapshot = memory.snapshot(project_id=PROJECT, session_id="sess-1")
    assert [i.body["finding_id"] for i in snapshot.items] == ["F-1"]


def test_unverified_records_never_reach_the_pack(memory, tmp_path):
    """An appended forgery is dropped, and dropping it is not a composition break.

    An attacker with write access can add lines but cannot sign them, so they are
    simply not in the walk -- which is why this case must NOT withhold the project
    the way a genuine removal does.
    """
    finding(memory)
    forged = MemoryRecord(
        project_id=PROJECT,
        target="Vault.sol",
        source_type=SourceType.human_input,
        session_id="sess-1",
        finding={"finding_id": "FORGED", "severity": "critical"},
        seq=1,
        chain_prev="0" * 64,
        log_sequence=2,
        hmac="0" * 64,
    )
    with (tmp_path / PROJECT / "Vault.sol.jsonl").open("a", encoding="utf-8") as f:
        f.write(forged.model_dump_json() + "\n")

    snapshot = memory.snapshot(project_id=PROJECT, session_id="sess-1")
    assert [i.body["finding_id"] for i in snapshot.items] == ["F-1"]


# ── Watermark ───────────────────────────────────────────────────────────────


def test_same_watermark_yields_byte_identical_contents(memory):
    """Stability is the whole reason the bound is the append watermark (D28).

    Neither of the appends below moves `session_revision`, so a revision-bounded
    snapshot would silently change while claiming to be the same view.
    """
    finding(memory)
    at_one = memory.snapshot(project_id=PROJECT, session_id="sess-1")

    finding(memory, "F-2")
    response(memory)

    again = memory.snapshot(
        project_id=PROJECT, session_id="sess-1", as_of_sequence=at_one.as_of_sequence
    )
    assert canonical_bytes(again.model_dump(mode="json")) == canonical_bytes(
        at_one.model_dump(mode="json")
    )

    later = memory.snapshot(project_id=PROJECT, session_id="sess-1")
    assert len(later.items) == 3


def test_future_watermark_fails_closed(memory):
    """Not clamped, not served as "everything so far".

    Clamping would let one watermark denote a growing history, which destroys the
    immutability every caller downstream is relying on.
    """
    finding(memory)
    current_max = memory.snapshot(project_id=PROJECT, session_id="sess-1").as_of_sequence

    with pytest.raises(SnapshotWatermarkError) as excinfo:
        memory.snapshot(
            project_id=PROJECT, session_id="sess-1", as_of_sequence=current_max + 1
        )
    message = str(excinfo.value)
    assert str(current_max) in message and str(current_max + 1) in message


def test_a_refused_watermark_is_not_a_deferred_request(memory):
    """The call fails now; it does not wait for the log to reach the value.

    Once the log genuinely grows, that same watermark becomes a legal request and
    is served -- with contents pinned from then on. What must never happen is the
    first call returning a partial view, or the same watermark denoting different
    contents on two reads. Both are checked here.
    """
    finding(memory, "F-1")
    with pytest.raises(SnapshotWatermarkError):
        memory.snapshot(project_id=PROJECT, session_id="sess-1", as_of_sequence=2)

    finding(memory, "F-2")
    served = memory.snapshot(project_id=PROJECT, session_id="sess-1", as_of_sequence=2)
    assert len(served.items) == 2

    finding(memory, "F-3")
    again = memory.snapshot(project_id=PROJECT, session_id="sess-1", as_of_sequence=2)
    assert canonical_bytes(again.model_dump(mode="json")) == canonical_bytes(
        served.model_dump(mode="json")
    )


# ── Pipeline order ──────────────────────────────────────────────────────────


def test_a_later_correction_does_not_rewrite_an_earlier_snapshot(memory):
    """Cut the prefix BEFORE resolving corrections (D32)."""
    target = finding(memory)
    before = memory.snapshot(project_id=PROJECT, session_id="sess-1")

    correction(memory, target.record_id)

    unchanged = memory.snapshot(
        project_id=PROJECT, session_id="sess-1", as_of_sequence=before.as_of_sequence
    )
    assert canonical_bytes(unchanged.model_dump(mode="json")) == canonical_bytes(
        before.model_dump(mode="json")
    )
    assert memory.snapshot(project_id=PROJECT, session_id="sess-1").items == ()


def test_a_correction_works_even_when_its_own_kind_is_not_served(memory):
    """Resolve corrections BEFORE the final kind selection (D36).

    `operator_note` is never handed to the pack. If selection ran first the
    correction would be discarded before it could cancel anything, and the
    retraction would silently do nothing.
    """
    target = finding(memory)
    correction(memory, target.record_id)

    snapshot = memory.snapshot(project_id=PROJECT, session_id="sess-1")
    assert snapshot.items == ()


def test_a_correction_under_a_different_target_still_applies(memory):
    """Resolution is not narrowed by target -- `004` FR-003's threat model."""
    target = finding(memory, "F-1", target="Vault.sol")
    correction(memory, target.record_id, target="Token.sol")
    assert memory.snapshot(project_id=PROJECT, session_id="sess-1").items == ()


def test_a_cross_session_correction_applies_and_is_deliberate(memory):
    """Pinned on purpose (D35), inherited from the shipped `004` FR-007.

    A human correction filed in session B does remove a record from session A's
    projection. Narrowing resolution to a session would let an attacker resurrect
    a record by isolating the file that holds its correction, and that threat
    outranks session tidiness. Session filtering decides what the pack RECEIVES;
    it is not a resolution boundary.
    """
    target = finding(memory, "F-1", session_id="sess-1")
    before = memory.snapshot(project_id=PROJECT, session_id="sess-1")

    correction(memory, target.record_id, session_id="sess-2")

    assert memory.snapshot(project_id=PROJECT, session_id="sess-1").items == ()
    # ...but only from its own sequence onward.
    earlier = memory.snapshot(
        project_id=PROJECT, session_id="sess-1", as_of_sequence=before.as_of_sequence
    )
    assert len(earlier.items) == 1


def test_no_write_path_session_check_blocks_a_cross_session_correction(memory):
    """The write path deliberately has no `session_id` guard on `supersedes`."""
    target = finding(memory, "F-1", session_id="sess-1")
    assert correction(memory, target.record_id, session_id="sess-2").hmac is not None


# ── Composition ─────────────────────────────────────────────────────────────


def test_a_composition_break_withholds_the_snapshot_entirely(memory, tmp_path, caplog):
    """No partial "verified subset" -- that view is what `004` exists to prevent.

    Serving the records that still verify after a removal hands the pack exactly
    the resurrected-record picture the chain was built to detect.
    """
    finding(memory, "F-1")
    finding(memory, "F-2")

    path = tmp_path / PROJECT / "Vault.sol.jsonl"
    kept = path.read_text(encoding="utf-8").splitlines()[:1]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")

    with caplog.at_level("WARNING"):
        with pytest.raises(MemoryCompositionBreak):
            memory.snapshot(project_id=PROJECT, session_id="sess-1")

    # Operator channel only: nothing about the break is shaped for model context.
    assert any("composition" in record.message for record in caplog.records)


# ── Capacity ────────────────────────────────────────────────────────────────


def test_capacity_constants_are_the_fixed_values(memory):
    from sr_agent.models.dispatch import MAX_SNAPSHOT_BYTES, MAX_SNAPSHOT_ITEMS

    assert (MAX_SNAPSHOT_ITEMS, MAX_SNAPSHOT_BYTES) == (10000, 33554432)


def test_item_count_at_the_limit_is_served(memory, monkeypatch):
    monkeypatch.setattr("sr_agent.memory.episodic.MAX_SNAPSHOT_ITEMS", 2)
    finding(memory, "F-1")
    finding(memory, "F-2")
    assert len(memory.snapshot(project_id=PROJECT, session_id="sess-1").items) == 2


def test_item_count_over_the_limit_fails_closed(memory, monkeypatch):
    """Named limit, measured value, and remedy -- and no truncation anywhere.

    Silently dropping the oldest records would give the pack a projection that is
    wrong in the one direction it cannot detect: history it never knew existed.
    """
    monkeypatch.setattr("sr_agent.memory.episodic.MAX_SNAPSHOT_ITEMS", 2)
    for i in range(3):
        finding(memory, f"F-{i}")

    with pytest.raises(SnapshotCapacityExceeded) as excinfo:
        memory.snapshot(project_id=PROJECT, session_id="sess-1")
    message = str(excinfo.value)
    assert "2" in message and "3" in message and "complete_session" in message


def test_byte_size_at_the_limit_is_served(memory, monkeypatch):
    finding(memory, "F-1")
    measured = memory.snapshot(project_id=PROJECT, session_id="sess-1").measured_bytes
    monkeypatch.setattr("sr_agent.memory.episodic.MAX_SNAPSHOT_BYTES", measured)
    assert len(memory.snapshot(project_id=PROJECT, session_id="sess-1").items) == 1


def test_byte_size_over_the_limit_fails_closed(memory, monkeypatch):
    finding(memory, "F-1")
    measured = memory.snapshot(project_id=PROJECT, session_id="sess-1").measured_bytes
    monkeypatch.setattr("sr_agent.memory.episodic.MAX_SNAPSHOT_BYTES", measured - 1)

    with pytest.raises(SnapshotCapacityExceeded) as excinfo:
        memory.snapshot(project_id=PROJECT, session_id="sess-1")
    assert str(measured) in str(excinfo.value)
