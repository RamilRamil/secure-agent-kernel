"""Mythril tool tests — parser + run_mythril wiring (T051). No Docker."""
import json
import pytest
from pathlib import Path

from sr_agent.tools.sandbox import SandboxResult
from sr_agent.packs.audit.tools.static_analysis import (
    MythrilError,
    parse_mythril_json,
    run_mythril,
)

SAMPLE = json.dumps({
    "error": None,
    "success": True,
    "issues": [
        {"title": "External Call To User-Supplied Address", "swc-id": "107",
         "severity": "Low", "function": "withdraw(uint256)",
         "description": "A call to a user-supplied address is executed."},
        {"title": "State access after external call", "swc-id": "107",
         "severity": "Medium", "function": "withdraw(uint256)",
         "description_head": "Read of persistent state following external call.",
         "description_tail": "Consider checks-effects-interactions."},
    ],
})


class _FakeSandbox:
    def __init__(self, result: SandboxResult) -> None:
        self._result = result
        self.calls: list[dict] = []

    def run(self, image, command, mounts=None, timeout_s=None, network="none", workdir=None):
        self.calls.append({"image": image, "command": command})
        return self._result


# ── parser ───────────────────────────────────────────────────────────────────

def test_parse_basic():
    findings = parse_mythril_json(SAMPLE)
    assert len(findings) == 2
    assert findings[0].swc_id == "107"
    assert findings[0].severity == "Low"
    assert findings[0].severity_norm == "low"


def test_parse_description_head_tail_combined():
    findings = parse_mythril_json(SAMPLE)
    assert "Read of persistent state" in findings[1].description
    assert "checks-effects-interactions" in findings[1].description


def test_parse_no_issues():
    assert parse_mythril_json(json.dumps({"issues": []})) == []


def test_parse_missing_issues_key():
    assert parse_mythril_json(json.dumps({"success": True})) == []


def test_parse_invalid_json_raises():
    with pytest.raises(MythrilError):
        parse_mythril_json("<<not json>>")


# ── run_mythril wiring ───────────────────────────────────────────────────────

@pytest.fixture
def audit_root(tmp_path: Path) -> Path:
    (tmp_path / "Vault.sol").write_text("// solidity")
    return tmp_path


def test_run_mythril_builds_command(audit_root):
    fake = _FakeSandbox(SandboxResult(exit_code=0, stdout=SAMPLE, stderr=""))
    findings = run_mythril(audit_root / "Vault.sol", audit_root, fake)
    assert len(findings) == 2
    cmd = fake.calls[0]["command"]
    assert cmd[0] == "analyze"
    assert cmd[1] == "/audit/contracts/Vault.sol"
    assert "-o" in cmd and "json" in cmd
    assert "--solv" in cmd and "0.8.20" in cmd


def test_run_mythril_max_depth(audit_root):
    fake = _FakeSandbox(SandboxResult(exit_code=0, stdout=SAMPLE, stderr=""))
    run_mythril(audit_root / "Vault.sol", audit_root, fake, max_depth=22)
    cmd = fake.calls[0]["command"]
    assert "--max-depth" in cmd and "22" in cmd


def test_run_mythril_empty_output_raises(audit_root):
    fake = _FakeSandbox(SandboxResult(exit_code=1, stdout="", stderr="boom"))
    with pytest.raises(MythrilError, match="no JSON output"):
        run_mythril(audit_root / "Vault.sol", audit_root, fake)


def test_run_mythril_outside_root_raises(audit_root, tmp_path):
    fake = _FakeSandbox(SandboxResult(exit_code=0, stdout=SAMPLE, stderr=""))
    with pytest.raises(MythrilError, match="outside audit root"):
        run_mythril(tmp_path.parent / "X.sol", audit_root, fake)
