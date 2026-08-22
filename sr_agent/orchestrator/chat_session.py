"""Chat session persistence (feature 003, T009).

Reuses EpisodicMemory exactly the way orchestrator/checkpoint.py does — a
session-scoped target key `chat:{session_id}`, records layered inside the
generic `MemoryRecord.payload` (payload_kind discriminates). No new storage,
no new integrity story: chat turns get the same HMAC signing + silent-drop-on-
tamper as everything else in memory (research R5).

Trust posture (R6/R12): the ChatSession snapshot, its SessionFacts, and every
PoCStatusEvent are ORCHESTRATOR-authored (`tool_output` tier). Only ChatTurns
carry the reasoning provider's `external_llm_output` tier. Nothing here is ever
written from parsed model output directly into facts/status.
"""
from __future__ import annotations

import logging

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.chat import ChatSession, ChatTurn, PoCStatusEvent, SessionFacts
from sr_agent.models.memory import MemoryRecord, SourceType
from sr_agent.models.session import TERMINAL_SESSION_STATUSES
from sr_agent.orchestrator.lease import LeaseState, WriterLease
from sr_agent.orchestrator.scope import in_flight_operation, rebind_scope as _rebind_scope

logger = logging.getLogger(__name__)

MAX_TOOL_SUMMARIES = 10


class SessionLifecycleError(Exception):
    """Raised when an operator control is illegal in the current session state."""
    pass

logger = logging.getLogger(__name__)

MAX_TOOL_SUMMARIES = 10


def _target(session_id: str) -> str:
    return f"chat:{session_id}"


def save_session(session: ChatSession, memory: EpisodicMemory) -> MemoryRecord:
    """Persist the session snapshot (principal/status/facts/turn_ids).

    Orchestrator-authored → tool_output. This is the record `load_session`
    reconstructs from; it is re-written on every turn so turn_ids/facts stay current.
    """
    from sr_agent.models.chat import CHAT_SESSION_PROJECTION_VERSION

    if session.projection_version is None:
        session.projection_version = CHAT_SESSION_PROJECTION_VERSION
    record = MemoryRecord(
        project_id=session.principal.project_id,
        target=_target(session.session_id),
        source_type=SourceType.tool_output,   # orchestrator-authored, never model
        tool="orchestrator",
        session_id=session.session_id,
        payload=session.model_dump(mode="json"),
        payload_kind="chat_session",
    )
    return memory.write(record, principal=session.principal)


def save_turn(session: ChatSession, turn: ChatTurn, memory: EpisodicMemory) -> MemoryRecord:
    """Append a turn and re-snapshot the session (updated turn_ids/facts)."""
    if turn.turn_id not in session.turn_ids:
        session.turn_ids.append(turn.turn_id)
    record = MemoryRecord(
        project_id=session.principal.project_id,
        target=_target(session.session_id),
        source_type=turn.source_type,          # external_llm_output (model-tier)
        tool=None,
        session_id=session.session_id,
        payload=turn.model_dump(mode="json"),
        payload_kind="chat_turn",
    )
    saved = memory.write(record, principal=session.principal)
    save_session(session, memory)
    return saved


def update_facts(
    session: ChatSession,
    *,
    finding_id: str | None = None,
    tool_summary: str | None = None,
) -> None:
    """Deterministically update grounding facts (R6). Orchestrator-only mutator —
    never fed from parsed model output. Callers: _persist_finding / _dispatch-equivalent."""
    facts = session.session_facts or SessionFacts(project_id=session.principal.project_id)
    if finding_id and finding_id not in facts.known_finding_ids:
        facts.known_finding_ids.append(finding_id)
    if tool_summary:
        facts.recent_tool_summaries.append(tool_summary)
        # bounded — keep the most recent MAX_TOOL_SUMMARIES
        del facts.recent_tool_summaries[:-MAX_TOOL_SUMMARIES]
    session.session_facts = facts


def record_poc_status(
    session: ChatSession, event: PoCStatusEvent, memory: EpisodicMemory
) -> MemoryRecord:
    """Append a mechanical PoC status event (R12/FR-014). tool_output tier — a
    passed PoC is a reproduction, NOT a security verdict (Constitution II)."""
    record = MemoryRecord(
        project_id=session.principal.project_id,
        target=_target(session.session_id),
        source_type=SourceType.tool_output,
        tool="orchestrator",
        session_id=session.session_id,
        payload=event.model_dump(mode="json"),
        payload_kind="poc_status",
    )
    return memory.write(record, principal=session.principal)


def load_session(
    session_id: str, project_id: str, memory: EpisodicMemory
) -> ChatSession | None:
    """Reconstruct the session from its latest snapshot or pause_checkpoint.

    A pause is one checkpoint record (FR-010b). If that record is the newest
    durable event, the session loads as paused even when no chat_session
    snapshot was rewritten alongside it.
    """
    from sr_agent.models.session import PauseContinuation

    records = memory.load(project_id, _target(session_id))
    snapshots = [r for r in records if r.payload_kind == "chat_session" and r.payload]
    checkpoints = [r for r in records if r.payload_kind == "pause_checkpoint" and r.payload]
    controls = [
        r for r in records
        if r.payload_kind == "control_event" and r.payload
        and r.payload.get("event") in {"complete_session", "abandon_session", "detach_session"}
    ]
    if not snapshots and not checkpoints:
        return None

    session = None
    if snapshots:
        latest_snap = max(snapshots, key=lambda r: (r.log_sequence or 0, r.timestamp))
        session = ChatSession.model_validate(latest_snap.payload)
    elif checkpoints:
        from sr_agent.models.principal import Principal

        session = ChatSession(
            session_id=session_id,
            principal=Principal(user_id="unknown", platform="cli", project_id=project_id),
        )

    latest_ck = max(checkpoints, key=lambda r: r.log_sequence or 0) if checkpoints else None
    latest_ctrl = max(controls, key=lambda r: r.log_sequence or 0) if controls else None

    ck_seq = latest_ck.log_sequence or 0 if latest_ck else -1
    ctrl_seq = latest_ctrl.log_sequence or 0 if latest_ctrl else -1
    if latest_ck is not None and ck_seq >= ctrl_seq and session is not None:
        payload = latest_ck.payload or {}
        session.status = payload.get("session_status", session.status)
        session.pending_relay_request_id = payload.get("pending_relay_request_id")
        session.continuation = PauseContinuation(
            turn_id=payload.get("turn_id") or session.session_id,
            phase=payload.get("phase") or "dispatch",
            user_message=payload.get("user_message") or "",
            system_prompt_id=payload.get("system_prompt_id") or "",
            system_prompt_hash=payload.get("system_prompt_hash") or "",
            last_dispatch_operation_id=payload.get("last_dispatch_operation_id"),
            pending=payload.get("pending"),
            tool_calls_used=int(payload.get("tool_calls_used") or 0),
            pending_relay_request_id=payload.get("pending_relay_request_id"),
            expected_session_revision=int(payload.get("expected_session_revision") or 0),
            session_status=payload.get("session_status") or "paused_relay",
            action_snapshot=payload.get("action_snapshot"),
            scope_generation=int(payload.get("scope_generation") or 0),
        )
    elif latest_ctrl is not None and session is not None and ctrl_seq > ck_seq:
        event = (latest_ctrl.payload or {}).get("event")
        session.status = {
            "complete_session": "completed",
            "abandon_session": "abandoned",
            "detach_session": "detached",
        }.get(event, session.status)
    return session


def render_roadmap(session_id: str, project_id: str, memory: EpisodicMemory) -> str:
    """Render the findings roadmap as a markdown table (R12/FR-014).

    A regenerable VIEW over the append-only PoCStatusEvent history — never a
    parallel store. Latest status wins per finding; a skipped row always shows
    its reason (no silent omission). Mechanical status only, never a verdict.
    """
    records = memory.load(project_id, _target(session_id))
    latest: dict[str, dict] = {}
    for r in records:
        if r.payload_kind == "poc_status" and r.payload:
            fid = r.payload["finding_id"]
            prev = latest.get(fid)
            if prev is None or r.timestamp >= prev["_ts"]:
                latest[fid] = {**r.payload, "_ts": r.timestamp}

    if not latest:
        return "No PoC activity recorded yet."

    lines = ["| finding | status | note |", "|---|---|---|"]
    for fid in sorted(latest):
        ev = latest[fid]
        note = ev.get("skip_reason") or ev.get("poc_path") or ""
        lines.append(f"| {fid} | {ev['status']} | {note} |")
    return "\n".join(lines)


def load_turns(
    session_id: str, project_id: str, memory: EpisodicMemory
) -> list[ChatTurn]:
    """Reconstruct turn history in order, from unordered memory.load() results."""
    records = memory.load(project_id, _target(session_id))
    by_id = {
        r.payload["turn_id"]: ChatTurn.model_validate(r.payload)
        for r in records if r.payload_kind == "chat_turn" and r.payload
    }
    session = load_session(session_id, project_id, memory)
    order = session.turn_ids if session else list(by_id)
    return [by_id[t] for t in order if t in by_id]


def _lease_of(memory: EpisodicMemory) -> WriterLease:
    lease = getattr(memory, "_lease", None)
    if lease is None:
        raise SessionLifecycleError(
            "Session lifecycle APIs require a memory bound to a WriterLease."
        )
    return lease


def _record_control(
    session: ChatSession,
    memory: EpisodicMemory,
    event: str,
    extra: dict | None = None,
    approved_by: str = "operator",
) -> MemoryRecord:
    payload = {"event": event, "approved_by": approved_by}
    if extra:
        payload.update(extra)
    return memory.write(
        MemoryRecord(
            project_id=session.principal.project_id,
            target=_target(session.session_id),
            source_type=SourceType.human_input,
            session_id=session.session_id,
            payload_kind="control_event",
            payload=payload,
        )
    )


def _require_turn_boundary(session: ChatSession, action: str) -> None:
    pending = in_flight_operation(session)
    if pending is not None:
        raise SessionLifecycleError(
            f"{action} refused: operation {pending!r} is in flight. "
            "Legal only at a completed-turn boundary."
        )


def complete_session(
    session: ChatSession,
    memory: EpisodicMemory,
    *,
    approved_by: str,
) -> ChatSession:
    """Operator-only: release the lease, mark the session finished."""
    lease = _lease_of(memory)
    project_id = session.principal.project_id
    _record_control(session, memory, "complete_session", approved_by=approved_by)
    session.status = "completed"
    session.lease_mode = "completed"
    save_session(session, memory)
    lease.complete(project_id, session.session_id)
    return session


def abandon_session(
    session: ChatSession,
    memory: EpisodicMemory,
    *,
    approved_by: str,
) -> ChatSession:
    lease = _lease_of(memory)
    project_id = session.principal.project_id
    _record_control(session, memory, "abandon_session", approved_by=approved_by)
    session.status = "abandoned"
    session.lease_mode = "abandoned"
    save_session(session, memory)
    lease.abandon(project_id, session.session_id)
    return session


def detach_session(
    session: ChatSession,
    memory: EpisodicMemory,
    *,
    approved_by: str,
) -> ChatSession:
    """REPL EOF. Refused mid-turn rather than silently dropping pending state."""
    _require_turn_boundary(session, "detach_session")
    lease = _lease_of(memory)
    project_id = session.principal.project_id
    _record_control(session, memory, "detach_session", approved_by=approved_by)
    session.status = "detached"
    session.lease_mode = "detached"
    save_session(session, memory)
    lease.detach(project_id, session.session_id)
    return session


def reacquire_lease(
    session: ChatSession,
    memory: EpisodicMemory,
) -> LeaseState:
    """Same session resumes as writer. Terminal status is durable on the session,
    so this still refuses after another session has used the project.
    """
    if session.status in TERMINAL_SESSION_STATUSES:
        raise SessionLifecycleError(
            f"Session {session.session_id!r} is {session.status} and cannot write again."
        )
    lease = _lease_of(memory)
    state = lease.reacquire(session.principal.project_id, session.session_id)
    session.status = "active"
    session.lease_mode = state.mode.value
    session.writer_session_id = session.session_id
    save_session(session, memory)
    return state


def takeover_lease(
    session: ChatSession,
    memory: EpisodicMemory,
    *,
    approved_by: str,
) -> ChatSession:
    """Human-approved release of a paused reservation (FR-014)."""
    lease = _lease_of(memory)
    project_id = session.principal.project_id
    _record_control(session, memory, "takeover_lease", approved_by=approved_by)
    session.status = "abandoned"
    session.lease_mode = "abandoned"
    save_session(session, memory)
    lease.takeover(project_id, approved_by=approved_by)
    return session


def rebind_scope(session: ChatSession, policy, *, memory: EpisodicMemory, approved_by: str):
    """Operator control; delegated to `orchestrator.scope` so the state machine
    and the digest live in one place.
    """
    return _rebind_scope(session, policy, memory=memory, approved_by=approved_by)
