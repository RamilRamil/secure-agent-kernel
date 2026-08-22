"""Full-restart idempotency of effect ports (FR-021 / FR-005 / SC-012 / SC-012b).

The id must exist *before* the checkpoint, as a function of the transition, so
a crash between creating the relay request and writing the checkpoint cannot
mint a second request. Seeding `operation_id` into the test would hide exactly
the derivation this is meant to prove.
"""
import pytest

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.action import Action, ActionClass
from sr_agent.models.chat import ChatSession
from sr_agent.models.dispatch import DispatchPayload, DispatchResult, DispatchStatus, PendingKind, PendingWait
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.executor import KernelActionExecutor
from sr_agent.orchestrator.lease import WriterLease
from sr_agent.orchestrator.pack import ActionSpec, CapabilityPack
from sr_agent.orchestrator.relay import request_analysis_if_absent
from pydantic import ValidationError


SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"


def _pack(dispatch):
    return CapabilityPack(
        name="fixture",
        actions={"do_thing": ActionSpec(ActionClass.read_only, True, lambda a, r: None)},
        tools=(),
        privileged_statuses=frozenset(),
        reasoning_prompt="",
        dispatch=dispatch,
        execute_confirmed=lambda a, ctx: ("", None),
        persist_finding=lambda p, ctx: None,
        domain_escalation=lambda *a, **k: None,
        signal_from=lambda aa: None,
    )


def _make(tmp_path, dispatch, version="1"):
    lease = WriterLease(tmp_path, SECRET)
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease)
    session = ChatSession(principal=Principal(user_id="u", platform="cli", project_id=PROJECT))
    lease.acquire(PROJECT, session.session_id)
    executor = KernelActionExecutor(
        memory=memory,
        scope_root=tmp_path,
        pack_id="fixture",
        pack_contract_version=version,
        confirmations_dir=tmp_path / "conf",
        relay_dir=tmp_path / "relay",
    )
    return session, memory, lease, executor


def test_crash_before_checkpoint_adopts_the_existing_request_on_restart(tmp_path):
    """Kill point: relay file exists, checkpoint does not.

    A fresh process re-derives the transition from the same inputs and must
    reuse the file already on disk. No operation_id is handed to the test.
    """
    relay_dir = tmp_path / "relay"

    def dispatch(action, ctx):
        request_analysis_if_absent(
            target="Vault.sol",
            context="contract Vault {}",
            relay_dir=relay_dir,
            operation_id=ctx.operation_id,
        )
        return DispatchResult(
            status=DispatchStatus.pending,
            body="awaiting",
            pending=PendingWait(kind=PendingKind.external_response, correlation_id=ctx.operation_id),
        )

    session, memory, lease, executor = _make(tmp_path, dispatch)

    def boom(*a, **k):
        raise KeyboardInterrupt("killed after effect, before checkpoint")

    executor.write_pause_checkpoint = boom
    with pytest.raises(KeyboardInterrupt):
        executor.execute(
            _pack(dispatch),
            session,
            Action(action_type="do_thing", params={"finding_id": "F-1"}),
        )

    requests = list((relay_dir / "requests").glob("*.md"))
    assert len(requests) == 1
    first_name = requests[0].name

    fresh_lease = WriterLease(tmp_path, SECRET)
    fresh_memory = EpisodicMemory(tmp_path, SECRET, lease=fresh_lease)
    fresh_lease.acquire(PROJECT, session.session_id)
    fresh = KernelActionExecutor(
        memory=fresh_memory,
        scope_root=tmp_path,
        pack_id="fixture",
        pack_contract_version="1",
        confirmations_dir=tmp_path / "conf",
        relay_dir=relay_dir,
    )
    fresh.execute(
        _pack(dispatch),
        session,
        Action(action_type="do_thing", params={"finding_id": "F-1"}),
    )
    assert [p.name for p in (relay_dir / "requests").glob("*.md")] == [first_name]


def test_ingested_response_survives_a_crash_before_commit(tmp_path):
    """Ingest, kill, mutate the source file: resume still sees the stored body."""
    def pending_once(action, ctx, state={"n": 0}):
        state["n"] += 1
        if state["n"] == 1:
            return DispatchResult(
                status=DispatchStatus.pending,
                body="awaiting",
                pending=PendingWait(kind=PendingKind.external_response, correlation_id=ctx.operation_id),
            )
        return DispatchResult(status=DispatchStatus.ran, body="done")

    session, memory, lease, executor = _make(tmp_path, pending_once)
    executor.execute(
        _pack(pending_once),
        session,
        Action(action_type="do_thing", params={"finding_id": "F-1"}),
    )

    source = tmp_path / "relay" / "responses" / "incoming.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('{"decision":"deny"}', encoding="utf-8")

    with pytest.raises(KeyboardInterrupt):
        executor.ingest_pending_response(session, body={"decision": "deny"})
        raise KeyboardInterrupt("killed after ingest")

    source.write_text('{"decision":"approve"}', encoding="utf-8")
    ckpt = [
        r for r in memory.load(PROJECT, f"chat:{session.session_id}")
        if r.payload_kind == "pause_checkpoint"
    ][-1]
    stored = memory.find_external_response(
        PROJECT,
        session.session_id,
        operation_id=ckpt.payload["last_dispatch_operation_id"],
        correlation_id=ckpt.payload["pending"]["correlation_id"],
    )
    assert stored is not None
    assert stored.payload["body"] == {"decision": "deny"}
    assert source.read_text(encoding="utf-8") == '{"decision":"approve"}'


def test_forged_identity_is_rejected_on_both_surfaces(tmp_path):
    """Chat and batch share the executor, so a pack-supplied identity dies once."""
    with pytest.raises(ValidationError):
        DispatchPayload(body={"k": "v"}, session_id="forged")

    session, memory, lease, executor = _make(
        tmp_path, lambda a, c: DispatchResult(status=DispatchStatus.ran, body="ok")
    )
    for surface in ("chat", "batch"):
        result = executor.execute(
            _pack(lambda a, c: DispatchResult(status=DispatchStatus.ran, body=surface)),
            session,
            Action(action_type="do_thing", params={"finding_id": surface}),
        )
        assert result.status is DispatchStatus.ran
