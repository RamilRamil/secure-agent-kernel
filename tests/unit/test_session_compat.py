"""Old snapshots stay verifiable; new fields are not invented (FR-015, SC-008)."""
from pathlib import Path

import pytest

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.action import Action, ActionClass
from sr_agent.models.chat import CHAT_SESSION_PROJECTION_VERSION, ChatSession
from sr_agent.models.dispatch import DispatchResult, DispatchStatus
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.chat_session import load_session, save_session
from sr_agent.orchestrator.executor import KernelActionExecutor, ResumeError
from sr_agent.orchestrator.lease import WriterLease
from sr_agent.orchestrator.loop import OrchestratorLoop
from sr_agent.orchestrator.pack import ActionSpec, CapabilityPack
from sr_agent.orchestrator.scope import ScopeUnboundError, restore_scope_root


SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"


def _pack():
    return CapabilityPack(
        name="fixture",
        actions={"do_thing": ActionSpec(ActionClass.read_only, True, lambda a, r: None)},
        tools=(),
        privileged_statuses=frozenset(),
        reasoning_prompt="",
        dispatch=lambda a, c: DispatchResult(status=DispatchStatus.ran, body="x"),
        execute_confirmed=lambda a, ctx: ("", None),
        persist_finding=lambda p, ctx: None,
        domain_escalation=lambda *a, **k: None,
        signal_from=lambda aa: None,
    )


def test_old_chat_session_payload_does_not_invent_scope_root():
    payload = {
        "session_id": "sess-old",
        "principal": {"user_id": "u", "platform": "cli", "project_id": PROJECT},
    }
    session = ChatSession.model_validate(payload)
    assert session.scope_root is None
    assert session.projection_version is None
    assert session.continuation is None
    with pytest.raises(ScopeUnboundError, match="no scope_root"):
        restore_scope_root(session)


def test_save_stamps_projection_version_and_load_keeps_unbound_root(tmp_path: Path):
    lease = WriterLease(tmp_path, SECRET)
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease)
    session = ChatSession(principal=Principal(user_id="u", platform="cli", project_id=PROJECT))
    lease.acquire(PROJECT, session.session_id)
    save_session(session, memory)
    assert session.projection_version == CHAT_SESSION_PROJECTION_VERSION

    loaded = load_session(session.session_id, PROJECT, memory)
    assert loaded is not None
    assert loaded.scope_root is None
    with pytest.raises(ScopeUnboundError):
        restore_scope_root(loaded)


def test_legacy_record_without_log_sequence_is_not_re_signed(tmp_path: Path):
    """FR-015: missing new fields stay missing. The store does not invent them."""
    lease = WriterLease(tmp_path, SECRET)
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease)
    session = ChatSession(principal=Principal(user_id="u", platform="cli", project_id=PROJECT))
    lease.acquire(PROJECT, session.session_id)
    save_session(session, memory)

    stem = f"chat__{session.session_id}"
    target = tmp_path / PROJECT / f"{stem}.jsonl"
    line = target.read_text(encoding="utf-8").strip()
    import json
    data = json.loads(line)
    data.pop("log_sequence", None)
    target.write_text(json.dumps(data) + "\n", encoding="utf-8")
    memory._invalidate_cache(PROJECT)

    assert load_session(session.session_id, PROJECT, memory) is None
    assert memory.load(PROJECT, f"chat:{session.session_id}") == []
    # The unverifiable line stays on disk -- not repaired, not re-signed.
    assert "log_sequence" not in json.loads(target.read_text(encoding="utf-8").splitlines()[0])


def test_resume_without_checkpoint_fails_explicitly(tmp_path: Path):
    lease = WriterLease(tmp_path, SECRET)
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease)
    session = ChatSession(
        principal=Principal(user_id="u", platform="cli", project_id=PROJECT),
        scope_root=str(tmp_path),
        include=["*"],
    )
    lease.acquire(PROJECT, session.session_id)
    executor = KernelActionExecutor(
        memory=memory, scope_root=tmp_path, pack_id="fixture",
        pack_contract_version="1",
    )
    with pytest.raises(ResumeError, match="checkpoint"):
        executor.resume(_pack(), session)


def test_resume_of_old_snapshot_without_scope_root_fails(tmp_path: Path):
    lease = WriterLease(tmp_path, SECRET)
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease)
    session = ChatSession(principal=Principal(user_id="u", platform="cli", project_id=PROJECT))
    lease.acquire(PROJECT, session.session_id)
    loop = OrchestratorLoop(
        session, memory, tmp_path, pack=_pack(), reasoning_provider=object(),
        confirmations_dir=tmp_path / "conf",
    )
    with pytest.raises(ResumeError, match="scope_root"):
        loop.resume_turn(system_prompt="inline")
