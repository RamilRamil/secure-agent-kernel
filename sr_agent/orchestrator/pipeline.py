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

from sr_agent.eval.tracer import NOOP_TRACER, Tracer
from sr_agent.io.progress import ProgressEvent, ProgressStream, silent
from sr_agent.io.report import generate_report
from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.audit import AuditInput, AuditSession, Principal, Stage1Report
from sr_agent.models.finding import Finding
from sr_agent.models.memory import MemoryRecord, SourceType
from sr_agent.planner.sig import build_sig, build_sig_from_smartgraphical
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
    sg_graphs: dict = field(default_factory=dict)  # file_rel -> SmartGraphical graph


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


def _run_static_analysis(
    audit_root: Path,
    files: list[str],
    session: AuditSession,
    memory: EpisodicMemory,
    progress: ProgressStream,
) -> int:
    """Run Slither on each file and store findings as tool_output (best effort).

    Deterministic findings from a static analyser are more trusted than relayed
    ones (tool_output > external_llm_output). If Docker/Slither is unavailable
    this is skipped silently — it enriches the audit, it does not gate it.
    """
    from sr_agent.tools.sandbox import DockerSandbox
    from sr_agent.tools.static_analysis import run_slither, slither_to_findings

    sandbox = DockerSandbox()
    written = 0
    for file_rel in files:
        try:
            slither_findings = run_slither(audit_root / file_rel, audit_root, sandbox)
        except Exception as e:  # SandboxUnavailable / SlitherError / daemon down
            progress.emit(ProgressEvent.stage1_done, f"static analysis skipped ({type(e).__name__})")
            return written
        for sf, finding in zip(slither_findings, slither_to_findings(slither_findings, file_rel)):
            payload = finding.model_dump()
            payload["notes"] = sf.description
            payload["engine"] = "slither"
            memory.write(
                MemoryRecord(
                    project_id=session.principal.project_id,
                    target=file_rel,
                    source_type=SourceType.tool_output,
                    tool="run_slither",
                    session_id=session.session_id,
                    finding=payload,
                ),
                principal=session.principal,
            )
            written += 1
    return written


def _run_smartgraphical_analysis(
    audit_root: Path,
    files: list[str],
    session: AuditSession,
    memory: EpisodicMemory,
    progress: ProgressStream,
    sg_root: str,
) -> tuple[int, dict]:
    """Run SmartGraphical on each file, store findings, and collect graphs.

    Returns (findings_written, {file_rel: graph}). Best-effort: a missing
    SmartGraphical root, an unavailable interpreter, or any error skips the pass
    silently — it enriches the audit, never gates it.
    """
    if not sg_root:
        return 0, {}
    from sr_agent.guardrails.sanitize import sanitize as _sanitize
    from sr_agent.tools.smartgraphical import run_smartgraphical, sg_to_findings

    written = 0
    graphs: dict[str, dict] = {}
    for file_rel in files:
        try:
            sg_findings, graph = run_smartgraphical(audit_root / file_rel, audit_root, sg_root)
        except Exception as e:
            progress.emit(ProgressEvent.stage1_done, f"SmartGraphical skipped ({type(e).__name__})")
            return written, graphs
        if graph:
            graphs[file_rel] = graph
        for sf, finding in zip(sg_findings, sg_to_findings(sg_findings, file_rel)):
            payload = finding.model_dump()
            clean = _sanitize(f"{sf.message} — {sf.remediation_hint}".strip(" —"))
            payload["notes"] = clean.normalized
            payload["notes_flags"] = clean.flags
            payload["engine"] = "smartgraphical"
            payload["rule_id"] = sf.rule_id
            payload["category"] = sf.category
            payload["confidence"] = sf.confidence
            memory.write(
                MemoryRecord(
                    project_id=session.principal.project_id,
                    target=file_rel,
                    source_type=SourceType.tool_output,
                    tool="run_smartgraphical",
                    session_id=session.session_id,
                    finding=payload,
                ),
                principal=session.principal,
            )
            written += 1
    return written, graphs


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
    progress: ProgressStream | None = None,
    run_static: bool = True,
    stage2_provider: str = "relay",
    local_client=None,
    smartgraphical_root: str = "",
    tracer: Tracer = NOOP_TRACER,
) -> PipelineResult:
    """Run Stage 1, then Stage 2 (local model or relay), and persist run state.

    stage2_provider: "relay" (manual Claude, pause/resume), "local" (Ollama,
    synchronous), or "auto" (local if reachable, else relay).
    """
    progress = progress or silent()
    session = AuditSession(principal=audit_input.principal, audit_input=audit_input)

    progress.emit(ProgressEvent.stage1_start)
    stage1 = run_stage1(
        audit_root,
        exclude=audit_input.exclude_paths or None,
        focus=audit_input.focus_files or None,
    )
    progress.emit(ProgressEvent.stage1_done, f"{len(stage1.priority_targets)} priority targets")

    # Static-analysis pass: deterministic findings from Slither (tool_output).
    sg_graphs: dict = {}
    if run_static:
        unique_files: list[str] = []
        for target in stage1.priority_targets:
            f = target.split(":")[0]
            if f not in unique_files:
                unique_files.append(f)
        static_count = _run_static_analysis(audit_root, unique_files, session, memory, progress)
        if static_count:
            progress.emit(ProgressEvent.stage1_done, f"{static_count} static-analysis finding(s)")
        sg_count, sg_graphs = _run_smartgraphical_analysis(
            audit_root, unique_files, session, memory, progress, smartgraphical_root
        )
        if sg_count:
            progress.emit(ProgressEvent.stage1_done, f"{sg_count} SmartGraphical finding(s)")

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
        sg_graphs=sg_graphs,
    )
    save_state(state, runs_dir)

    # Stage 2 provider: a local Ollama model runs synchronously (no pause);
    # relay falls back to the manual Claude channel (pause/resume).
    if stage2_provider in ("local", "auto"):
        from sr_agent.llm_core.local_client import LocalClient
        from sr_agent.planner.stage2 import run_stage2_local

        client = local_client or LocalClient.for_stage2()
        if client.available():
            progress.emit(ProgressEvent.stage2_emit, f"local model {client.model}")
            run_stage2_local(
                session, state.targets, memory, client, _context_provider(audit_root),
                tracer=tracer,
            )
            progress.emit(ProgressEvent.stage2_ingest, "local analysis complete")
            return _finish(state, memory, progress)
        progress.emit(ProgressEvent.paused, "local model unavailable — falling back to relay")

    return _run_stage2_step(state, session, memory, relay_dir, runs_dir, progress)


def resume_audit(
    session_id: str,
    memory: EpisodicMemory,
    relay_dir: Path,
    runs_dir: Path,
    progress: ProgressStream | None = None,
) -> PipelineResult:
    """Ingest available relay responses; finish with a report when complete."""
    progress = progress or silent()
    state = load_state(session_id, runs_dir)
    session = _session_from_state(state)
    return _run_stage2_step(state, session, memory, relay_dir, runs_dir, progress)


def _run_stage2_step(
    state: PipelineState,
    session: AuditSession,
    memory: EpisodicMemory,
    relay_dir: Path,
    runs_dir: Path,
    progress: ProgressStream,
) -> PipelineResult:
    result = run_stage2(
        session,
        state.targets,
        memory,
        relay_dir,
        _context_provider(Path(state.audit_root)),
    )
    if result.requested:
        progress.emit(ProgressEvent.stage2_emit, f"{result.requested} request(s)")
    if result.ingested:
        progress.emit(ProgressEvent.stage2_ingest, f"{result.ingested} response(s)")

    if result.status == "paused":
        pending = len(result.pending) or len(state.targets)
        progress.emit(ProgressEvent.paused, f"{pending} target(s)")
        return PipelineResult(status="paused", session_id=state.session_id, pending=pending)

    return _finish(state, memory, progress)


def _finish(
    state: PipelineState, memory: EpisodicMemory, progress: ProgressStream
) -> PipelineResult:
    principal = Principal(user_id=state.user_id, platform=state.platform, project_id=state.project_id)

    # Reconstruct Finding objects from stored payloads, keeping notes aside
    # (notes is not a Finding field). Stage 3 transforms the Finding objects.
    # Display extras that are not Finding fields (engine attribution, rule id,
    # category, confidence, notes) are carried aside and re-attached for the
    # report after Stage 3 transforms the Finding objects.
    extras_map: dict[str, dict] = {}
    finding_objs: list[Finding] = []
    for record in memory.load_for_principal(principal):
        if not record.finding:
            continue
        payload = dict(record.finding)
        extras = {
            "notes": payload.pop("notes", ""),
            "notes_flags": payload.pop("notes_flags", []),
            "engine": payload.get("engine"),
            "rule_id": payload.get("rule_id"),
            "category": payload.get("category"),
            "confidence": payload.get("confidence"),
        }
        try:
            finding = Finding(**payload)
        except Exception:
            continue
        extras_map[finding.finding_id] = extras
        finding_objs.append(finding)

    # Build a per-file State Interference Graph so Stage 3 combines findings
    # that actually share state, not merely findings in the same file.
    audit_root = Path(state.audit_root)
    sigs: dict = {}
    for finding in finding_objs:
        src_file = finding.location.split(":")[0]
        if src_file in sigs:
            continue
        # Prefer SmartGraphical's structural graph (accurate read/write +
        # inheritance) when captured; fall back to the regex SIG.
        sg_graph = (state.sg_graphs or {}).get(src_file)
        if sg_graph:
            try:
                sigs[src_file] = build_sig_from_smartgraphical(sg_graph)
                continue
            except Exception:
                pass
        src_path = audit_root / src_file
        if src_path.exists():
            try:
                sigs[src_file] = build_sig(src_path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass

    stage3 = run_stage3(finding_objs, sigs=sigs)
    progress.emit(ProgressEvent.stage3, f"{len(stage3.combinations)} combination chain(s)")

    # Back to dicts for the report, re-attaching the sanitized notes.
    finding_dicts: list[dict] = []
    for finding in stage3.findings:
        d = finding.model_dump(mode="json")  # enums -> their string values for the report
        for key, value in extras_map.get(finding.finding_id, {}).items():
            if value not in (None, ""):
                d[key] = value
        finding_dicts.append(d)

    stage1 = Stage1Report(
        priority_targets=state.targets, skipped_targets=state.skipped, notes=state.stage1_notes
    )
    report_md = generate_report(
        state.project_id, finding_dicts, stage1=stage1, combinations=stage3.combinations
    )
    Path(state.output).write_text(report_md, encoding="utf-8")
    progress.emit(ProgressEvent.report, state.output)

    logger.info(
        "Audit %s complete: %d findings, %d chains -> %s",
        state.session_id, len(finding_dicts), len(stage3.combinations), state.output,
    )
    return PipelineResult(
        status="done", session_id=state.session_id,
        report_path=state.output, findings_count=len(finding_dicts),
    )
