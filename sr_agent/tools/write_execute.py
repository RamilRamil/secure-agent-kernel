"""WRITE_EXECUTE tools (US4, T043).

These run only after out-of-band human confirmation (see confirmation.py) and,
where they execute code, only inside the network-isolated DockerSandbox.

  write_poc            — write a PoC test file to disk (no execution)
  run_tests            — `forge test` inside the sandbox
  deploy_test_contract — local Anvil / localhost only (hard guard)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sr_agent.tools.sandbox import DockerSandbox, Mount

logger = logging.getLogger(__name__)

ALLOWED_DEPLOY_NETWORKS: frozenset[str] = frozenset({"anvil", "localhost"})
FOUNDRY_IMAGE = "ghcr.io/foundry-rs/foundry:latest"


class DeployTargetError(Exception):
    """Raised when a deploy targets anything other than local Anvil/localhost."""


@dataclass
class PoCResult:
    finding_id: str
    path: Path
    framework: str


@dataclass
class TestResult:
    passed: bool
    exit_code: int
    stdout: str
    stderr: str


@dataclass
class DeployResult:
    network: str
    address: str | None
    success: bool
    detail: str


_FOUNDRY_STUB = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {{Test}} from "forge-std/Test.sol";

/// @notice PoC for finding {finding_id}
/// Auto-generated stub — fill in the exploit path, then run via the sandbox.
contract PoC_{ident} is Test {{
    function setUp() public {{}}

    function test_exploit_{ident}() public {{
        // TODO: reproduce finding {finding_id}
        // 1. set up the vulnerable state
        // 2. execute the attacker-controlled path
        // 3. assert the broken invariant
        revert("PoC not implemented");
    }}
}}
"""


def _ident(finding_id: str) -> str:
    """Turn a finding id like 'HIGH-001' into a Solidity-safe identifier."""
    return "".join(c if c.isalnum() else "_" for c in finding_id)


def write_poc(
    finding_id: str,
    poc_dir: Path,
    framework: str = "foundry",
    generator: Callable[[str], str] | None = None,
) -> PoCResult:
    """Write a PoC test file for a finding. Does NOT execute anything.

    By default writes a deterministic Foundry stub. A `generator` callable
    (e.g. an LLM-backed Qwen3-Coder function) may be injected to produce the
    test body; its output is treated as data — written to disk, never executed
    here. Execution happens later via run_tests, under sandbox + confirmation.
    """
    if framework != "foundry":
        raise ValueError(f"Unsupported framework: {framework!r}")

    poc_dir.mkdir(parents=True, exist_ok=True)
    ident = _ident(finding_id)
    content = (
        generator(finding_id)
        if generator is not None
        else _FOUNDRY_STUB.format(finding_id=finding_id, ident=ident)
    )
    path = poc_dir / f"{ident}.t.sol"
    path.write_text(content, encoding="utf-8")
    logger.info("Wrote PoC for %s to %s", finding_id, path)
    return PoCResult(finding_id=finding_id, path=path, framework=framework)


def run_tests(
    project_dir: Path,
    sandbox: DockerSandbox,
    test_path: str | None = None,
    image: str = FOUNDRY_IMAGE,
    timeout_s: float = 180.0,
    foundry_test_dir: str | None = None,
) -> TestResult:
    """Run `forge test` inside the network-isolated sandbox.

    `foundry_test_dir` sets the Foundry test-discovery dir via the FOUNDRY_TEST
    env override (audit-pack, contracts/poc-execution.md) — required when PoCs
    live outside the default `test/` dir (e.g. `audit/poc/`), else forge reports
    "No tests to run". `via_ir` and remappings are inherited from the default
    profile unchanged.

    `ghcr.io/foundry-rs/foundry` has `ENTRYPOINT=["/bin/sh","-c"]` (shell form) —
    passing the command as an argv list double-wraps under the image's own
    `sh -c` and the real command is never actually run: `sh -c "sh" "-c" "cmd"`
    executes the *script text* "sh" with "-c"/"cmd" as its $0/$1, which just
    reads stdin and exits 0 with no output (see docs/roadmap.md gotcha #3,
    root-caused 2026-07-02 — a "passed" PoC test that never actually compiled).
    The fix is to always hand the image a SINGLE command string.

    Always passes `--offline`: this sandbox always runs with `--network none`
    (Constitution I), so forge WILL fail to reach binaries.soliditylang.org
    regardless; `--offline` makes it fail cleanly ("solc not installed") using
    whatever solc is already cached in the image rather than hanging on a DNS
    timeout (docs/roadmap.md gotcha #6 — `--offline` must NOT be combined with
    `--use`, a confirmed upstream bug, foundry-rs/foundry#2412 — omit `--use`
    and let forge auto-detect the pragma-resolved version from the cache).
    A Foundry image used with this sandbox MUST have its target project's solc
    version(s) pre-baked (see docker/Dockerfile.foundry) or every run fails.
    """
    inner = "forge test --offline"
    if test_path:
        inner += f" --match-path {test_path}"
    if foundry_test_dir:
        inner = f"FOUNDRY_TEST={foundry_test_dir} {inner}"
    command = [inner]

    # The project workspace is mounted rw so forge can write build artifacts;
    # isolation comes from --network none + ephemeral container + dropped caps.
    mounts = [Mount(host_path=project_dir, container_path="/work", read_only=False)]
    result = sandbox.run(
        image, command, mounts=mounts, workdir="/work", timeout_s=timeout_s
    )
    return TestResult(
        passed=result.ok,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def deploy_test_contract(
    network: str,
    deployer: Callable[[], DeployResult] | None = None,
) -> DeployResult:
    """Deploy a test contract — local Anvil / localhost only.

    The network guard is enforced here as defense in depth, mirroring
    validate_action: a deploy to mainnet or any live network is refused before
    any deployer is invoked.
    """
    if network not in ALLOWED_DEPLOY_NETWORKS:
        raise DeployTargetError(
            f"deploy_test_contract only allowed on {sorted(ALLOWED_DEPLOY_NETWORKS)}, "
            f"got {network!r}"
        )
    if deployer is None:
        return DeployResult(
            network=network, address=None, success=False,
            detail="no deployer configured (dry run)",
        )
    return deployer()
