"""Chat-mode models (feature 003, T001).

New in-memory shapes for the interactive chat loop. Everything else reuses
existing models unmodified (`Principal`, `AgentAction`, `Action`,
`ValidationResult`, `SourceType`, `EscalationTrigger`). `ChatTurn` and
`PoCStatusEvent` persist as generic `MemoryRecord.payload`s (payload_kind
"chat_turn" / "poc_status") — the kernel memory envelope stays pack-agnostic.

See specs/003-interactive-chat-mode/data-model.md.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from sr_agent.llm_core.schemas import AgentAction, EscalationTrigger
from sr_agent.models.action import Action, ActionType, ValidationResult
from sr_agent.models.principal import Principal
from sr_agent.models.memory import SourceType

# Per-turn tool-call budget (FR-006 / research R4). The chat *session* spans an
# unbounded number of turns; any single turn's tool loop stops here.
MAX_TOOL_CALLS_PER_TURN = 10

SessionStatus = Literal[
    "active", "paused_confirmation", "paused_relay", "blocked_local_unavailable"
]

# Trust tiers a model/tool-derived turn may carry — never human_input, never
# llm_inference (Constitution I / FR-007). external_llm_output for the reasoning
# provider's output; tool_output for orchestrator-authored records.
_FORBIDDEN_TURN_TIERS = frozenset({SourceType.human_input, SourceType.llm_inference})


class SessionFacts(BaseModel):
    """Deterministic, orchestrator-authored grounding facts (research R6).

    Never LLM-authored — this is what keeps a long session consistent about its
    own scope even when the raw conversation exceeds the model's window.
    """
    project_id: str
    known_finding_ids: list[str] = Field(default_factory=list)
    recent_tool_summaries: list[str] = Field(default_factory=list)  # bounded (~last 10)


class RoutingDecision(BaseModel):
    """Which tier produced a turn + why it escalated (FR-010, research R2/R3/R10)."""
    tier: Literal["local", "relay", "additional", "blocked_local_unavailable"]
    escalation_trigger: EscalationTrigger | None = None
    escalation_source: Literal["model_self_report", "deterministic_guard"] | None = None


class ToolInvocation(BaseModel):
    """One tool call within a turn. Reuses existing Action/ValidationResult."""
    action: Action
    validation_result: ValidationResult
    result_summary: str  # the wrap_data-wrapped tool output as fed back to the model


class ChatTurn(BaseModel):
    """One user message + the agent's response (spec Turn entity)."""
    turn_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    user_message: str
    routing_decision: RoutingDecision | None = None
    agent_action: AgentAction | None = None
    tool_invocations: list[ToolInvocation] = Field(default_factory=list)
    source_type: SourceType = SourceType.external_llm_output
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def _invariants(self) -> ChatTurn:
        if len(self.tool_invocations) > MAX_TOOL_CALLS_PER_TURN:
            raise ValueError(
                f"tool_invocations ({len(self.tool_invocations)}) exceeds "
                f"per-turn budget {MAX_TOOL_CALLS_PER_TURN} (FR-006)"
            )
        # The turn's provenance is model/tool output — it is NEVER human_input
        # (that would grant it authority over privileged status changes) and
        # never the lowest llm_inference tier (matches stage2 convention).
        if self.source_type in _FORBIDDEN_TURN_TIERS:
            raise ValueError(
                f"ChatTurn.source_type must not be {self.source_type.value!r} "
                "(model-produced content is external_llm_output, never human/inference)"
            )
        return self


class ChatSession(BaseModel):
    """A resumable conversation bound to exactly one project (spec Chat Session)."""
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    principal: Principal
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: SessionStatus = "active"
    pending_confirmation_id: str | None = None
    pending_relay_request_id: str | None = None
    turn_ids: list[str] = Field(default_factory=list)
    session_facts: SessionFacts | None = None

    @model_validator(mode="after")
    def _project_binding(self) -> ChatSession:
        if not self.principal.project_id:
            raise ValueError("ChatSession.principal.project_id must be non-empty (FR-001)")
        # Default the facts store to the bound project; if supplied, it MUST match —
        # this is the mechanical form of FR-001 "no implicit project switching".
        if self.session_facts is None:
            self.session_facts = SessionFacts(project_id=self.principal.project_id)
        elif self.session_facts.project_id != self.principal.project_id:
            raise ValueError(
                "ChatSession.session_facts.project_id "
                f"({self.session_facts.project_id!r}) != principal.project_id "
                f"({self.principal.project_id!r}) — cross-project binding rejected"
            )
        return self


class ConsequentialActionNotice(BaseModel):
    """In-chat visibility that a hard OOB confirmation is being requested (research R8).

    Not a soft gate — shown as the confirmation request is filed (FR-008/FR-013).
    """
    action_type: ActionType
    action_params: dict = Field(default_factory=dict)
    confirmation_id: str
    shown_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


PoCStatus = Literal[
    "pending", "written", "compiled", "passed", "failed", "errored", "skipped"
]


class PoCStatusEvent(BaseModel):
    """Memory-backed findings-roadmap status event (research R12, FR-014).

    Mechanical PoC lifecycle ONLY — never a security verdict. `passed` means a
    reproduction exists, NOT that the finding is confirmed/safe (that stays a
    REQUIRES_HUMAN_CONFIRMATION action, Constitution II).
    """
    finding_id: str
    status: PoCStatus
    skip_reason: str | None = None
    poc_path: str | None = None
    source_type: SourceType = SourceType.tool_output

    @model_validator(mode="after")
    def _rules(self) -> PoCStatusEvent:
        if self.status == "skipped" and not self.skip_reason:
            raise ValueError("a 'skipped' status MUST carry a skip_reason (no silent omission)")
        if self.source_type in _FORBIDDEN_TURN_TIERS:
            raise ValueError("PoCStatusEvent is orchestrator-authored tool_output, not human/inference")
        return self
