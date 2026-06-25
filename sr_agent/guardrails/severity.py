from __future__ import annotations

import logging
from dataclasses import dataclass

from sr_agent.models.finding import Finding, Severity

logger = logging.getLogger(__name__)

# Severity ordering for comparison
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.informational: 0,
    Severity.low: 1,
    Severity.medium: 2,
    Severity.high: 3,
    Severity.critical: 4,
}


@dataclass
class SeverityVerdict:
    original: Severity
    corrected: Severity | None     # None = no correction needed
    correction_reason: str | None

    @property
    def was_corrected(self) -> bool:
        return self.corrected is not None

    @property
    def final(self) -> Severity:
        return self.corrected if self.corrected else self.original


def check_severity(finding: Finding) -> SeverityVerdict:
    """Deterministic severity validation — no LLM involved.

    Two rules from the AttackPathGNN conjunction analysis:

    Rule 1 (SCB — Severity Correction Bias):
      ANY mitigation present → severity must be ≤ medium.
      Rationale: a mitigated vulnerability is less exploitable. An LLM
      inflating severity above medium on a mitigated finding is likely
      hallucinating exploitability or responding to injected context.

    Rule 2 (Precondition threshold):
      ≥4 active preconditions AND no mitigations → severity must be ≥ high.
      Rationale: many satisfied preconditions = high exploitability. An LLM
      deflating severity here may be hiding a real finding.
    """
    severity = finding.severity
    active_preconditions = sum(1 for v in finding.preconditions.values() if v)
    has_mitigations = len(finding.mitigations_present) > 0

    # Rule 1: mitigation present → cap at medium
    if has_mitigations and _SEVERITY_RANK[severity] > _SEVERITY_RANK[Severity.medium]:
        corrected = Severity.medium
        reason = (
            f"Severity downgraded {severity.value} → medium: "
            f"mitigations present ({', '.join(finding.mitigations_present)}). "
            "SCB correction applied."
        )
        logger.info("Severity correction: %s", reason)
        return SeverityVerdict(original=severity, corrected=corrected, correction_reason=reason)

    # Rule 2: high precondition count + no mitigations → floor at high
    if active_preconditions >= 4 and not has_mitigations:
        if _SEVERITY_RANK[severity] < _SEVERITY_RANK[Severity.high]:
            corrected = Severity.high
            reason = (
                f"Severity upgraded {severity.value} → high: "
                f"{active_preconditions} active preconditions, no mitigations. "
                "Precondition threshold correction applied."
            )
            logger.info("Severity correction: %s", reason)
            return SeverityVerdict(original=severity, corrected=corrected, correction_reason=reason)

    return SeverityVerdict(original=severity, corrected=None, correction_reason=None)
