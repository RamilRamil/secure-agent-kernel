"""The dispatch-commit target is bounded BEFORE the pack runs (T037, option C).

`commit_if_absent` files a record under `action.params["target"]`, and that
target becomes a filename. Unbounded, an oversized or NUL-bearing target raises
`OSError`/`ValueError` from inside the append — and it does so *after*
`pack.dispatch` has already returned, i.e. after the effect happened in the
world. Two things go wrong there, and only the second one matters:

* the exception escapes `execute` and kills the turn; and
* the commit record never lands, so `find_committed_bundle` finds nothing on a
  retry and the SAME action dispatches again. For a `write_execute` action that
  was confirmed out of band, one human approval would then cover two
  executions — Constitution II.

So the bound is checked before dispatch, not at the commit call: the failure
mode has to be "nothing happened", not "something happened and was not
recorded". Every test below therefore asserts BOTH the clean refusal and that
`pack.dispatch` was never entered. A test that only checked the returned status
would pass against a fix applied at the wrong end of the path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

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
from sr_agent.orchestrator.action import MAX_TARGET_BYTES
from sr_agent.orchestrator.executor import KernelActionExecutor
from sr_agent.orchestrator.lease import WriterLease
from sr_agent.orchestrator.pack import ActionSpec, CapabilityPack

SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"

#: Values that reach the filesystem layer as a filename and fail there. The
#: first is the plain size bound; the rest are what a hostile pack or a model
#: reaches for once size alone is checked.
UNUSABLE = [
    pytest.param("x" * (MAX_TARGET_BYTES + 1), id="oversize-ascii"),
    pytest.param("я" * 150, id="oversize-utf8-300-bytes"),
    pytest.param("a\x00b", id="embedded-nul"),
    pytest.param("a\nb", id="newline"),
    pytest.param("a\x7fb", id="delete-char"),
]


def _pack(dispatch) -> CapabilityPack:
    return CapabilityPack(
        name="fixture",
        actions={
            "do_thing": ActionSpec(ActionClass.read_only, True, lambda a, r: None),
            # A pack that declines to check its own params. The kernel may not
            # depend on a pack doing so (Principle III) — that is the point.
            "loose": ActionSpec(ActionClass.read_only, True, lambda a, r: None),
        },
        tools=(),
        privileged_statuses=frozenset(),
        reasoning_prompt="",
        dispatch=dispatch,
        execute_confirmed=lambda a, ctx: ("", None),
        persist_finding=lambda p, ctx: None,
        domain_escalation=lambda *a, **k: None,
        signal_from=lambda aa: None,
    )


def _env(tmp_path: Path, dispatch):
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
        pack_contract_version="1",
        confirmations_dir=tmp_path / "conf",
        relay_dir=tmp_path / "relay",
    )
    return session, memory, executor


def _ran(payload: str = "ok"):
    calls: list[Action] = []

    def dispatch(action, ctx):
        calls.append(action)
        return DispatchResult(
            status=DispatchStatus.ran,
            body=payload,
            payloads=[DispatchPayload(body={"note": payload})],
        )

    return dispatch, calls


def _commits(memory: EpisodicMemory) -> list:
    return [r for r in memory._all_records(PROJECT) if r.payload_kind == "dispatch_commit"]


# ── The refusal happens before the effect ────────────────────────────────────


@pytest.mark.parametrize("target", UNUSABLE)
def test_unusable_target_refuses_without_dispatching(target: str, tmp_path: Path) -> None:
    dispatch, calls = _ran()
    session, memory, executor = _env(tmp_path, dispatch)

    result = executor.execute(
        _pack(dispatch), session,
        Action(action_type="loose", params={"target": target}),
    )

    assert result.status is DispatchStatus.error
    assert calls == [], "the pack ran; the bound was applied too late to help"
    assert _commits(memory) == []


@pytest.mark.parametrize("target", UNUSABLE)
def test_unusable_target_never_raises(target: str, tmp_path: Path) -> None:
    """A model supplies `params`. An exception here would be a model-triggerable
    way to end the turn, which is the DoS shape D12.3 rules out.

    Only the NUL case raises today (the length at which the filesystem itself
    objects is ~255, above this bound); the parametrisation is uniform on
    purpose, so that a later change to the bound cannot quietly reintroduce a
    raising case for one of the others.
    """
    dispatch, _ = _ran()
    session, _, executor = _env(tmp_path, dispatch)
    executor.execute(  # must not raise
        _pack(dispatch), session,
        Action(action_type="loose", params={"target": target}),
    )


def test_an_oversized_action_id_is_caught_through_the_fallback(tmp_path: Path) -> None:
    """With no `target` param the kernel files the commit under the action id,
    so the id is a target too and has to meet the same bound."""
    dispatch, calls = _ran()
    session, memory, executor = _env(tmp_path, dispatch)
    pack = _pack(dispatch)
    long_id = "z" * (MAX_TARGET_BYTES + 1)
    pack.actions[long_id] = ActionSpec(ActionClass.read_only, True, lambda a, r: None)

    result = executor.execute(pack, session, Action(action_type=long_id, params={}))

    assert result.status is DispatchStatus.error
    assert calls == []


def test_a_non_string_target_is_refused_rather_than_coerced(tmp_path: Path) -> None:
    """`str()` would turn anything into a usable-looking name. Refusing keeps the
    filed target something the caller actually wrote."""
    dispatch, calls = _ran()
    session, _, executor = _env(tmp_path, dispatch)
    result = executor.execute(
        _pack(dispatch), session,
        Action(action_type="loose", params={"target": ["Vault.sol"]}),
    )
    assert result.status is DispatchStatus.error
    assert calls == []


# ── What must NOT change ─────────────────────────────────────────────────────


def test_an_ordinary_target_still_commits(tmp_path: Path) -> None:
    """The positive control. Without it every assertion above passes against an
    executor that refuses everything."""
    dispatch, calls = _ran()
    session, memory, executor = _env(tmp_path, dispatch)

    result = executor.execute(
        _pack(dispatch), session,
        Action(action_type="do_thing", params={"target": "Vault.sol"}),
    )

    assert result.status is DispatchStatus.ran
    assert len(calls) == 1
    assert [r.target for r in _commits(memory)] == ["Vault.sol"]


def test_an_absent_target_still_falls_back_to_the_action_id(tmp_path: Path) -> None:
    dispatch, _ = _ran()
    session, memory, executor = _env(tmp_path, dispatch)
    executor.execute(_pack(dispatch), session, Action(action_type="do_thing", params={}))
    assert [r.target for r in _commits(memory)] == ["do_thing"]


def test_an_empty_target_still_falls_back_rather_than_refusing(tmp_path: Path) -> None:
    """Pre-existing behaviour, kept deliberately: `params.get("target") or id`
    already treats an empty target as absent, so this never reached the
    filesystem and there is no reason for the bound to start rejecting it."""
    dispatch, _ = _ran()
    session, memory, executor = _env(tmp_path, dispatch)
    executor.execute(
        _pack(dispatch), session, Action(action_type="do_thing", params={"target": ""})
    )
    assert [r.target for r in _commits(memory)] == ["do_thing"]


def test_a_traversal_shaped_target_is_still_accepted(tmp_path: Path) -> None:
    """The bound is about error quality and exactly-once, NOT containment.
    Containment already holds: `project_id` comes from the principal and
    `_target_stem` renders separators inert. Rejecting this would advertise the
    check without adding one."""
    dispatch, _ = _ran()
    session, memory, executor = _env(tmp_path, dispatch)
    result = executor.execute(
        _pack(dispatch), session,
        Action(action_type="do_thing", params={"target": "../../etc/passwd"}),
    )
    assert result.status is DispatchStatus.ran
    written = {p.name for p in (tmp_path / PROJECT).iterdir()}
    assert ".._.._etc_passwd.jsonl" in written


# ── The resume path carries the same guard ───────────────────────────────────


def test_resume_refuses_a_bad_target_from_an_older_checkpoint(tmp_path: Path) -> None:
    """`execute` now rejects these up front, so a bad target can only arrive on
    the resume path from a checkpoint written before this change. The guard is
    there for that case: the checkpoint is signed, but being signed says the
    kernel wrote it down, not that the value inside is usable."""
    dispatch, calls = _ran()
    session, memory, executor = _env(tmp_path, dispatch)
    pack = _pack(dispatch)

    bad = Action(action_type="loose", params={"target": "x" * (MAX_TARGET_BYTES + 1)})
    transition_key, operation_id, revision = executor.derive_ids(session, bad)
    paused = DispatchResult(
        status=DispatchStatus.pending,
        body="awaiting",
        pending=PendingWait(kind=PendingKind.external_response, correlation_id=operation_id),
    )
    executor.write_pause_checkpoint(
        session, paused, bad, transition_key, operation_id, revision,
        turn_id=None, user_message="", system_prompt_id="", system_prompt_hash="",
        tool_calls_used=0, phase="dispatch",
    )
    calls.clear()

    result = executor.resume(pack, session, response_body={"decision": "ok"})

    assert result.status is DispatchStatus.error
    assert calls == []
    assert _commits(memory) == []
