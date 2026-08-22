"""The pack's read seam carries the finding-provenance stamp (kernel/005, US3).

`PackContext` has no memory handle; `MemorySnapshot` is the only read seam a
pack gets. If the four provenance fields never reached `SnapshotItem`, the
whole feature would be invisible to Repo B no matter how carefully the kernel
stamped `MemoryRecord` on write. This file is the FR-016/FR-017 half of the
feature: the fields must arrive on the item, they must come off the record
ENVELOPE and not out of `body`, and a finding must not silently fall out of
the projection because it now carries more fields than before.
"""
import pytest

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.dispatch import DispatchPayload, DispatchStatus
from sr_agent.models.memory import MemoryRecord, SourceType

SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"


@pytest.fixture
def memory(tmp_path):
    return EpisodicMemory(memory_root=tmp_path, secret_key=SECRET)


def resolved_finding(memory, finding_id="F-1", session_id="sess-1", operation_id="op-1"):
    """A finding whose turn's action resolved -- the richest legal stamp."""
    return memory.write(
        MemoryRecord(
            project_id=PROJECT,
            target="Vault.sol",
            source_type=SourceType.tool_output,
            tool="run_slither",
            session_id=session_id,
            finding={"finding_id": finding_id, "severity": "high"},
            action_resolution="resolved",
            action_operation_id=operation_id,
            action_dispatch_status=DispatchStatus.ran,
        )
    )


def pending_finding(memory, finding_id="F-2", session_id="sess-1", operation_id="op-2"):
    """A finding reported in a turn that paused -- record A of the pause pair."""
    return memory.write(
        MemoryRecord(
            project_id=PROJECT,
            target="Vault.sol",
            source_type=SourceType.tool_output,
            tool="run_slither",
            session_id=session_id,
            finding={"finding_id": finding_id, "severity": "medium"},
            action_resolution="pending",
            action_operation_id=operation_id,
            action_dispatch_status=DispatchStatus.pending,
        )
    )


def resolution_of(memory, paused_record, session_id="sess-1"):
    """Record B of a pause pair: written on resume, points back at A."""
    return memory.write(
        MemoryRecord(
            project_id=PROJECT,
            target="Vault.sol",
            source_type=SourceType.tool_output,
            tool="run_slither",
            session_id=session_id,
            finding={"finding_id": "F-2", "severity": "medium"},
            action_resolution="resolved",
            action_operation_id=paused_record.action_operation_id,
            action_dispatch_status=DispatchStatus.ran,
            resolves_record_id=paused_record.record_id,
        )
    )


def commit(memory, operation_id="op-commit", session_id="sess-1"):
    return memory.commit_if_absent(
        project_id=PROJECT,
        target="Vault.sol",
        session_id=session_id,
        tool="run_slither",
        operation_id=operation_id,
        transition_key=f"tk-{operation_id}",
        expected_revision=0,
        payloads=[DispatchPayload(body={"analyzer": "slither"})],
    )


# ── T026: findings survive the projection despite the new fields ───────────


def test_finding_keeps_kind_finding_and_stays_in_the_snapshot(memory):
    """`_snapshot_kind` reads `payload_kind` FIRST, so a finding given a kind
    string would resolve to that string instead of falling back to "finding"
    -- silently dropping it out of `SNAPSHOT_KINDS`, which is exactly the
    hazard this feature must not introduce.

    Asserting only "a finding is present" would also pass for a builder that
    returns every record regardless of `SNAPSHOT_KINDS`; asserting a
    dispatch_commit is ALSO present, in the same snapshot, pins that kind
    selection is still real and not just always-true.
    """
    resolved_finding(memory)
    commit(memory)

    snapshot = memory.snapshot(project_id=PROJECT, session_id="sess-1")
    kinds = {item.kind for item in snapshot.items}

    assert "finding" in kinds
    assert "dispatch_commit" in kinds


# ── T027: the four fields are readable, and the two operation-id fields differ ─


def test_provenance_fields_readable_off_snapshot_item(memory):
    resolved_finding(memory, operation_id="op-42")

    snapshot = memory.snapshot(project_id=PROJECT, session_id="sess-1")
    item = next(i for i in snapshot.items if i.kind == "finding")

    assert item.action_resolution == "resolved"
    assert item.action_operation_id == "op-42"
    assert item.action_dispatch_status == DispatchStatus.ran
    assert item.resolves_record_id is None


def test_operation_id_and_action_operation_id_answer_different_questions(memory):
    """`operation_id` is "which operation is this record the commit of?" --
    always None on a finding, which is never itself a commit. `action_operation_id`
    is "which operation did the turn that produced this finding dispatch?" and
    may be set. The two names are close enough to misread one for the other;
    a test that only checked one of them would not catch that confusion, so
    both assertions must hold on the very same item.
    """
    resolved_finding(memory, operation_id="op-77")

    snapshot = memory.snapshot(project_id=PROJECT, session_id="sess-1")
    item = next(i for i in snapshot.items if i.kind == "finding")

    assert item.operation_id is None
    assert item.action_operation_id == "op-77"


# ── T030: the fields never leak into `body` or into `finding` on disk ──────


def test_provenance_fields_absent_from_snapshot_body(memory):
    """A weaker test that only checked `SnapshotItem` fields could still pass
    if the kernel had also written the stamp into the finding's own dict --
    i.e. duplicated it into pack-visible content instead of keeping it
    envelope-only. Checking `body` catches that duplication.
    """
    resolved_finding(memory, operation_id="op-9")

    snapshot = memory.snapshot(project_id=PROJECT, session_id="sess-1")
    item = next(i for i in snapshot.items if i.kind == "finding")

    for key in (
        "action_resolution", "action_operation_id",
        "action_dispatch_status", "resolves_record_id",
    ):
        assert key not in item.body


def test_provenance_fields_absent_from_finding_dict_on_disk(memory):
    """Same duplication hazard as above, checked at the source: the record's
    OWN `finding` dict (what `contracts/finding-provenance.md` calls
    `finding.model_dump()`) must never carry the stamp either.
    """
    record = resolved_finding(memory, operation_id="op-9")

    for key in (
        "action_resolution", "action_operation_id",
        "action_dispatch_status", "resolves_record_id",
    ):
        assert key not in record.finding


# ── T031: the pause-pair capacity note ──────────────────────────────────────


def test_paused_findings_produce_two_snapshot_items_each(memory):
    """A weaker test asserting only "N records go in" would not catch a
    snapshot builder that collapsed a pause pair into one item -- collapsing
    is explicitly the CONSUMER's join per the contract, not something the
    kernel does. Asserting the count is exactly 2N pins that both A and B
    independently survive into the projection.
    """
    n = 3
    paused = [pending_finding(memory, finding_id=f"F-{i}", operation_id=f"op-{i}") for i in range(n)]
    for p in paused:
        resolution_of(memory, p)

    snapshot = memory.snapshot(project_id=PROJECT, session_id="sess-1")
    finding_items = [i for i in snapshot.items if i.kind == "finding"]

    assert len(finding_items) == 2 * n
    pending_items = [i for i in finding_items if i.action_resolution == "pending"]
    resolved_items = [i for i in finding_items if i.action_resolution == "resolved"]
    assert len(pending_items) == n
    assert len(resolved_items) == n
    # B links back to A; A has no forward pointer (append-only, B written second).
    for r in resolved_items:
        assert r.resolves_record_id is not None
    for p in pending_items:
        assert p.resolves_record_id is None


def test_capacity_envelope_still_fails_closed_and_does_not_truncate(memory, monkeypatch):
    """The pause pair doubles item count, so the fail-closed cap must still
    bind rather than silently truncating the older half of a pair -- a
    truncated pair would show a "resolved" outcome with no paired "pending"
    record ever having existed, or vice versa. Patching the module constant
    down (rather than writing the real 10000-item cap's worth of records)
    is what keeps this test fast while still exercising the real refusal path.
    """
    import sr_agent.memory.episodic as episodic_module
    from sr_agent.memory.episodic import SnapshotCapacityExceeded

    monkeypatch.setattr(episodic_module, "MAX_SNAPSHOT_ITEMS", 3)

    for i in range(3):
        p = pending_finding(memory, finding_id=f"F-{i}", operation_id=f"op-{i}")
        resolution_of(memory, p)

    with pytest.raises(SnapshotCapacityExceeded):
        memory.snapshot(project_id=PROJECT, session_id="sess-1")
