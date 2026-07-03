import pytest

from sr_agent.packs.audit.guardrails.severity import check_severity
from sr_agent.models.finding import Finding, Severity


def _finding(**kwargs) -> Finding:
    defaults = dict(
        finding_id="TEST-001",
        location="Vault.sol:10",
        function_name="withdraw",
        severity=Severity.high,
        preconditions={},
        mitigations_present=[],
    )
    defaults.update(kwargs)
    return Finding(**defaults)


def test_mitigation_present_downgrades_critical():
    f = _finding(severity=Severity.critical, mitigations_present=["reentrancy_guard"])
    verdict = check_severity(f)
    assert verdict.was_corrected
    assert verdict.final == Severity.medium


def test_mitigation_present_downgrades_high():
    f = _finding(severity=Severity.high, mitigations_present=["checks_effects_interactions"])
    verdict = check_severity(f)
    assert verdict.was_corrected
    assert verdict.final == Severity.medium


def test_mitigation_does_not_affect_low():
    f = _finding(severity=Severity.low, mitigations_present=["reentrancy_guard"])
    verdict = check_severity(f)
    assert not verdict.was_corrected


def test_four_preconditions_no_mitigations_upgrades_low():
    f = _finding(
        severity=Severity.low,
        preconditions={1: True, 2: True, 3: True, 4: True},
        mitigations_present=[],
    )
    verdict = check_severity(f)
    assert verdict.was_corrected
    assert verdict.final == Severity.high


def test_four_preconditions_with_mitigation_no_upgrade():
    """Mitigation rule takes precedence — severity stays corrected to medium."""
    f = _finding(
        severity=Severity.low,
        preconditions={1: True, 2: True, 3: True, 4: True},
        mitigations_present=["reentrancy_guard"],
    )
    verdict = check_severity(f)
    # mitigation rule fires first: low → no downgrade needed (already low), but
    # low < medium so Rule 1 doesn't trigger either. Rule 2 would upgrade to high
    # but mitigation is present → Rule 2 doesn't apply.
    assert not verdict.was_corrected


def test_severity_already_correct_no_correction():
    f = _finding(severity=Severity.high, preconditions={1: True, 2: True, 3: True, 4: True})
    verdict = check_severity(f)
    assert not verdict.was_corrected
