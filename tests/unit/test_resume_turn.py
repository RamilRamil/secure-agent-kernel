"""resume_turn continues the stored phase (FR-011 / FR-010c / SC-007 / SC-012a).

Calling `run_turn(user_message)` again after a pause is how the original defect
happened: the loop forgot the in-flight action and asked the model to restate
it. Resume rebuilds the Action from the checkpoint snapshot and never consults
the model for that.
"""
from pathlib import Path

import pytest

from sr_agent.memory.canonical import canonical_digest
from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.action import Action, ActionClass
from sr_agent.models.chat import ChatSession
from sr_agent.models.dispatch import (
    DispatchPayload,
    DispatchResult,
    DispatchStatus,
    PendingKind,
    PendingWait,
)
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.executor import KernelActionExecutor, ResumeError
from sr_agent.orchestrator.lease import WriterLease
from sr_agent.orchestrator.loop import OrchestratorLoop
from sr_agent.orchestrator.pack import ActionSpec, CapabilityPack


SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"


def _pack(dispatch, name="fixture", version_unused=None):
    return CapabilityPack(
        name=name,
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


def _env(tmp_path, dispatch, contract_version="1"):
    lease = WriterLease(tmp_path, SECRET)
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease)
    session = ChatSession(
        principal=Principal(user_id="u", platform="cli", project_id=PROJECT),
        scope_root=str(tmp_path),
        include=["*"],
    )
    lease.acquire(PROJECT, session.session_id)
    executor = KernelActionExecutor(
        memory=memory,
        scope_root=tmp_path,
        pack_id="fixture",
        pack_contract_version=contract_version,
        confirmations_dir=tmp_path / "conf",
        relay_dir=tmp_path / "relay",
    )
    return session, memory, lease, executor


def test_resume_re_enters_dispatch_with_the_same_identity_and_commits(tmp_path):
    seen = {"ids": [], "n": 0}

    def dispatch(action, ctx):
        seen["ids"].append(ctx.operation_id)
        seen["n"] += 1
        if seen["n"] == 1:
            return DispatchResult(
                status=DispatchStatus.pending,
                body="awaiting",
                pending=PendingWait(kind=PendingKind.external_response, correlation_id=ctx.operation_id),
            )
        return DispatchResult(
            status=DispatchStatus.ran,
            body="done",
            payloads=[DispatchPayload(body={"note": "second"})],
        )

    pack = _pack(dispatch)
    session, memory, lease, executor = _env(tmp_path, dispatch)
    first = executor.execute(pack, session, Action(action_type="do_thing", params={"finding_id": "F-1"}))
    assert first.status is DispatchStatus.pending

    second = executor.resume(pack, session, response_body={"decision": "ok"})
    assert second.status is DispatchStatus.ran
    assert seen["ids"][0] == seen["ids"][1]
    commits = [r for r in memory.load(PROJECT, "Vault.sol") if r.payload_kind == "dispatch_commit"]
    # target may be chat or action default — look project-wide
    commits = [
        r for r in memory._all_records(PROJECT) if r.payload_kind == "dispatch_commit"
    ]
    assert len(commits) == 1


def test_resume_turn_never_calls_run_turn(tmp_path):
    def dispatch(action, ctx):
        return DispatchResult(
            status=DispatchStatus.pending,
            body="awaiting",
            pending=PendingWait(kind=PendingKind.external_response, correlation_id=ctx.operation_id),
        )

    pack = _pack(dispatch)
    session, memory, lease, executor = _env(tmp_path, dispatch)
    executor.execute(pack, session, Action(action_type="do_thing", params={"finding_id": "F-1"}))

    loop = OrchestratorLoop(
        session, memory, tmp_path, pack=pack, reasoning_provider=object(),
        confirmations_dir=tmp_path / "conf",
    )
    loop._executor = executor

    def forbidden(*a, **k):
        raise AssertionError("resume_turn must not fall back to run_turn")

    loop.run_turn = forbidden
    result = loop.resume_turn(system_prompt="p")
    assert result.status in {"completed", "paused_relay", "paused_confirmation"}


def test_missing_checkpoint_reports_why(tmp_path):
    session, memory, lease, executor = _env(tmp_path, lambda a, c: DispatchResult(status=DispatchStatus.ran, body="x"))
    with pytest.raises(ResumeError, match="checkpoint"):
        executor.resume(_pack(lambda a, c: DispatchResult(status=DispatchStatus.ran, body="x")), session)


def test_action_is_rebuilt_from_the_snapshot_not_the_model(tmp_path):
    rebuilt = {}

    def dispatch(action, ctx):
        rebuilt["type"] = action.action_type
        rebuilt["params"] = dict(action.params)
        return DispatchResult(
            status=DispatchStatus.pending if not rebuilt.get("second") else DispatchStatus.ran,
            body="x",
            pending=PendingWait(kind=PendingKind.external_response, correlation_id=ctx.operation_id)
            if not rebuilt.get("second") else None,
            payloads=[] if not rebuilt.get("second") else [DispatchPayload(body={"ok": True})],
        )

    pack = _pack(dispatch)
    session, memory, lease, executor = _env(tmp_path, dispatch)
    executor.execute(pack, session, Action(action_type="do_thing", params={"finding_id": "F-1"}))
    rebuilt["second"] = True
    executor.resume(pack, session, response_body={"decision": "ok"})
    assert rebuilt["type"] == "do_thing"
    assert rebuilt["params"]["finding_id"] == "F-1"


def test_incompatible_pack_version_fails_closed(tmp_path):
    def pending(action, ctx):
        return DispatchResult(
            status=DispatchStatus.pending,
            body="awaiting",
            pending=PendingWait(kind=PendingKind.external_response, correlation_id="c-1"),
        )

    session, memory, lease, executor = _env(tmp_path, pending, contract_version="1")
    executor.execute(_pack(pending), session, Action(action_type="do_thing", params={"finding_id": "F-1"}))

    other = KernelActionExecutor(
        memory=memory,
        scope_root=tmp_path,
        pack_id="fixture",
        pack_contract_version="9",
        confirmations_dir=tmp_path / "conf",
        relay_dir=tmp_path / "relay",
    )
    with pytest.raises(ResumeError, match="pack_contract_version"):
        other.resume(_pack(pending), session)


def test_execute_batch_is_the_executor_seam(tmp_path):
    """`sr-agent audit`-style batch uses the same kernel execute path (T065)."""
    seen = {"n": 0}

    def dispatch(action, ctx):
        seen["n"] += 1
        return DispatchResult(
            status=DispatchStatus.ran,
            body="ok",
            payloads=[DispatchPayload(body={"ok": True})],
        )

    pack = _pack(dispatch)
    session, memory, lease, executor = _env(tmp_path, dispatch)
    result = executor.execute_batch(
        pack, session, Action(action_type="do_thing", params={"finding_id": "F-1"}),
    )
    assert result.status is DispatchStatus.ran
    assert seen["n"] == 1


def test_human_confirmation_with_only_a_correlation_id_executes_nothing(tmp_path):
    session, memory, lease, executor = _env(tmp_path, lambda a, c: None)
    # A confirmation file existing is not authority to run an action.
    (tmp_path / "conf").mkdir()
    (tmp_path / "conf" / "only-id.json").write_text('{"status":"approved"}', encoding="utf-8")
    with pytest.raises(ResumeError):
        executor.execute_confirmed_by_correlation("only-id")
