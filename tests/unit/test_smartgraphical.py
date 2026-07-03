"""SmartGraphical mapping tests (feature 002, US1). No SmartGraphical needed."""
import json
import pytest
from pathlib import Path

from sr_agent.models.finding import BastetTag, Severity
from sr_agent.packs.audit.tools.smartgraphical import (
    SGFinding,
    SmartGraphicalError,
    parse_sg_findings,
    sg_to_findings,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "smartgraphical"


def _sample_findings_json() -> str:
    return (_FIXTURES / "sample_findings.json").read_text()


# ── parse_sg_findings ────────────────────────────────────────────────────────

def test_parse_fixture_findings():
    findings = parse_sg_findings(_sample_findings_json())
    assert findings
    rule_ids = {f.rule_id for f in findings}
    assert "check_order" in rule_ids          # the logic-ordering finding
    co = next(f for f in findings if f.rule_id == "check_order")
    assert co.function == "withdraw"
    assert co.confidence == "medium"


def test_parse_empty_is_empty():
    assert parse_sg_findings("") == []
    assert parse_sg_findings("   ") == []


def test_parse_garbage_raises():
    with pytest.raises(SmartGraphicalError):
        parse_sg_findings("not json at all")


def test_parse_accepts_bare_list():
    raw = json.dumps([{"rule_id": "withdraw_check", "confidence": "medium",
                       "evidences": [{"function_name": "f", "line_number": 12}]}])
    findings = parse_sg_findings(raw)
    assert len(findings) == 1
    assert findings[0].line == 12


# ── sg_to_findings ───────────────────────────────────────────────────────────

def test_map_severity_and_tag():
    sg = [SGFinding(rule_id="check_order", task_id="8", title="t", category="x",
                    confidence="medium", message="m", remediation_hint="h",
                    function="withdraw", line=19)]
    findings = sg_to_findings(sg, "Vault.sol")
    assert findings[0].location == "Vault.sol:19"
    assert findings[0].function_name == "withdraw"
    assert findings[0].severity is Severity.medium
    assert findings[0].bastet_tag is BastetTag.oracle_manipulation


def test_unmapped_rule_has_no_tag():
    sg = [SGFinding(rule_id="similar_names", task_id="10", title="t", category="naming",
                    confidence="low", message="m", remediation_hint="h",
                    function="f", line=None)]
    findings = sg_to_findings(sg, "A.sol")
    assert findings[0].bastet_tag is None
    assert findings[0].location == "A.sol"          # no line -> file only
    assert findings[0].severity is Severity.low


def test_confidence_severity_mapping():
    for conf, sev in [("high", Severity.high), ("medium", Severity.medium), ("low", Severity.low)]:
        sg = [SGFinding(rule_id="r", task_id="1", title="t", category="c",
                        confidence=conf, message="m", remediation_hint="h")]
        assert sg_to_findings(sg, "X.sol")[0].severity is sev


def test_fixture_maps_to_findings():
    sgs = parse_sg_findings(_sample_findings_json())
    findings = sg_to_findings(sgs, "Vault.sol")
    assert len(findings) == len(sgs)
    assert any(f.bastet_tag is BastetTag.oracle_manipulation for f in findings)  # check_order
