"""Static-analysis tools that run inside the DockerSandbox (T050).

run_slither executes Slither in the ephemeral, network-isolated sandbox and
parses its JSON output. The parser is pure and tested without Docker; the live
run is exercised by an auto-skipping integration test.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from sr_agent.tools.sandbox import DockerSandbox, Mount

logger = logging.getLogger(__name__)

SLITHER_IMAGE = "slither-sandbox"
_CONTAINER_MOUNT = "/audit/contracts"

# Slither impact -> our Severity vocabulary (str, mapped loosely).
SLITHER_IMPACT_TO_SEVERITY = {
    "High": "high",
    "Medium": "medium",
    "Low": "low",
    "Informational": "informational",
    "Optimization": "informational",
}


class SlitherError(Exception):
    pass


@dataclass
class SlitherFinding:
    check: str
    impact: str
    confidence: str
    description: str

    @property
    def severity(self) -> str:
        return SLITHER_IMPACT_TO_SEVERITY.get(self.impact, "informational")


def parse_slither_json(stdout: str) -> list[SlitherFinding]:
    """Parse `slither --json -` output into findings."""
    try:
        data = json.loads(stdout)
    except Exception as e:
        raise SlitherError(f"could not parse slither JSON: {e}") from e

    detectors = ((data.get("results") or {}).get("detectors")) or []
    findings: list[SlitherFinding] = []
    for d in detectors:
        findings.append(
            SlitherFinding(
                check=d.get("check", ""),
                impact=d.get("impact", ""),
                confidence=d.get("confidence", ""),
                description=(d.get("description", "") or "").strip(),
            )
        )
    return findings


def run_slither(
    target: str | Path,
    audit_root: str | Path,
    sandbox: DockerSandbox,
    image: str = SLITHER_IMAGE,
    detectors: list[str] | None = None,
    timeout_s: float = 300.0,
) -> list[SlitherFinding]:
    """Run Slither on a target inside the sandbox and return parsed findings."""
    audit_root = Path(audit_root).resolve()
    target = Path(target).resolve()
    try:
        rel = target.relative_to(audit_root)
    except ValueError as e:
        raise SlitherError(f"target {target} is outside audit root {audit_root}") from e

    mounts = [Mount(host_path=audit_root, container_path=_CONTAINER_MOUNT, read_only=True)]
    command = [f"{_CONTAINER_MOUNT}/{rel}", "--json", "-"]
    if detectors:
        command += ["--detect", ",".join(detectors)]

    # Slither exits non-zero when it reports issues; the JSON is still on stdout.
    result = sandbox.run(image, command, mounts=mounts, timeout_s=timeout_s)
    if not result.stdout.strip():
        raise SlitherError(
            f"slither produced no JSON output (stderr: {result.stderr[:300]})"
        )
    findings = parse_slither_json(result.stdout)
    logger.info("Slither on %s: %d findings", rel, len(findings))
    return findings
