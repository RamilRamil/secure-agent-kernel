"""Audit report generation tests (T062)."""
import pytest

from sr_agent.models.audit import Stage1Report
from sr_agent.io.report import generate_report


def _f(fid, severity, **kw):
    base = {
        "finding_id": fid, "severity": severity,
        "location": "Vault.sol:18", "function_name": "withdraw",
    }
    base.update(kw)
    return base


def test_report_has_title_and_summary():
    md = generate_report("demo", [_f("H-1", "high")])
    assert "# Security Audit — demo" in md
    assert "## Summary" in md
    assert "| High | 1 |" in md


def test_findings_ordered_by_severity():
    findings = [_f("L-1", "low"), _f("C-1", "critical"), _f("M-1", "medium")]
    md = generate_report("demo", findings)
    # critical heading appears before medium before low
    assert md.index("C-1") < md.index("M-1") < md.index("L-1")


def test_notes_and_tag_rendered():
    md = generate_report("demo", [_f("H-1", "high", bastet_tag="reentrancy", notes="external call before state update")])
    assert "reentrancy" in md
    assert "external call before state update" in md


def test_unverified_section_separated():
    findings = [_f("H-1", "high"), _f("U-1", "medium", status="unverified")]
    md = generate_report("demo", findings)
    assert "## Unverified Findings" in md
    # U-1 is under unverified, not in the main count
    assert "| Medium | 0 |" in md


def test_false_positive_hidden():
    md = generate_report("demo", [_f("FP-1", "high", status="false_positive")])
    assert "FP-1" not in md


def test_coverage_section_from_stage1():
    stage1 = Stage1Report(
        priority_targets=["Vault.sol:withdraw"],
        skipped_targets=["Vault.sol:deposit"],
    )
    md = generate_report("demo", [_f("H-1", "high")], stage1=stage1)
    assert "## Coverage" in md
    assert "Vault.sol:withdraw" in md
    assert "Vault.sol:deposit" in md


def test_empty_findings():
    md = generate_report("demo", [])
    assert "_No confirmed findings._" in md


def test_sanitizer_flags_surfaced():
    md = generate_report("demo", [_f("H-1", "high", notes="x", notes_flags=["zero_width_chars"])])
    assert "zero_width_chars" in md


def test_combined_with_rendered():
    md = generate_report("demo", [_f("H-1", "high", combined_with=["H-2"])])
    assert "Combined with" in md
    assert "H-2" in md


def test_combinations_section():
    md = generate_report("demo", [_f("H-1", "critical")],
                         combinations=["Vault.sol: 2 interacting high+ findings → critical chain"])
    assert "## Combination Chains" in md
    assert "critical chain" in md


def test_report_shows_all_engine_attributions():
    findings = [
        _f("SL-1", "high", engine="slither"),
        _f("MY-1", "medium", engine="mythril"),
        _f("SG-1", "high", engine="smartgraphical"),
        _f("M-1", "high", engine="model"),
    ]
    md = generate_report("demo", findings)
    for eng in ("slither", "mythril", "smartgraphical", "model"):
        assert f"**Engine**: {eng}" in md
