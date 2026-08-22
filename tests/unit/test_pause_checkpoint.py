"""One pause_checkpoint makes a pause durable (FR-010a / FR-010b / SC-007).

Two writes (continuation, then session status) leave a crash window in which
the session loads as `active` while the turn is gone. One record closes that
window: after it is fsynced the session is paused, and nothing else has to
land for the pause to be visible.
"""
from pathlib import Path

import pytest

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.action import Action
from sr_agent.models.chat import ChatSession
from sr_agent.models.dispatch import DispatchResult, DispatchStatus, PendingKind, PendingWait
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.executor import KernelActionExecutor
from sr_agent.orchestrator.lease import WriterLease
from sr_agent.orchestrator.pack import ActionSpec, CapabilityPack, PackContext
from sr_agent.models.action import ActionClass


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


@pytest.fixture
def env(tmp_path):
    lease = WriterLease(tmp_path, SECRET)
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease)
    session = ChatSession(principal=Principal(user_id="u", platform="cli", project_id=PROJECT))
    lease.acquire(PROJECT, session.session_id)
    executor = KernelActionExecutor(
        memory=memory,
        scope_root=tmp_path,
        pack_id="fixture",
        pack_contract_version="1",
        confirmations_dir=tmp_path / "conf",
        relay_dir=tmp_path / "relay",
    )
    return session, memory, lease, executor, tmp_path


def _pause_records(memory, session):
    return [
        r for r in memory.load(PROJECT, f"chat:{session.session_id}")
        if r.payload_kind == "pause_checkpoint"
    ]


def test_pending_dispatch_writes_exactly_one_checkpoint(env):
    session, memory, lease, executor, tmp_path = env

    def dispatch(action, ctx):
        return DispatchResult(
            status=DispatchStatus.pending,
            body="awaiting relay",
            pending=PendingWait(kind=PendingKind.external_response, correlation_id=ctx.operation_id),
        )

    before = len(memory.load(PROJECT, f"chat:{session.session_id}"))
    result = executor.execute(_pack(dispatch), session, Action(action_type="do_thing", params={"finding_id": "F-1"}))
    after = memory.load(PROJECT, f"chat:{session.session_id}")

    assert result.status is DispatchStatus.pending
    assert len(after) - before == 1
    assert after[-1].payload_kind == "pause_checkpoint"
    assert after[-1].payload["session_status"] == "paused_relay"
    assert after[-1].payload["tool_calls_used"] == 0
    assert "system_prompt_body" not in after[-1].payload


@pytest.mark.parametrize(
    "kind,status",
    [
        (PendingKind.external_response, "paused_relay"),
        (PendingKind.human_confirmation, "paused_confirmation"),
        (PendingKind.local_model_retry, "blocked_local_unavailable"),
    ],
)
def test_each_pending_kind_maps_to_one_paused_status(env, kind, status):
    session, memory, lease, executor, tmp_path = env

    def dispatch(action, ctx):
        return DispatchResult(
            status=DispatchStatus.pending,
            body="waiting",
            pending=PendingWait(kind=kind, correlation_id="c-1"),
        )

    executor.execute(_pack(dispatch), session, Action(action_type="do_thing", params={"finding_id": "F-1"}))
    records = _pause_records(memory, session)
    assert len(records) == 1
    assert records[0].payload["session_status"] == status


def test_crash_after_the_checkpoint_loads_as_paused_not_active(env):
    session, memory, lease, executor, tmp_path = env

    def dispatch(action, ctx):
        return DispatchResult(
            status=DispatchStatus.pending,
            body="awaiting",
            pending=PendingWait(kind=PendingKind.external_response, correlation_id="c-1"),
        )

    executor.execute(
        _pack(dispatch),
        session,
        Action(action_type="do_thing", params={"finding_id": "F-1"}),
        tool_calls_used=3,
    )

    fresh = EpisodicMemory(tmp_path, SECRET)
    from sr_agent.orchestrator.chat_session import load_session

    loaded = load_session(session.session_id, PROJECT, fresh)
    assert loaded is not None
    assert loaded.status == "paused_relay"
    assert loaded.continuation is not None
    assert loaded.continuation.tool_calls_used == 3
