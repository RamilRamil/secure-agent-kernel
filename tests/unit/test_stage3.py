"""Stage 3 deterministic synthesis tests (T056)."""
import pytest

from sr_agent.models.finding import Finding, Severity
from sr_agent.planner.stage3 import run_stage3


def _f(fid, severity, location="Vault.sol:1", **kw) -> Finding:
    return Finding(finding_id=fid, location=location, function_name="f",
                   severity=severity, **kw)


# ── severity correction (pass 1) ─────────────────────────────────────────────

def test_mitigation_downgrades():
    f = _f("H-1", Severity.high, mitigations_present=["reentrancy_guard"])
    result = run_stage3([f])
    assert f.severity is Severity.medium
    assert result.corrections


def test_preconditions_upgrade():
    f = _f("L-1", Severity.low, location="A.sol:1",
           preconditions={1: True, 2: True, 3: True, 4: True})
    run_stage3([f])
    assert f.severity is Severity.high


# ── combination (pass 2) ─────────────────────────────────────────────────────

def test_combined_with_links_same_file():
    a = _f("A-1", Severity.medium, location="Vault.sol:10")
    b = _f("B-1", Severity.medium, location="Vault.sol:20")
    run_stage3([a, b])
    assert a.combined_with == ["B-1"]
    assert b.combined_with == ["A-1"]


def test_different_files_not_combined():
    a = _f("A-1", Severity.high, location="A.sol:1")
    b = _f("B-1", Severity.high, location="B.sol:1")
    run_stage3([a, b])
    assert a.combined_with == []
    assert b.combined_with == []


def test_critical_chain_elevation():
    a = _f("A-1", Severity.high, location="Vault.sol:10")
    b = _f("B-1", Severity.high, location="Vault.sol:20")
    result = run_stage3([a, b])
    assert a.severity is Severity.critical
    assert b.severity is Severity.critical
    assert result.combinations


def test_chain_requires_two_severe():
    a = _f("A-1", Severity.high, location="Vault.sol:10")
    b = _f("B-1", Severity.low, location="Vault.sol:20")
    result = run_stage3([a, b])
    assert a.severity is Severity.high  # not elevated — only one severe
    assert result.combinations == []


def test_mitigated_finding_excluded_from_chain():
    a = _f("A-1", Severity.high, location="Vault.sol:10")
    b = _f("B-1", Severity.high, location="Vault.sol:20",
           mitigations_present=["checks_effects_interactions"])
    result = run_stage3([a, b])
    # b downgraded to medium in pass 1 -> only one severe -> no chain
    assert b.severity is Severity.medium
    assert a.severity is Severity.high
    assert result.combinations == []
