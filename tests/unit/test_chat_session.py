"""Tests for chat session persistence (feature 003, T010)."""
from __future__ import annotations

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.audit import Principal
from sr_agent.models.chat import ChatSession, ChatTurn, PoCStatusEvent
from sr_agent.models.memory import SourceType
from sr_agent.orchestrator.chat_session import (
    load_session,
    load_turns,
    record_poc_status,
    save_session,
    save_turn,
    update_facts,
)

_KEY = bytes(range(32))


def _memory(tmp_path) -> EpisodicMemory:
    return EpisodicMemory(memory_root=tmp_path, secret_key=_KEY)


def _session() -> ChatSession:
    return ChatSession(principal=Principal(user_id="u", platform="cli", project_id="proj"))


def test_round_trip_session(tmp_path):
    mem = _memory(tmp_path)
    s = _session()
    update_facts(s, finding_id="F1", tool_summary="read_file A.sol (10 lines)")
    save_session(s, mem)

    loaded = load_session(s.session_id, "proj", mem)
    assert loaded is not None
    assert loaded.session_id == s.session_id
    assert loaded.principal.project_id == "proj"
    assert loaded.status == "active"
    assert loaded.session_facts.known_finding_ids == ["F1"]
    assert loaded.session_facts.recent_tool_summaries == ["read_file A.sol (10 lines)"]


def test_session_and_facts_are_orchestrator_authored(tmp_path):
    mem = _memory(tmp_path)
    s = _session()
    turn = ChatTurn(session_id=s.session_id, user_message="hi")
    save_turn(s, turn, mem)

    records = mem.load("proj", f"chat:{s.session_id}")
    kinds = {r.payload_kind: r.source_type for r in records}
    # session snapshot + facts are orchestrator-authored (tool_output);
    # the turn itself carries the reasoning provider's tier (external_llm_output).
    assert kinds["chat_session"] is SourceType.tool_output
    assert kinds["chat_turn"] is SourceType.external_llm_output


def test_resume_reconstructs_turn_order(tmp_path):
    mem = _memory(tmp_path)
    s = _session()
    t1 = ChatTurn(session_id=s.session_id, user_message="first")
    t2 = ChatTurn(session_id=s.session_id, user_message="second")
    save_turn(s, t1, mem)
    save_turn(s, t2, mem)

    turns = load_turns(s.session_id, "proj", mem)
    assert [t.user_message for t in turns] == ["first", "second"]
    # and the reloaded session knows the ordered turn ids
    loaded = load_session(s.session_id, "proj", mem)
    assert loaded.turn_ids == [t1.turn_id, t2.turn_id]


def test_bounded_tool_summaries(tmp_path):
    s = _session()
    for i in range(15):
        update_facts(s, tool_summary=f"summary {i}")
    assert len(s.session_facts.recent_tool_summaries) == 10
    assert s.session_facts.recent_tool_summaries[-1] == "summary 14"


def test_record_poc_status(tmp_path):
    mem = _memory(tmp_path)
    s = _session()
    record_poc_status(s, PoCStatusEvent(finding_id="F1", status="passed", poc_path="audit/poc/F1.t.sol"), mem)

    records = mem.load("proj", f"chat:{s.session_id}")
    poc = [r for r in records if r.payload_kind == "poc_status"]
    assert len(poc) == 1
    assert poc[0].source_type is SourceType.tool_output      # not a verdict, tool tier
    assert poc[0].payload["status"] == "passed"
