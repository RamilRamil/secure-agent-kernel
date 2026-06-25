from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class EscalationTrigger(str, Enum):
    irreversible_action = "irreversible_action"
    memory_status_change = "memory_status_change"
    critical_finding = "critical_finding"
    unverified_high = "unverified_high"
    mock_test = "mock_test"
    contradicting_findings = "contradicting_findings"
    unknown_pattern = "unknown_pattern"
    resource_limit_approaching = "resource_limit_approaching"


class FindingPayload(BaseModel):
    """Structured finding that the LLM can include in an AgentAction."""
    finding_id: str
    location: str
    function_name: str
    bastet_tag: str | None = None
    severity: str
    preconditions: dict[int, bool] = {}
    mitigations_present: list[str] = []
    notes: str = ""


class AgentAction(BaseModel):
    """The only output structure accepted from LLM calls.

    LLM returns exactly one AgentAction per turn. The orchestrator reads
    next_action + tool_params to decide what to execute, and finding to
    persist any new security finding. Free text is in reasoning_summary only.

    If the LLM response does not parse as AgentAction → orchestrator treats
    it as a malformed response and does NOT execute anything.
    """
    next_action: str                            # must match ActionType enum value
    tool_params: dict = {}                      # params for next_action
    finding: FindingPayload | None = None       # new finding to persist, if any
    reasoning_summary: str = ""                 # human-readable explanation (not executed)
    escalation_trigger: EscalationTrigger | None = None  # set when next_action=escalate
