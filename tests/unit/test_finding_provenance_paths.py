"""A finding is written after its turn's action resolves, stamped (kernel/005).

Before this feature the loop persisted a model-reported finding the moment the
model reported it — in BOTH entry points, before the terminal check, before the
unknown-action check, before validation and before dispatch. A signed `Finding`
therefore existed whether the turn ran a tool, proposed a rejected action, named
one that did not exist, or ended on `complete`, and nothing on the record told
those apart.

The table below is the whole feature. It is run against **both** `run` (batch)
and `run_turn` (chat) from one parametrisation on purpose: the old bug was a
per-path ordering mistake, and two separately-written test bodies are exactly how
one path silently stops covering a case. A reader who adds a scenario gets it in
both paths or in neither.

The stamp says what happened to the action proposed in the same `AgentAction`.
It does NOT say the finding was derived from that action's output — see
`contracts/finding-provenance.md`.
"""
from __future__ import annotations

import os
from pathlib import Path

# `sr_agent.config` is loaded at import time by `loop`; the house pattern for a
# module that imports it standalone (see tests/security/test_chat_mi_scenarios.py).
os.environ.setdefault("SR_SECRET_KEY", "00" * 32)

import pytest

from sr_agent.llm_core.chat_reasoning import ReasoningOutcome
from sr_agent.llm_core.schemas import AgentAction, FindingPayload
from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.action import ActionClass
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

FINDING = FindingPayload(
    finding_id="F-1", location="Vault.sol:42", function_name="withdraw", severity="high",
)


class _StubAuditClient:
    def __init__(self, actions): self._actions = list(actions)
    def complete(self, messages): return self._actions.pop(0)


class _StubReasoning:
    def __init__(self, actions): self._actions = list(actions)
    def complete(self, messages):
        return ReasoningOutcome(kind="action", agent_action=self._actions.pop(0))


def _pack(dispatch, *, builds_finding: bool = True) -> CapabilityPack:
    from tests.fixtures.pack.fixture_pack import FixtureFinding

    def persist(payload, ctx):
        if not builds_finding or payload is None:
            return None
        return FixtureFinding(finding_id=payload.finding_id, location=payload.location)

    return CapabilityPack(
        name="fixture",
        actions={"do_thing": ActionSpec(ActionClass.read_only, True, lambda a, r: None)},
        tools=(),
        privileged_statuses=frozenset(),
        reasoning_prompt="",
        dispatch=dispatch,
        execute_confirmed=lambda a, ctx: ("", None),
        persist_finding=persist,
        domain_escalation=lambda *a, **k: None,
        signal_from=lambda aa: None,
    )


def _env(tmp_path: Path, session):
    lease = WriterLease(tmp_path, SECRET)
    lease.acquire(PROJECT, session.session_id)
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease)
    executor = KernelActionExecutor(
        memory=memory, scope_root=tmp_path, pack_id="fixture",
        pack_contract_version="1", confirmations_dir=tmp_path / "conf",
        relay_dir=tmp_path / "relay",
    )
    return memory, executor


def _run_batch(tmp_path: Path, pack, actions):
    session = FixtureSession(principal=Principal(user_id="u", platform="cli", project_id=PROJECT))
    memory, executor = _env(tmp_path, session)
    loop = OrchestratorLoop(session, memory, tmp_path, pack=pack,
                            confirmations_dir=tmp_path / "conf")
    loop._executor = executor
    loop._audit_client = _StubAuditClient(actions)
    loop.run(system_prompt="be a security auditor")
    return memory


def _run_chat(tmp_path: Path, pack, actions):
    session = ChatSession(
        principal=Principal(user_id="u", platform="cli", project_id=PROJECT),
        scope_root=str(tmp_path), include=["*"],
    )
    memory, executor = _env(tmp_path, session)
    loop = OrchestratorLoop(session, memory, tmp_path, pack=pack,
                            reasoning_provider=_StubReasoning(actions),
                            confirmations_dir=tmp_path / "conf")
    loop._executor = executor
    loop.run_turn(user_message="look at withdraw", system_prompt="be a security auditor")
    return memory


#: T014 — one table, two runners. Adding a runner here covers every scenario;
#: adding a scenario covers every runner.
RUNNERS = [
    pytest.param(_run_batch, id="batch-run"),
    pytest.param(_run_chat, id="chat-run_turn"),
]


def _findings(memory: EpisodicMemory) -> list:
    principal = Principal(user_id="u", platform="cli", project_id=PROJECT)
    return [r for r in memory.load_for_principal(principal) if r.finding]


def _ok(action, ctx):
    return DispatchResult(status=DispatchStatus.ran, body="done")


# ── Scenario 1: the action ran ───────────────────────────────────────────────


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_finding_after_a_dispatch_that_ran(runner, tmp_path: Path) -> None:
    memory = runner(tmp_path, _pack(_ok), [
        AgentAction(next_action="do_thing", tool_params={}, finding=FINDING),
        AgentAction(next_action="complete", reasoning_summary="done"),
    ])

    records = _findings(memory)
    assert len(records) == 1
    r = records[0]
    assert r.action_resolution == "resolved"
    assert r.action_dispatch_status is DispatchStatus.ran
    assert r.action_operation_id
    # Never promoted, by this path or any other.
    assert r.source_type.value == "external_llm_output"


# ── Scenario 2: the action resolved, badly ───────────────────────────────────


@pytest.mark.parametrize("runner", RUNNERS)
@pytest.mark.parametrize("status", [
    DispatchStatus.error, DispatchStatus.did_not_run,
    DispatchStatus.timeout, DispatchStatus.unavailable,
])
def test_a_finding_after_a_dispatch_that_failed(runner, status, tmp_path: Path) -> None:
    """`resolved` is about the action having an outcome, not a good one. The
    hypothesis is still recorded; it is simply not proof-eligible."""
    memory = runner(tmp_path, _pack(lambda a, c: DispatchResult(status=status, body="nope")), [
        AgentAction(next_action="do_thing", tool_params={}, finding=FINDING),
        AgentAction(next_action="complete", reasoning_summary="done"),
    ])

    records = _findings(memory)
    assert len(records) == 1
    assert records[0].action_resolution == "resolved"
    assert records[0].action_dispatch_status is status


# ── Scenarios 3-5: no action resolved at all ─────────────────────────────────


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_finding_on_a_rejected_action(runner, tmp_path: Path) -> None:
    """`read_file` outside the scope root is refused by the kernel's own
    validator, so the rejection is real rather than staged."""
    memory = runner(tmp_path, _pack(_ok), [
        AgentAction(next_action="read_file", tool_params={"path": "/etc/passwd"}, finding=FINDING),
        AgentAction(next_action="complete", reasoning_summary="done"),
    ])

    records = _findings(memory)
    assert len(records) == 1
    assert records[0].action_resolution == "unresolved"
    assert records[0].action_operation_id is None
    assert records[0].action_dispatch_status is None


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_finding_on_an_unknown_action(runner, tmp_path: Path) -> None:
    memory = runner(tmp_path, _pack(_ok), [
        AgentAction(next_action="no_such_action", tool_params={}, finding=FINDING),
        AgentAction(next_action="complete", reasoning_summary="done"),
    ])

    records = _findings(memory)
    assert len(records) == 1
    assert records[0].action_resolution == "unresolved"


@pytest.mark.parametrize("runner", RUNNERS)
@pytest.mark.parametrize("terminal", ["complete", "escalate"])
def test_a_finding_on_a_turn_that_used_no_tool(runner, terminal, tmp_path: Path) -> None:
    """The case the old ordering hid best: the model reports a finding and ends
    the turn. Nothing ran, and the record must say so."""
    memory = runner(tmp_path, _pack(_ok), [
        AgentAction(next_action=terminal, reasoning_summary="answered directly", finding=FINDING),
    ])

    records = _findings(memory)
    assert len(records) == 1
    assert records[0].action_resolution == "unresolved"


# ── Scenario 7: the pack declines ────────────────────────────────────────────


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_finding_the_pack_declines_to_build_produces_no_record(runner, tmp_path: Path) -> None:
    """Unchanged behaviour, pinned because the call sites moved. No record, no
    partial, no near-finding."""
    memory = runner(tmp_path, _pack(_ok, builds_finding=False), [
        AgentAction(next_action="do_thing", tool_params={}, finding=FINDING),
        AgentAction(next_action="complete", reasoning_summary="done"),
    ])

    assert _findings(memory) == []


# ── The negative control ─────────────────────────────────────────────────────


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_turn_with_no_finding_writes_no_finding_record(runner, tmp_path: Path) -> None:
    """Without this, every assertion above would pass against a loop that had
    stopped persisting findings entirely."""
    memory = runner(tmp_path, _pack(_ok), [
        AgentAction(next_action="do_thing", tool_params={}),
        AgentAction(next_action="complete", reasoning_summary="done"),
    ])
    assert _findings(memory) == []


# ── Scenario 6: the turn paused (US2) ────────────────────────────────────────
#
# The consumer's main pending path is an out-of-band confirmation, so a
# hypothesis has to survive a crash and stay visible in the snapshot while a
# human decides. Withholding the write until resume was rejected for that
# reason; leaving it frozen at `pending` was rejected because after resume the
# action HAS an outcome and a record still saying `pending` is false evidence of
# a different kind. Hence a pair, and hence both halves tested.


def _paused(action, ctx):
    from sr_agent.models.dispatch import PendingKind, PendingWait

    return DispatchResult(
        status=DispatchStatus.pending, body="awaiting",
        pending=PendingWait(kind=PendingKind.external_response,
                            correlation_id=ctx.operation_id),
    )


def _chat_env(tmp_path: Path, pack, actions):
    session = ChatSession(
        principal=Principal(user_id="u", platform="cli", project_id=PROJECT),
        scope_root=str(tmp_path), include=["*"],
    )
    memory, executor = _env(tmp_path, session)
    loop = OrchestratorLoop(session, memory, tmp_path, pack=pack,
                            reasoning_provider=_StubReasoning(actions),
                            confirmations_dir=tmp_path / "conf")
    loop._executor = executor
    return loop, memory


def test_a_finding_in_a_turn_that_pauses_is_recorded_as_pending(tmp_path: Path) -> None:
    loop, memory = _chat_env(tmp_path, _pack(_paused), [
        AgentAction(next_action="do_thing", tool_params={}, finding=FINDING),
    ])
    result = loop.run_turn(user_message="look", system_prompt="p")
    assert result.status.startswith("paused")

    records = _findings(memory)
    assert len(records) == 1
    assert records[0].action_resolution == "pending"
    assert records[0].action_dispatch_status is DispatchStatus.pending
    assert records[0].action_operation_id == result.pending_confirmation_id


def test_resume_adds_a_resolution_record_and_the_paused_one_survives(tmp_path: Path) -> None:
    """The survival assertion is the point of the whole design.

    Under `supersedes` the paused record would be gone from every load, and a
    consumer that captured its `record_id` before the out-of-band confirmation
    would be left with a dangling reference at exactly the moment it needs it.
    `supersedes` is also unavailable to the kernel — it requires `human_input`,
    and promoting a finding to that tier is what Constitution I forbids.
    """
    calls = {"n": 0}

    def dispatch(action, ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            return _paused(action, ctx)
        return DispatchResult(status=DispatchStatus.ran, body="done")

    loop, memory = _chat_env(tmp_path, _pack(dispatch), [
        AgentAction(next_action="do_thing", tool_params={}, finding=FINDING),
    ])
    loop.run_turn(user_message="look", system_prompt="p")
    paused = _findings(memory)[0]
    address = paused.record_id

    loop.resume_turn(system_prompt="p")

    records = _findings(memory)
    assert len(records) == 2
    assert any(r.record_id == address for r in records), "the paused record was removed"

    resolution = [r for r in records if r.record_id != address][0]
    assert resolution.action_resolution == "resolved"
    assert resolution.action_dispatch_status is DispatchStatus.ran
    assert resolution.action_operation_id == paused.action_operation_id
    assert resolution.resolves_record_id == address
    # The link runs B -> A only: A is written first and the store is append-only.
    assert paused.resolves_record_id is None


def test_a_resolved_pair_does_not_grow_a_third_record(tmp_path: Path) -> None:
    """The pair is written once even if the resolution hook runs twice.

    A second full `resume_turn` is already refused one layer down — kernel/003
    re-derives the transition identity against the moved session revision and
    raises rather than re-dispatching — so this drives the hook directly. Relying
    on that outer refusal alone would leave the inner guard untested, and the
    inner one is what stops a retry from filing the same outcome twice.
    """
    loop, memory = _chat_env(tmp_path, _pack(_paused), [
        AgentAction(next_action="do_thing", tool_params={}, finding=FINDING),
    ])
    loop.run_turn(user_message="look", system_prompt="p")

    done = DispatchResult(status=DispatchStatus.ran, body="done")
    loop._resolve_paused_findings(done)
    loop._resolve_paused_findings(done)

    records = _findings(memory)
    assert len(records) == 2
    assert sum(1 for r in records if r.resolves_record_id) == 1


def test_a_resume_with_no_pending_finding_writes_nothing_extra(tmp_path: Path) -> None:
    """The negative control: without it the resume hook could be writing a
    record for every resume regardless of whether a finding was ever paused."""
    def dispatch(action, ctx):
        return DispatchResult(status=DispatchStatus.ran, body="done")

    loop, memory = _chat_env(tmp_path, _pack(_paused), [
        AgentAction(next_action="do_thing", tool_params={}),      # no finding
    ])
    loop.run_turn(user_message="look", system_prompt="p")
    loop._pack = _pack(dispatch)
    loop.resume_turn(system_prompt="p")

    assert _findings(memory) == []
