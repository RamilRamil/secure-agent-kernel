"""SmartGraphical engine integration (feature 002).

SmartGraphical is a third deterministic analysis engine (logic rules + a
structural read/write + call graph), complementary to Slither (syntactic) and
Mythril (symbolic). It is invoked as an external tool: a subprocess running
SmartGraphical's own interpreter drives its web_api facade and prints clean JSON
(findings + graph). SR-agent parses that and maps it onto Finding + the SIG.

Per research.md R1: the `sg_cli ... json` path emits only a summary and pollutes
stdout, so we drive `web_api.analyze_all` / `web_api.graph` instead.

Output is consumed as DATA — parsed, mapped, stored as tool_output hypotheses,
never executed. Findings are confirmed only by PoC.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sr_agent.models.finding import BastetTag, Finding, Severity

logger = logging.getLogger(__name__)

SG_CONFIDENCE_TO_SEVERITY = {"high": "high", "medium": "medium", "low": "low"}

# SmartGraphical rule_id -> BastetTag (best effort; unmapped -> None). research R3.
SG_RULE_TO_TAG: dict[str, BastetTag] = {
    "read_only_oracle_reentrancy": BastetTag.read_only_reentrancy,
    "unstake_share_burn_order": BastetTag.reentrancy,
    "bridge_retry_reentrancy": BastetTag.cross_function_reentrancy,
    "check_order": BastetTag.oracle_manipulation,
    "min_slippage_bounds": BastetTag.sandwich_attack,
    "outer_calls": BastetTag.missing_access_control,
    "unallowed_manipulation": BastetTag.missing_access_control,
    "withdraw_check": BastetTag.logic_error,
    "local_points": BastetTag.logic_error,
    "staking": BastetTag.logic_error,
    "pool_interactions": BastetTag.erc20_compliance,
    "tainted_input_unguarded_sink": BastetTag.missing_check,
}


class SmartGraphicalError(Exception):
    pass


@dataclass
class SGFinding:
    rule_id: str
    task_id: str
    title: str
    category: str
    confidence: str
    message: str
    remediation_hint: str
    function: str = ""
    line: int | None = None


def _findings_from_data(data) -> list[SGFinding]:
    findings = data.get("findings", data) if isinstance(data, dict) else data
    if not isinstance(findings, list):
        return []
    out: list[SGFinding] = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        ev = (f.get("evidences") or [{}])[0]
        line = ev.get("line_number") or 0
        if not line:
            lns = ev.get("line_numbers") or []
            line = lns[0] if lns else 0
        out.append(
            SGFinding(
                rule_id=f.get("rule_id", ""),
                task_id=str(f.get("task_id", "")),
                title=f.get("title", ""),
                category=f.get("category", ""),
                confidence=f.get("confidence", ""),
                message=f.get("message", ""),
                remediation_hint=f.get("remediation_hint", ""),
                function=ev.get("function_name", ""),
                line=line or None,
            )
        )
    return out


def parse_sg_findings(stdout: str) -> list[SGFinding]:
    """Parse SmartGraphical JSON output into SGFinding (tolerant).

    Accepts either the {findings, graph} wrapper or a bare findings list. Empty
    output -> []; non-empty non-JSON -> SmartGraphicalError.
    """
    if not stdout.strip():
        return []
    try:
        data = json.loads(stdout)
    except Exception as e:
        raise SmartGraphicalError(f"could not parse SmartGraphical JSON: {e}") from e
    return _findings_from_data(data)


def sg_to_findings(
    sg_findings: list[SGFinding], file_rel: str, id_prefix: str = "SG"
) -> list[Finding]:
    """Map SGFindings on a known file to Finding objects (severity from
    confidence, bastet_tag via lookup, location file_rel:line)."""
    out: list[Finding] = []
    for i, sf in enumerate(sg_findings, 1):
        loc = f"{file_rel}:{sf.line}" if sf.line else file_rel
        severity = SG_CONFIDENCE_TO_SEVERITY.get(sf.confidence, "low")
        out.append(
            Finding(
                finding_id=f"{id_prefix}-{i:03d}",
                location=loc,
                function_name=sf.function or "unknown",
                severity=Severity(severity),
                bastet_tag=SG_RULE_TO_TAG.get(sf.rule_id),
            )
        )
    return out


_FACADE_SCRIPT = (
    "import json,sys; from smartgraphical.services import web_api; "
    "t=sys.argv[1]; f=web_api.analyze_all(t, mode='auditor'); g=web_api.graph(t); "
    "ms=g.get('model_summary',{}) if isinstance(g,dict) else {}; "
    "gr=ms.get('graph',{}) if isinstance(ms,dict) else {}; "
    "print(json.dumps({'findings':f.get('findings',[]),"
    "'graph':{'nodes':gr.get('nodes',[]),'edges':gr.get('edges',[])}}))"
)


def run_smartgraphical(
    target: str | Path,
    audit_root: str | Path,
    sg_root: str | Path,
    sg_python: str | Path | None = None,
    timeout_s: float = 120.0,
) -> tuple[list[SGFinding], dict]:
    """Run SmartGraphical on one file via its web_api facade subprocess.

    Returns (findings, graph). Raises SmartGraphicalError when SmartGraphical is
    unavailable or produces nothing usable.
    """
    sg_root = Path(sg_root)
    sg_python = Path(sg_python) if sg_python else sg_root / ".venv" / "bin" / "python"
    if not sg_python.exists():
        raise SmartGraphicalError(f"SmartGraphical interpreter not found: {sg_python}")

    target = Path(target).resolve()
    try:
        proc = subprocess.run(
            [str(sg_python), "-c", _FACADE_SCRIPT, str(target)],
            cwd=str(sg_root), capture_output=True, text=True, timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise SmartGraphicalError(f"SmartGraphical run failed: {e}") from e

    if not proc.stdout.strip():
        raise SmartGraphicalError(
            f"SmartGraphical produced no output (stderr: {proc.stderr[:300]})"
        )
    try:
        data = json.loads(proc.stdout)
    except Exception as e:
        raise SmartGraphicalError(f"could not parse SmartGraphical JSON: {e}") from e

    findings = _findings_from_data(data)
    graph = data.get("graph", {}) if isinstance(data, dict) else {}
    logger.info("SmartGraphical on %s: %d findings", target.name, len(findings))
    return findings, graph
