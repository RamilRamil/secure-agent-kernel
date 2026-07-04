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

logger = logging.getLogger(__name__)

MAX_TOOL_SUMMARIES = 10


def _target(session_id: str) -> str:
    return f"chat:{session_id}"


def save_session(session: ChatSession, memory: EpisodicMemory) -> MemoryRecord:
    """Persist the session snapshot (principal/status/facts/turn_ids).

    Orchestrator-authored → tool_output. This is the record `load_session`
    reconstructs from; it is re-written on every turn so turn_ids/facts stay current.
    """
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
    """Reconstruct the session from its latest snapshot (FR-012 resume)."""
    records = memory.load(project_id, _target(session_id))
    snapshots = [r for r in records if r.payload_kind == "chat_session" and r.payload]
    if not snapshots:
        return None
    latest = max(snapshots, key=lambda r: r.timestamp)
    return ChatSession.model_validate(latest.payload)


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
