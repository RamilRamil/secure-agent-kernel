"""Audit domain-escalation triggers #3-#7 fire through the kernel (feature 004, R5).

Locks in the escalation split: the kernel's evaluate_triggers runs the generic
guards, then the pack's domain_escalation, preserving order and behavior.
"""
from __future__ import annotations

from sr_agent.guardrails.escalation import evaluate_triggers
from sr_agent.packs.audit.finding import Finding, FindingStatus, Severity
from sr_agent.packs.audit.escalation import domain_escalation


def _eval(finding=None, existing=None):
    return evaluate_triggers(
        action=None, record=None, finding=finding, session=None,
        existing_findings=existing, domain_escalation=domain_escalation,
    )


def test_trigger3_critical_finding():
    f = Finding(finding_id="F1", location="V.sol:10", function_name="w", severity=Severity.critical)
    r = _eval(f)
    assert r.triggered and r.trigger.value == "critical_finding"


def test_trigger4_unverified_high():
    # #4 requires status==unverified explicitly (default is `open` → falls to #7)
    f = Finding(finding_id="F2", location="V.sol:11", function_name="w",
                severity=Severity.high, status=FindingStatus.unverified)
    r = _eval(f)
    assert r.triggered and r.trigger.value == "unverified_high"


def test_trigger7_unknown_pattern_no_tag():
    # medium severity, no bastet_tag → unknown pattern
    f = Finding(finding_id="F3", location="V.sol:12", function_name="w", severity=Severity.medium)
    r = _eval(f)
    assert r.triggered and r.trigger.value == "unknown_pattern"


def test_no_finding_no_domain_trigger():
    r = _eval(finding=None)
    assert not r.triggered


def test_low_severity_with_no_tag_does_not_trigger():
    # low/informational are exempt from the unknown-pattern trigger
    f = Finding(finding_id="F4", location="V.sol:13", function_name="w", severity=Severity.low)
    r = _eval(f)
    assert not r.triggered
