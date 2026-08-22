"""The kernel-owned `write_memory` path (feature 002, US1 / T008-T013).

`KernelActionExecutor.execute` intercepts `write_memory` before `derive_ids`
(D8) and never reaches `pack.dispatch`. The record's every field is kernel-set
(data-model.md, Entity 1); the model supplies only a note and, optionally, a
target. This file drives that path directly through the executor and through
both loop surfaces (batch `run`, chat `run_turn`) to prove parity (T010).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sr_agent.llm_core.chat_reasoning import ReasoningOutcome
from sr_agent.llm_core.schemas import AgentAction
from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.action import Action, ActionClass
from sr_agent.models.chat import ChatSession
from sr_agent.models.dispatch import DispatchResult, DispatchStatus
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.executor import KernelActionExecutor
from sr_agent.orchestrator.lease import WriterLease
from sr_agent.orchestrator.loop import OrchestratorLoop
from sr_agent.orchestrator.pack import ActionSpec, CapabilityPack

from tests.fixtures.pack.fixture_pack import FixtureSession

SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"
NOTE = "withdraw() has no reentrancy guard"


def _asserting_pack(name="fixture"):
    """A pack whose `dispatch` raises. `write_memory` must never call it (T008)."""

    def boom(action, ctx):
        raise AssertionError("pack.dispatch must not be called for write_memory")

    return CapabilityPack(
        name=name,
        actions={"do_thing": ActionSpec(ActionClass.read_only, True, lambda a, r: None)},
        tools=(),
        privileged_statuses=frozenset({"blessed"}),
        reasoning_prompt="",
        dispatch=boom,
        execute_confirmed=lambda a, ctx: ("", None),
        persist_finding=lambda p, ctx: None,
        domain_escalation=lambda *a, **k: None,
        signal_from=lambda aa: None,
    )


def _env(tmp_path: Path, *, with_lease: WriterLease | None = "make", scope_root: Path | None = None):
    """Build a session + memory + executor sharing one lease, unless told otherwise.

    `with_lease="make"` (default) creates and acquires a fresh lease for the
    returned session. `with_lease=None` builds a memory with NO lease bound
    (lease check is a no-op — not what T012a needs). Pass an already-acquired
    lease belonging to a DIFFERENT session to exercise the lease-conflict path.
    """
    session = ChatSession(
        principal=Principal(user_id="u", platform="cli", project_id=PROJECT),
        scope_root=str(scope_root or tmp_path),
        include=["*"],
    )
    if with_lease == "make":
        lease = WriterLease(tmp_path, SECRET)
        lease.acquire(PROJECT, session.session_id)
    else:
        lease = with_lease
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease)
    executor = KernelActionExecutor(
        memory=memory,
        scope_root=scope_root or tmp_path,
        pack_id="fixture",
        pack_contract_version="1",
        confirmations_dir=tmp_path / "conf",
        relay_dir=tmp_path / "relay",
    )
    return session, memory, lease, executor


# ── T008: field shape, dispatch never called ────────────────────────────────


def test_a_validated_write_memory_produces_one_record_with_the_fixed_shape(tmp_path):
    session, memory, lease, executor = _env(tmp_path)
    pack = _asserting_pack()

    result = executor.execute(pack, session, Action(action_type="write_memory", params={"note": NOTE}))

    assert result.status is DispatchStatus.ran

    records = memory._all_records(PROJECT)
    assert len(records) == 1
    record = records[0]
    assert record.source_type.value == "llm_inference"
    assert record.payload_kind == "model_note"
    assert record.payload == {"note": NOTE}
    assert record.tool is None
    assert record.finding is None
    assert record.checkpoint is None
    assert record.status_change is None
    assert record.session_id == session.session_id
    assert record.project_id == PROJECT


# ── T009: target routing (D11), project_id never from params ───────────────


def test_target_param_routes_the_record_and_is_loadable_by_it(tmp_path):
    session, memory, lease, executor = _env(tmp_path)
    pack = _asserting_pack()

    executor.execute(
        pack, session,
        Action(action_type="write_memory", params={"target": "Vault.sol", "note": NOTE}),
    )

    found = memory.load(PROJECT, "Vault.sol")
    assert len(found) == 1
    assert found[0].payload == {"note": NOTE}


def test_no_target_falls_back_to_the_action_id(tmp_path):
    session, memory, lease, executor = _env(tmp_path)
    pack = _asserting_pack()

    executor.execute(pack, session, Action(action_type="write_memory", params={"note": NOTE}))

    found = memory.load(PROJECT, "write_memory")
    assert len(found) == 1


def test_project_id_param_cannot_override_the_principal(tmp_path):
    """The validator rejects a `project_id` param outright (T002); prove the
    rejection path returns status=error rather than writing anything, so
    params can never reach the record even indirectly."""
    session, memory, lease, executor = _env(tmp_path)
    pack = _asserting_pack()

    result = executor.execute(
        pack, session,
        Action(action_type="write_memory", params={"note": NOTE, "project_id": "other-project"}),
    )

    assert result.status is DispatchStatus.error
    assert memory._all_records(PROJECT) == []
    assert memory._all_records("other-project") == []


# ── T010: batch/chat parity, neither surface reaches dispatch ──────────────


class _StubAuditClient:
    """Stands in for `ClaudeClient` in `OrchestratorLoop.run` (batch)."""

    def __init__(self, actions: list[AgentAction]):
        self._actions = list(actions)

    def complete(self, messages):
        return self._actions.pop(0)


class _StubReasoningProvider:
    """Stands in for `ChatReasoningProvider` in `OrchestratorLoop.run_turn` (chat)."""

    def __init__(self, outcomes: list[ReasoningOutcome]):
        self._outcomes = list(outcomes)

    def complete(self, messages):
        return self._outcomes.pop(0)


def _loop(tmp_path, session, memory, pack, **kwargs) -> OrchestratorLoop:
    loop = OrchestratorLoop(
        session, memory, tmp_path, pack=pack,
        confirmations_dir=tmp_path / "conf",
        **kwargs,
    )
    return loop


def test_batch_run_writes_a_note_without_reaching_dispatch(tmp_path):
    """`OrchestratorLoop.run` (batch) needs the generic `Session` protocol
    (`iterations`/`token_budget_used`), which `ChatSession` does not carry —
    `FixtureSession` is the house stand-in for that surface (see
    `tests/fixtures/pack/fixture_pack.py`)."""
    session = FixtureSession(principal=Principal(user_id="u", platform="cli", project_id=PROJECT))
    lease = WriterLease(tmp_path, SECRET)
    lease.acquire(PROJECT, session.session_id)
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease)
    executor = KernelActionExecutor(
        memory=memory,
        scope_root=tmp_path,
        pack_id="fixture",
        pack_contract_version="1",
        confirmations_dir=tmp_path / "conf",
        relay_dir=tmp_path / "relay",
    )
    pack = _asserting_pack()
    loop = _loop(tmp_path, session, memory, pack)
    loop._executor = executor
    loop._audit_client = _StubAuditClient([
        AgentAction(next_action="write_memory", tool_params={"note": NOTE}),
        AgentAction(next_action="complete", reasoning_summary="done"),
    ])

    result = loop.run(system_prompt="be a security auditor")

    assert result.completed is True
    records = [r for r in memory._all_records(PROJECT) if r.payload_kind == "model_note"]
    assert len(records) == 1
    assert records[0].payload == {"note": NOTE}


def test_chat_run_turn_writes_a_note_without_reaching_dispatch(tmp_path):
    session, memory, lease, executor = _env(tmp_path)
    pack = _asserting_pack()
    loop = _loop(
        tmp_path, session, memory, pack,
        reasoning_provider=_StubReasoningProvider([
            ReasoningOutcome(
                kind="action",
                agent_action=AgentAction(next_action="write_memory", tool_params={"note": NOTE}),
            ),
            ReasoningOutcome(
                kind="action",
                agent_action=AgentAction(next_action="complete", reasoning_summary="done"),
            ),
        ]),
    )
    loop._executor = executor

    result = loop.run_turn(user_message="please note this", system_prompt="be a security auditor")

    assert result.status == "completed"
    records = [r for r in memory._all_records(PROJECT) if r.payload_kind == "model_note"]
    assert len(records) == 1
    assert records[0].payload == {"note": NOTE}


# ── T012: failure modes never raise ─────────────────────────────────────────


def test_no_writer_lease_returns_error_not_an_exception(tmp_path):
    """A DIFFERENT session holds the lease; this session's write_memory must
    return status=error, not raise, and the turn must be able to continue."""
    other = ChatSession(principal=Principal(user_id="other", platform="cli", project_id=PROJECT))
    lease = WriterLease(tmp_path, SECRET)
    lease.acquire(PROJECT, other.session_id)

    session, memory, _, executor = _env(tmp_path, with_lease=lease)
    pack = _asserting_pack()

    result = executor.execute(pack, session, Action(action_type="write_memory", params={"note": NOTE}))

    assert result.status is DispatchStatus.error
    assert memory._all_records(PROJECT) == []


def test_no_writer_lease_lets_the_turn_continue_via_run_turn(tmp_path):
    other = ChatSession(principal=Principal(user_id="other", platform="cli", project_id=PROJECT))
    lease = WriterLease(tmp_path, SECRET)
    lease.acquire(PROJECT, other.session_id)

    session, memory, _, executor = _env(tmp_path, with_lease=lease)
    pack = _asserting_pack()
    loop = _loop(
        tmp_path, session, memory, pack,
        reasoning_provider=_StubReasoningProvider([
            ReasoningOutcome(
                kind="action",
                agent_action=AgentAction(next_action="write_memory", tool_params={"note": NOTE}),
            ),
            ReasoningOutcome(
                kind="action",
                agent_action=AgentAction(next_action="complete", reasoning_summary="done after refusal"),
            ),
        ]),
    )
    loop._executor = executor

    result = loop.run_turn(user_message="please note this", system_prompt="be a security auditor")

    # The turn reaches a normal terminal status — the refusal did not kill it.
    assert result.status == "completed"


def test_broken_composition_chain_returns_error_not_an_exception(tmp_path):
    """Corrupt the signed chain head so `_next_log_sequence` raises
    `MemoryChainError`; the executor must still return, never raise."""
    session, memory, lease, executor = _env(tmp_path)
    pack = _asserting_pack()

    # Seed one record so there is a head to corrupt, then delete it —
    # `EpisodicMemory._next_log_sequence` raises `MemoryChainError` when the
    # chain head is unreadable (see test_MI013_append_onto_an_unverifiable_chain).
    executor.execute(pack, session, Action(action_type="write_memory", params={"note": "seed"}))
    memory._head_path(PROJECT).unlink()
    memory._invalidate_cache(PROJECT)

    result = executor.execute(pack, session, Action(action_type="write_memory", params={"note": NOTE}))

    assert result.status is DispatchStatus.error


def test_unexpected_exception_type_still_surfaces(tmp_path):
    """The catch is narrow on purpose: an unrelated exception must NOT be
    swallowed as a routine refusal — it is a kernel bug and must be visible."""
    session, memory, lease, executor = _env(tmp_path)
    pack = _asserting_pack()

    def boom(record, principal=None):
        raise RuntimeError("not one of the three caught types")

    memory.write = boom
    with pytest.raises(RuntimeError):
        executor.execute(pack, session, Action(action_type="write_memory", params={"note": NOTE}))


# ── T013: a privileged status_change cannot ride this path ─────────────────


def test_status_change_param_is_refused_at_validation(tmp_path):
    session, memory, lease, executor = _env(tmp_path)
    pack = _asserting_pack()

    result = executor.execute(
        pack, session,
        Action(action_type="write_memory", params={"note": NOTE, "status_change": "blessed"}),
    )

    assert result.status is DispatchStatus.error
    assert memory._all_records(PROJECT) == []


def test_status_param_is_refused_at_validation(tmp_path):
    session, memory, lease, executor = _env(tmp_path)
    pack = _asserting_pack()

    result = executor.execute(
        pack, session,
        Action(action_type="write_memory", params={"note": NOTE, "status": "blessed"}),
    )

    assert result.status is DispatchStatus.error
    assert not any(r.status_change is not None for r in memory._all_records(PROJECT))
