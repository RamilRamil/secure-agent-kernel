"""Tests for chat-mode data models (feature 003, T002)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sr_agent.models.action import Action, ActionType, ValidationResult, ValidationStatus
from sr_agent.models.audit import Principal
from sr_agent.models.chat import (
    MAX_TOOL_CALLS_PER_TURN,
    ChatSession,
    ChatTurn,
    PoCStatusEvent,
    SessionFacts,
    ToolInvocation,
)
from sr_agent.models.memory import SourceType


def _principal(project_id: str = "proj-1") -> Principal:
    return Principal(user_id="cli-user", platform="cli", project_id=project_id)


def _invocation() -> ToolInvocation:
    return ToolInvocation(
        action=Action(action_type=ActionType.read_file, params={"path": "A.sol"}),
        validation_result=ValidationResult(status=ValidationStatus.approved),
        result_summary="[DATA START]...[DATA END]",
    )


# ── ChatSession: one-project binding (FR-001) ──────────────────────────────

def test_session_defaults_facts_to_bound_project():
    s = ChatSession(principal=_principal("proj-1"))
    assert s.session_facts is not None
    assert s.session_facts.project_id == "proj-1"


def test_session_rejects_mismatched_facts_project():
    with pytest.raises(ValidationError):
        ChatSession(
            principal=_principal("proj-1"),
            session_facts=SessionFacts(project_id="OTHER"),
        )


def test_session_rejects_empty_project():
    with pytest.raises(ValidationError):
        ChatSession(principal=_principal(""))


# ── ChatTurn: per-turn budget + trust tier (FR-006, FR-007) ────────────────

def test_turn_within_budget_ok():
    turn = ChatTurn(
        session_id="s1",
        user_message="show me Foo.sol",
        tool_invocations=[_invocation() for _ in range(MAX_TOOL_CALLS_PER_TURN)],
    )
    assert len(turn.tool_invocations) == MAX_TOOL_CALLS_PER_TURN


def test_turn_over_budget_rejected():
    with pytest.raises(ValidationError):
        ChatTurn(
            session_id="s1",
            user_message="loop forever",
            tool_invocations=[_invocation() for _ in range(MAX_TOOL_CALLS_PER_TURN + 1)],
        )


def test_turn_source_type_defaults_external_llm_output():
    turn = ChatTurn(session_id="s1", user_message="hi")
    assert turn.source_type is SourceType.external_llm_output


@pytest.mark.parametrize("tier", [SourceType.human_input, SourceType.llm_inference])
def test_turn_rejects_human_or_inference_tier(tier):
    with pytest.raises(ValidationError):
        ChatTurn(session_id="s1", user_message="hi", source_type=tier)


# ── PoCStatusEvent: mechanical status, no silent skips (FR-014) ─────────────

def test_poc_status_passed_ok():
    ev = PoCStatusEvent(finding_id="F1", status="passed", poc_path="audit/poc/F1.t.sol")
    assert ev.source_type is SourceType.tool_output


def test_poc_status_skipped_requires_reason():
    with pytest.raises(ValidationError):
        PoCStatusEvent(finding_id="F1", status="skipped")  # no skip_reason
    # with a reason it's fine
    ev = PoCStatusEvent(finding_id="F1", status="skipped", skip_reason="floor-gated, OOS")
    assert ev.skip_reason == "floor-gated, OOS"


@pytest.mark.parametrize("tier", [SourceType.human_input, SourceType.llm_inference])
def test_poc_status_rejects_human_or_inference_tier(tier):
    with pytest.raises(ValidationError):
        PoCStatusEvent(finding_id="F1", status="pending", source_type=tier)
