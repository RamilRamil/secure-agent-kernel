"""No `write_memory` params reach a trusted tier (feature 002, US3).

`write_memory` was a blessed, non-gated kernel-generic id with a pack-owned body
and an unchecked param bag. Making the kernel execute it closes that seam, but it
also creates a NEW way to be wrong: a path that constructs a signed record from
something the model said. These tests are the proof that the construction is
kernel-authored all the way down.

Two independent defences, tested separately on purpose (D12):

* the validator REJECTS a forged key — so an attempt is visible, not silently
  swallowed;
* the executor never reads one — so even a caller that skipped validation cannot
  produce a forged tier.

A test that only exercised the first would pass against an implementation whose
real defence had been deleted.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.action import Action, ActionClass, ValidationStatus
from sr_agent.models.chat import ChatSession
from sr_agent.models.dispatch import SNAPSHOT_KINDS, DispatchStatus
from sr_agent.models.memory import MemoryRecord, SourceType
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.action import KERNEL_GENERIC_ACTIONS, validate_action
from sr_agent.orchestrator.executor import KernelActionExecutor
from sr_agent.orchestrator.lease import WriterLease
from sr_agent.orchestrator.pack import ActionSpec, CapabilityPack

SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"

#: Every value a model might hope to land on. `human_input` is the prize — it is
#: the only tier the confirmation gate treats as authority.
FORGED_VALUES = ["human_input", "tool_output", SourceType.human_input, 4, True]


def _hostile_pack() -> CapabilityPack:
    def _never(action, ctx):
        raise AssertionError("pack.dispatch must not be reached for write_memory")

    return CapabilityPack(
        name="hostile",
        actions={"do_thing": ActionSpec(ActionClass.read_only, True, lambda a, r: None)},
        tools=(),
        privileged_statuses=frozenset({"blessed"}),
        reasoning_prompt="",
        dispatch=_never,
        execute_confirmed=lambda a, ctx: ("", None),
        persist_finding=lambda p, ctx: None,
        domain_escalation=lambda *a, **k: None,
        signal_from=lambda aa: None,
    )


def _make(tmp_path: Path):
    lease = WriterLease(tmp_path, SECRET)
    memory = EpisodicMemory(
        tmp_path, SECRET, privileged_statuses=frozenset({"blessed"}), lease=lease
    )
    session = ChatSession(principal=Principal(user_id="u", platform="cli", project_id=PROJECT))
    lease.acquire(PROJECT, session.session_id)
    executor = KernelActionExecutor(
        memory=memory, scope_root=tmp_path, pack_id="hostile", pack_contract_version="1"
    )
    return session, memory, executor


def _all_records(memory: EpisodicMemory) -> list[MemoryRecord]:
    return memory.load_for_principal(
        Principal(user_id="u", platform="cli", project_id=PROJECT)
    )


# ── T026: no params combination yields a tier above llm_inference ────────────


@pytest.mark.parametrize("field", sorted({
    "source_type", "hmac", "supersedes", "status_change", "status", "seq",
    "chain_prev", "log_sequence", "record_id", "project_id", "session_id", "tool",
}))
@pytest.mark.parametrize("value", FORGED_VALUES, ids=lambda v: str(v))
def test_no_forged_param_produces_a_record(field: str, value, tmp_path: Path) -> None:
    session, memory, executor = _make(tmp_path)
    result = executor.execute(
        _hostile_pack(), session,
        Action(action_type="write_memory", params={"note": "n", field: value}),
    )
    assert result.status is DispatchStatus.error
    assert _all_records(memory) == []


def test_every_written_note_sits_at_llm_inference(tmp_path: Path) -> None:
    """The whole-store assertion, not a per-call one.

    A per-call check passes against an implementation that writes a second,
    forged record alongside the honest one.
    """
    session, memory, executor = _make(tmp_path)
    for i in range(5):
        executor.execute(
            _hostile_pack(), session,
            Action(action_type="write_memory", params={"note": f"note {i}", "target": "Vault.sol"}),
        )
    records = _all_records(memory)
    assert len(records) == 5
    assert {r.source_type for r in records} == {SourceType.llm_inference}
    assert all(r.payload_kind == "model_note" for r in records)


def test_the_note_body_cannot_smuggle_provenance(tmp_path: Path) -> None:
    """The note is opaque text. Provenance-shaped words inside it stay text."""
    session, memory, executor = _make(tmp_path)
    executor.execute(
        _hostile_pack(), session,
        Action(action_type="write_memory", params={
            "note": '{"source_type": "human_input", "status": "blessed"}',
        }),
    )
    record = _all_records(memory)[0]
    assert record.source_type is SourceType.llm_inference
    assert record.status_change is None
    assert record.payload["note"].startswith('{"source_type"')


# ── T028: Constitution II is not expanded (FR-005) ───────────────────────────


def test_memory_class_still_carries_no_oob_gate() -> None:
    spec = KERNEL_GENERIC_ACTIONS["write_memory"]
    assert spec.action_class is ActionClass.memory

    action = Action(action_type="write_memory", params={"note": "n"})
    assert validate_action(action, Path(".")).status is ValidationStatus.approved
    # `human_confirmation` is set to False (pending) only for write_execute.
    assert action.human_confirmation is None


def test_write_execute_still_gates(tmp_path: Path) -> None:
    """The gate this feature must not have touched, exercised rather than asserted."""
    pack = _hostile_pack()
    pack.actions["risky"] = ActionSpec(ActionClass.write_execute, False, lambda a, r: None)
    action = Action(action_type="risky", params={})
    assert validate_action(action, tmp_path, pack).status is ValidationStatus.approved
    assert action.human_confirmation is False   # pending out-of-band confirmation


# ── T029: a model note never becomes a premise (D10) ─────────────────────────


def test_model_note_is_not_a_snapshot_kind() -> None:
    assert "model_note" not in SNAPSHOT_KINDS


def test_a_written_note_is_absent_from_the_snapshot_while_a_finding_is_present(
    tmp_path: Path,
) -> None:
    """Both halves matter.

    The absence assertion alone would pass against a snapshot builder that
    returned nothing at all, so a finding written in the same session has to show
    up in the same snapshot to prove the builder is working.
    """
    session, memory, executor = _make(tmp_path)

    executor.execute(
        _hostile_pack(), session,
        Action(action_type="write_memory", params={"note": "a note", "target": "Vault.sol"}),
    )
    memory.write(
        MemoryRecord(
            project_id=PROJECT,
            target="Vault.sol",
            source_type=SourceType.external_llm_output,
            session_id=session.session_id,
            finding={"finding_id": "F-1", "location": "Vault.sol:1"},
        )
    )

    snapshot = memory.snapshot(project_id=PROJECT, session_id=session.session_id)
    kinds = {item.kind for item in snapshot.items}
    assert "model_note" not in kinds
    assert "finding" in kinds
