"""Audit pipeline orchestration (T063/T064, relay variant).

Ties the deterministic stages together around the async relay channel:

    start_audit  -> Stage 1 (plan) -> Stage 2 emit relay requests -> PAUSE
    [human answers out-of-band]
    resume_audit -> Stage 2 ingest -> (done) -> report

Run state is persisted so `resume` can reconstruct the session by id. Stage 3
(combination) is not wired here yet — the chain is Stage 1 -> Stage 2 -> report.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sr_agent.io.report import generate_report
from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.audit import AuditInput, AuditSession, Principal, Stage1Report
from sr_agent.models.finding import Finding
from sr_agent.planner.sig import build_sig
from sr_agent.planner.stage1 import run_stage1
from sr_agent.planner.stage2 import run_stage2
from sr_agent.planner.stage3 import run_stage3
from sr_agent.tools.readonly import read_file

logger = logging.getLogger(__name__)


@dataclass
class PipelineState:
    session_id: str
    user_id: str
    platform: str
    project_id: str
    audit_root: str
    output: str
    targets: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    stage1_notes: str = ""


@dataclass
class PipelineResult:
    status: str  # "paused" | "done"
    session_id: str
    pending: int = 0
    report_path: str | None = None
    findings_count: int = 0


def _state_path(session_id: str, runs_dir: Path) -> Path:
    return runs_dir / f"{session_id}.json"


def save_state(state: PipelineState, runs_dir: Path) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    _state_path(state.session_id, runs_dir).write_text(
        json.dumps(state.__dict__, indent=2), encoding="utf-8"
    )


def load_state(session_id: str, runs_dir: Path) -> PipelineState:
    path = _state_path(session_id, runs_dir)
    if not path.exists():
        raise FileNotFoundError(f"No audit run: {session_id}")
    return PipelineState(**json.loads(path.read_text(encoding="utf-8")))


def _context_provider(audit_root: Path):
    def provider(target: str) -> str:
        filename = target.split(":")[0]
        return read_file(audit_root / filename, audit_root)
    return provider


def _session_from_state(state: PipelineState) -> AuditSession:
    principal = Principal(
        user_id=state.user_id, platform=state.platform, project_id=state.project_id
    )
    audit_input = AuditInput(path=Path(state.audit_root), principal=principal)
    # Reuse the saved session_id so the relay manifest matches on resume.
    return AuditSession(session_id=state.session_id, principal=principal, audit_input=audit_input)


def start_audit(
    audit_input: AuditInput,
    audit_root: Path,
    memory: EpisodicMemory,
    relay_dir: Path,
    runs_dir: Path,
    output: str = "audit-report.md",
) -> PipelineResult:
    """Run Stage 1, emit Stage 2 relay requests, and persist run state."""
    session = AuditSession(principal=audit_input.principal, audit_input=audit_input)

    stage1 = run_stage1(
        audit_root,
        exclude=audit_input.exclude_paths or None,
        focus=audit_input.focus_files or None,
    )

    state = PipelineState(
        session_id=session.session_id,
        user_id=audit_input.principal.user_id,
        platform=audit_input.principal.platform,
        project_id=audit_input.principal.project_id,
        audit_root=str(audit_root),
        output=output,
        targets=stage1.priority_targets,
        skipped=stage1.skipped_targets,
        stage1_notes=stage1.notes,
    )
    save_state(state, runs_dir)

    return _run_stage2_step(state, session, memory, relay_dir, runs_dir)


def resume_audit(
    session_id: str,
    memory: EpisodicMemory,
    relay_dir: Path,
    runs_dir: Path,
) -> PipelineResult:
    """Ingest available relay responses; finish with a report when complete."""
    state = load_state(session_id, runs_dir)
    session = _session_from_state(state)
    return _run_stage2_step(state, session, memory, relay_dir, runs_dir)


def _run_stage2_step(
    state: PipelineState,
    session: AuditSession,
    memory: EpisodicMemory,
    relay_dir: Path,
    runs_dir: Path,
) -> PipelineResult:
    result = run_stage2(
        session,
        state.targets,
        memory,
        relay_dir,
        _context_provider(Path(state.audit_root)),
    )
    if result.status == "paused":
        return PipelineResult(status="paused", session_id=state.session_id, pending=len(result.pending) or len(state.targets))

    return _finish(state, memory)


def _finish(state: PipelineState, memory: EpisodicMemory) -> PipelineResult:
    principal = Principal(user_id=state.user_id, platform=state.platform, project_id=state.project_id)

    # Reconstruct Finding objects from stored payloads, keeping notes aside
    # (notes is not a Finding field). Stage 3 transforms the Finding objects.
    notes_map: dict[str, tuple[str, list]] = {}
    finding_objs: list[Finding] = []
    for record in memory.load_for_principal(principal):
        if not record.finding:
            continue
        payload = dict(record.finding)
        notes = payload.pop("notes", "")
        notes_flags = payload.pop("notes_flags", [])
        try:
            finding = Finding(**payload)
        except Exception:
            continue
        notes_map[finding.finding_id] = (notes, notes_flags)
        finding_objs.append(finding)

    # Build a per-file State Interference Graph so Stage 3 combines findings
    # that actually share state, not merely findings in the same file.
    audit_root = Path(state.audit_root)
    sigs: dict = {}
    for finding in finding_objs:
        src_file = finding.location.split(":")[0]
        if src_file in sigs:
            continue
        src_path = audit_root / src_file
        if src_path.exists():
            try:
                sigs[src_file] = build_sig(src_path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass

    stage3 = run_stage3(finding_objs, sigs=sigs)

    # Back to dicts for the report, re-attaching the sanitized notes.
    finding_dicts: list[dict] = []
    for finding in stage3.findings:
        d = finding.model_dump()
        notes, flags = notes_map.get(finding.finding_id, ("", []))
        d["notes"] = notes
        d["notes_flags"] = flags
        finding_dicts.append(d)

    stage1 = Stage1Report(
        priority_targets=state.targets, skipped_targets=state.skipped, notes=state.stage1_notes
    )
    report_md = generate_report(
        state.project_id, finding_dicts, stage1=stage1, combinations=stage3.combinations
    )
    Path(state.output).write_text(report_md, encoding="utf-8")

    logger.info(
        "Audit %s complete: %d findings, %d chains -> %s",
        state.session_id, len(finding_dicts), len(stage3.combinations), state.output,
    )
    return PipelineResult(
        status="done", session_id=state.session_id,
        report_path=state.output, findings_count=len(finding_dicts),
    )
