"""Audit report generation (T062).

Pure, deterministic Markdown rendering over finding payloads. No services.

Findings are passed as the dicts stored in memory (Finding fields + optional
'notes'). Severity-first ordering; unverified findings are separated; a Coverage
section lists what Stage 1 prioritized vs skipped.
"""
from __future__ import annotations

from datetime import datetime

from sr_agent.models.audit import Stage1Report

SEVERITY_ORDER = ["critical", "high", "medium", "low", "informational"]
_UNVERIFIED_STATUSES = {"unverified", "mock_review"}
_HIDDEN_STATUSES = {"false_positive"}


def _rank(severity: str) -> int:
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        return len(SEVERITY_ORDER)


def _render_finding(f: dict) -> list[str]:
    fid = f.get("finding_id", "?")
    severity = f.get("severity", "?")
    location = f.get("location", "?")
    func = f.get("function_name", "?")
    tag = f.get("bastet_tag")
    notes = (f.get("notes") or "").strip()
    flags = f.get("notes_flags") or []

    lines = [f"### [{severity.upper()}] {fid} — `{func}`", ""]
    lines.append(f"- **Location**: {location}")
    if tag:
        lines.append(f"- **Category**: {tag}")
    mitig = f.get("mitigations_present") or []
    if mitig:
        lines.append(f"- **Mitigations present**: {', '.join(mitig)}")
    combined = f.get("combined_with") or []
    if combined:
        lines.append(f"- **Combined with**: {', '.join(combined)}")
    if flags:
        lines.append(f"- **Sanitizer flags**: {', '.join(flags)}")
    if notes:
        lines += ["", notes]
    lines.append("")
    return lines


def generate_report(
    project_id: str,
    findings: list[dict],
    stage1: Stage1Report | None = None,
    generated_at: datetime | None = None,
    combinations: list[str] | None = None,
) -> str:
    generated_at = generated_at or datetime.utcnow()
    visible = [f for f in findings if f.get("status", "open") not in _HIDDEN_STATUSES]
    main = [f for f in visible if f.get("status", "open") not in _UNVERIFIED_STATUSES]
    unverified = [f for f in visible if f.get("status", "open") in _UNVERIFIED_STATUSES]

    main.sort(key=lambda f: (_rank(f.get("severity", "")), f.get("finding_id", "")))
    unverified.sort(key=lambda f: (_rank(f.get("severity", "")), f.get("finding_id", "")))

    lines: list[str] = [
        f"# Security Audit — {project_id}",
        "",
        f"_Generated {generated_at.isoformat(timespec='seconds')}_",
        "",
        "## Summary",
        "",
    ]

    counts = {sev: 0 for sev in SEVERITY_ORDER}
    for f in main:
        sev = f.get("severity", "")
        if sev in counts:
            counts[sev] += 1
    lines.append("| Severity | Count |")
    lines.append("|---|---|")
    for sev in SEVERITY_ORDER:
        lines.append(f"| {sev.capitalize()} | {counts[sev]} |")
    lines.append("")

    lines += ["## Findings", ""]
    if main:
        for f in main:
            lines += _render_finding(f)
    else:
        lines += ["_No confirmed findings._", ""]

    if unverified:
        lines += ["## Unverified Findings", "",
                  "_Reported by analysis but not yet verified; treat as leads._", ""]
        for f in unverified:
            lines += _render_finding(f)

    if combinations:
        lines += ["## Combination Chains", ""]
        for chain in combinations:
            lines.append(f"- {chain}")
        lines.append("")

    lines += ["## Coverage", ""]
    if stage1 is not None:
        analyzed = stage1.priority_targets
        skipped = stage1.skipped_targets
        lines.append(f"**Analyzed ({len(analyzed)})**: prioritized red-flag targets")
        for t in analyzed:
            lines.append(f"- {t}")
        lines.append("")
        lines.append(f"**Not analyzed ({len(skipped)})**: no red-flag signal")
        for t in skipped:
            lines.append(f"- {t}")
        lines.append("")
    else:
        lines += ["_Coverage data unavailable._", ""]

    return "\n".join(lines)
