"""Slither tool tests — parser + run_slither wiring (T050). No Docker."""
import json
import pytest
from pathlib import Path

from sr_agent.models.finding import BastetTag, Severity
from sr_agent.tools.sandbox import SandboxResult
from sr_agent.tools.static_analysis import (
    SlitherError,
    parse_slither_json,
    run_slither,
    slither_to_findings,
)

SAMPLE_WITH_ELEMENTS = json.dumps({
    "results": {"detectors": [
        {"check": "reentrancy-eth", "impact": "High", "confidence": "Medium",
         "description": "Reentrancy in withdraw",
         "elements": [
             {"type": "function", "name": "withdraw",
              "source_mapping": {"lines": [16, 17, 18]}},
             {"type": "node", "name": "x", "source_mapping": {"lines": [19]}},
         ]},
    ]},
})

SAMPLE = json.dumps({
    "success": True,
    "error": None,
    "results": {"detectors": [
        {"check": "reentrancy-eth", "impact": "High", "confidence": "Medium",
         "description": "Reentrancy in Vault.withdraw(uint256)"},
        {"check": "solc-version", "impact": "Informational", "confidence": "High",
         "description": "Pragma version constraint"},
    ]},
})


class _FakeSandbox:
    def __init__(self, result: SandboxResult) -> None:
        self._result = result
        self.calls: list[dict] = []

    def run(self, image, command, mounts=None, timeout_s=None, network="none", workdir=None):
        self.calls.append({"image": image, "command": command, "mounts": mounts})
        return self._result


# ── parser ───────────────────────────────────────────────────────────────────

def test_parse_basic():
    findings = parse_slither_json(SAMPLE)
    assert len(findings) == 2
    assert findings[0].check == "reentrancy-eth"
    assert findings[0].impact == "High"
    assert findings[0].confidence == "Medium"


def test_parse_severity_mapping():
    findings = parse_slither_json(SAMPLE)
    assert findings[0].severity == "high"
    assert findings[1].severity == "informational"


def test_parse_empty_detectors():
    assert parse_slither_json(json.dumps({"results": {"detectors": []}})) == []


def test_parse_missing_results():
    assert parse_slither_json(json.dumps({"success": True})) == []


def test_parse_invalid_json_raises():
    with pytest.raises(SlitherError):
        parse_slither_json("not json")


def test_parse_extracts_function_and_line():
    f = parse_slither_json(SAMPLE_WITH_ELEMENTS)[0]
    assert f.function == "withdraw"
    assert f.line == 16


def test_slither_to_findings_maps_fields():
    findings = slither_to_findings(parse_slither_json(SAMPLE_WITH_ELEMENTS), "Vault.sol")
    assert len(findings) == 1
    assert findings[0].location == "Vault.sol:16"
    assert findings[0].function_name == "withdraw"
    assert findings[0].severity is Severity.high
    assert findings[0].bastet_tag is BastetTag.reentrancy


# ── run_slither wiring ───────────────────────────────────────────────────────

@pytest.fixture
def audit_root(tmp_path: Path) -> Path:
    (tmp_path / "Vault.sol").write_text("// solidity")
    return tmp_path


def test_run_slither_builds_command_and_parses(audit_root):
    # slither exits non-zero when it reports issues; JSON still on stdout
    fake = _FakeSandbox(SandboxResult(exit_code=255, stdout=SAMPLE, stderr=""))
    findings = run_slither(audit_root / "Vault.sol", audit_root, fake)
    assert len(findings) == 2
    cmd = fake.calls[0]["command"]
    assert cmd[0].endswith("Vault.sol")
    assert "--json" in cmd
    assert fake.calls[0]["image"] == "slither-sandbox"


def test_run_slither_empty_output_raises(audit_root):
    fake = _FakeSandbox(SandboxResult(exit_code=1, stdout="", stderr="compile error"))
    with pytest.raises(SlitherError, match="no JSON output"):
        run_slither(audit_root / "Vault.sol", audit_root, fake)


def test_run_slither_target_outside_root_raises(audit_root, tmp_path):
    outside = tmp_path.parent / "Other.sol"
    fake = _FakeSandbox(SandboxResult(exit_code=0, stdout=SAMPLE, stderr=""))
    with pytest.raises(SlitherError, match="outside audit root"):
        run_slither(outside, audit_root, fake)


def test_run_slither_detectors_passed(audit_root):
    fake = _FakeSandbox(SandboxResult(exit_code=0, stdout=SAMPLE, stderr=""))
    run_slither(audit_root / "Vault.sol", audit_root, fake, detectors=["reentrancy-eth"])
    cmd = fake.calls[0]["command"]
    assert "--detect" in cmd
    assert "reentrancy-eth" in cmd
