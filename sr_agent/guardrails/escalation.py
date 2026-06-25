from __future__ import annotations

import logging
from dataclasses import dataclass

from sr_agent.llm_core.schemas import EscalationTrigger
from sr_agent.models.action import Action, ActionClass
from sr_agent.models.audit import AuditSession
from sr_agent.models.finding import Finding, Severity
from sr_agent.models.memory import MemoryRecord, SourceType

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
    finding: Finding | None,
    session: AuditSession,
    existing_findings: list[Finding] | None = None,
) -> EscalationResult:
    """Evaluate all 8 escalation triggers. Returns first match, or no-trigger.

    All checks are deterministic — no LLM calls. The LLM may also set
    escalation_trigger in AgentAction, but these checks are independent
    and cannot be suppressed by injected context.
    """

    # 1. Irreversible action requested
    if action and action.action_class == ActionClass.write_execute:
        if not action.is_reversible:
            return EscalationResult(
                triggered=True,
                trigger=EscalationTrigger.irreversible_action,
                detail=f"Action '{action.action_type.value}' is irreversible",
            )

    # 2. Memory status change (any status_change record from non-human source)
    if record and record.status_change:
        if record.source_type != SourceType.human_input:
            return EscalationResult(
                triggered=True,
                trigger=EscalationTrigger.memory_status_change,
                detail=(
                    f"Status change '{record.status_change.new_status}' "
                    f"from source_type={record.source_type.value} — requires human review"
                ),
            )

    # 3. Critical finding
    if finding and finding.severity == Severity.critical:
        return EscalationResult(
            triggered=True,
            trigger=EscalationTrigger.critical_finding,
            detail=f"Critical severity finding at {finding.location}",
        )

    # 4. Unverified high severity
    if finding and finding.severity == Severity.high:
        from sr_agent.models.finding import FindingStatus
        if finding.status == FindingStatus.unverified:
            return EscalationResult(
                triggered=True,
                trigger=EscalationTrigger.unverified_high,
                detail=f"Unverified high-severity finding {finding.finding_id} at {finding.location}",
            )

    # 5. Mock test detected (PoC uses mock patterns — checked separately in mock_detect.py)
    if finding and finding.poc_status:
        from sr_agent.models.finding import PoCStatus
        if finding.poc_status == PoCStatus.mock_review:
            return EscalationResult(
                triggered=True,
                trigger=EscalationTrigger.mock_test,
                detail=f"PoC for {finding.finding_id} uses mock patterns — human review required",
            )

    # 6. Contradicting findings — same location, conflicting severity/status
    if finding and existing_findings:
        for existing in existing_findings:
            if existing.location == finding.location and existing.function_name == finding.function_name:
                existing_rank = _severity_rank(existing.severity)
                new_rank = _severity_rank(finding.severity)
                if abs(existing_rank - new_rank) >= 2:
                    return EscalationResult(
                        triggered=True,
                        trigger=EscalationTrigger.contradicting_findings,
                        detail=(
                            f"Contradicting severity at {finding.location}: "
                            f"existing={existing.severity.value}, new={finding.severity.value}"
                        ),
                    )

    # 7. Unknown pattern — bastet_tag is None on a non-informational finding
    if finding and finding.bastet_tag is None and finding.severity not in (
        Severity.informational, Severity.low
    ):
        return EscalationResult(
            triggered=True,
            trigger=EscalationTrigger.unknown_pattern,
            detail=f"No Bastet tag on {finding.severity.value} finding {finding.finding_id}",
        )

    # 8. Resource limit approaching
    if session.token_budget_used > 0:
        # Rough estimate: MAX_ITERATIONS * avg_tokens_per_iter
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


def _severity_rank(s: Severity) -> int:
    return {"informational": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}[s.value]
