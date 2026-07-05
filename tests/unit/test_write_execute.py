"""WRITE_EXECUTE tool tests (US4, T043).

No Docker required: run_tests is exercised with a fake sandbox, and the deploy
guard is pure logic. Verifies the security-critical anvil-only restriction.
"""
import pytest

from sr_agent.tools.sandbox import SandboxResult
from sr_agent.packs.audit.tools.write_execute import (
    DeployResult,
    DeployTargetError,
    deploy_test_contract,
    run_tests,
    write_poc,
)


# ── write_poc ────────────────────────────────────────────────────────────────

def test_write_poc_creates_stub(tmp_path):
    res = write_poc("HIGH-001", tmp_path)
    assert res.path.exists()
    assert res.path.name == "HIGH_001.t.sol"
    content = res.path.read_text()
    assert "HIGH-001" in content
    assert "contract PoC_HIGH_001" in content


def test_write_poc_with_generator(tmp_path):
    res = write_poc("X-1", tmp_path, generator=lambda fid: f"// custom {fid}")
    assert res.path.read_text() == "// custom X-1"


def test_write_poc_unsupported_framework(tmp_path):
    with pytest.raises(ValueError):
        write_poc("H-1", tmp_path, framework="hardhat")


# ── run_tests (fake sandbox) ─────────────────────────────────────────────────

class _FakeSandbox:
    def __init__(self, result: SandboxResult) -> None:
        self._result = result
        self.calls: list[dict] = []

    def run(self, image, command, mounts=None, timeout_s=None, network="none", workdir=None, env=None):
        self.calls.append(
            {"image": image, "command": command, "mounts": mounts, "workdir": workdir,
             "network": network, "env": env}
        )
        return self._result


def test_run_tests_invokes_sandbox(tmp_path):
    fake = _FakeSandbox(SandboxResult(exit_code=0, stdout="PASS", stderr=""))
    res = run_tests(tmp_path, fake, test_path="PoC.t.sol")
    assert res.passed
    # Single command STRING (not an argv list) so the foundry image's shell-form
    # ENTRYPOINT runs it directly; always --offline (network-isolated sandbox).
    # See write_execute.py::run_tests and docs/roadmap.md gotchas #3/#6.
    assert fake.calls[0]["command"] == ["forge test --offline --match-path PoC.t.sol"]
    assert fake.calls[0]["workdir"] == "/work"


def test_run_tests_reports_failure(tmp_path):
    fake = _FakeSandbox(SandboxResult(exit_code=1, stdout="", stderr="boom"))
    res = run_tests(tmp_path, fake)
    assert not res.passed
    assert res.exit_code == 1


# ── deploy_test_contract (anvil-only guard) ──────────────────────────────────

def test_deploy_rejects_mainnet():
    with pytest.raises(DeployTargetError):
        deploy_test_contract("mainnet")


def test_deploy_rejects_arbitrary_rpc():
    with pytest.raises(DeployTargetError):
        deploy_test_contract("https://eth.llamarpc.com")


def test_deploy_allows_anvil_dry_run():
    res = deploy_test_contract("anvil")
    assert res.network == "anvil"
    assert not res.success  # no deployer => dry run


def test_deploy_uses_injected_deployer():
    res = deploy_test_contract(
        "localhost",
        deployer=lambda: DeployResult("localhost", "0xabc", True, "deployed"),
    )
    assert res.success
    assert res.address == "0xabc"
