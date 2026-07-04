"""Deterministic escalation triggers — kernel guards (feature 004, R5).

The kernel owns the three domain-INDEPENDENT triggers — irreversible action,
unauthorized memory status-change, resource limit — which hold for ANY pack. The
five finding-based triggers moved to the audit pack (`packs/audit/escalation.py`)
and are supplied via the `domain_escalation` callback: the kernel runs the
generic guards, then the pack's domain check, then the resource guard; first
match wins. `finding` is opaque to the kernel (passed through to the pack); the
kernel never inspects it, so it imports no finding model.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from sr_agent.llm_core.schemas import EscalationTrigger
from sr_agent.models.action import Action, ActionClass
from sr_agent.models.memory import MemoryRecord, SourceType

if TYPE_CHECKING:
    from sr_agent.models.session import Session

logger = logging.getLogger(__name__)

# Token budget threshold — escalate when this fraction of budget is used
RESOURCE_LIMIT_THRESHOLD = 0.85


@dataclass
class EscalationResult:
    triggered: bool
    trigger: EscalationTrigger | None = None
    detail: str = ""


def evaluate_triggers(
    action: Action | None,
    record: MemoryRecord | None,
    finding: object | None = None,
    session: "Session | None" = None,
    existing_findings: list | None = None,
    domain_escalation: Callable[..., "EscalationResult | None"] | None = None,
) -> EscalationResult:
    """Evaluate the deterministic escalation triggers. Returns first match.

    All checks are deterministic — no LLM calls. The LLM may also set
    escalation_trigger in AgentAction, but these checks are independent and
    cannot be suppressed by injected context. Order is preserved: generic
    triggers #1/#2, then the pack's finding-based triggers (#3–#7) via
    `domain_escalation`, then the generic resource trigger #8.
    """
    # 1. Irreversible action requested
    if action and action.action_class == ActionClass.write_execute and not action.is_reversible:
        _at = getattr(action.action_type, "value", action.action_type)
        return EscalationResult(
            triggered=True,
            trigger=EscalationTrigger.irreversible_action,
            detail=f"Action '{_at}' is irreversible",
        )

    # 2. Memory status change from a non-human source
    if record and record.status_change and record.source_type != SourceType.human_input:
        return EscalationResult(
            triggered=True,
            trigger=EscalationTrigger.memory_status_change,
            detail=(
                f"Status change '{record.status_change.new_status}' "
                f"from source_type={record.source_type.value} — requires human review"
            ),
        )

    # 3–7. Domain (pack) finding-based triggers — opaque to the kernel.
    if domain_escalation is not None:
        result = domain_escalation(finding=finding, existing_findings=existing_findings)
        if result is not None and result.triggered:
            return result

    # 8. Resource limit approaching
    if session is not None and session.token_budget_used > 0:
        budget_estimate = session.iterations * 8000
        if budget_estimate > 0:
            utilization = session.token_budget_used / budget_estimate
            if utilization >= RESOURCE_LIMIT_THRESHOLD:
                return EscalationResult(
                    triggered=True,
                    trigger=EscalationTrigger.resource_limit_approaching,
                    detail=f"Token budget utilization at {utilization:.0%}",
                )

    return EscalationResult(triggered=False)
