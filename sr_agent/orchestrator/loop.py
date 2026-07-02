from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from sr_agent.config import config
from sr_agent.guardrails.sanitize import sanitize
from sr_agent.llm_core.claude_client import ClaudeClient
from sr_agent.llm_core.schemas import AgentAction
from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.action import Action, ActionType, ValidationStatus
from sr_agent.models.audit import AuditSession
from sr_agent.models.chat import MAX_TOOL_CALLS_PER_TURN, PoCStatusEvent, RoutingDecision
from sr_agent.models.finding import Finding, Severity
from sr_agent.models.memory import MemoryRecord, SourceType
from sr_agent.tools.sandbox import DockerSandbox, SandboxError
from sr_agent.tools.write_execute import run_tests, write_poc
from sr_agent.orchestrator.action import validate_action
from sr_agent.orchestrator.checkpoint import save_checkpoint
from sr_agent.orchestrator.confirmation import (
    ConfirmationStatus, check_confirmation, request_confirmation,
)
from sr_agent.orchestrator.context import build_messages, wrap_data
from sr_agent.tools.readonly import ReadOnlyToolError, read_file, search_code
from sr_agent.tools.registry import verify_all_hashes

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 50


@dataclass
class AuditResult:
    session_id: str
    findings: list[Finding]
    iterations: int
    completed: bool
    stop_reason: str


@dataclass
class TurnResult:
    """Outcome of one chat turn (feature 003, run_turn)."""
    status: str  # completed | paused_confirmation | paused_relay | blocked_local_unavailable | budget_exhausted
    answer: str = ""
    routing: RoutingDecision | None = None
    tool_calls: int = 0
    findings: list[Finding] = field(default_factory=list)
    pending_confirmation_id: str | None = None
    relay_request_id: str | None = None
    # ConsequentialActionNotice (FR-008/R8) — what the pending confirmation will run.
    pending_action_type: str | None = None
    pending_action_params: dict = field(default_factory=dict)


class OrchestratorLoop:
    """Main ReAct loop for Stage 1 and Stage 3.

    Security invariants maintained here:
    - Tool registry integrity verified before any LLM call
    - All external data wrapped in [DATA START]...[DATA END]
    - Every AgentAction validated before execution
    - WRITE_EXECUTE actions suspended pending out-of-band confirmation
    - LLM output parsed as structured AgentAction — free text never executed
    """

    def __init__(
        self,
        session: AuditSession,
        memory: EpisodicMemory,
        audit_root: Path,
        *,
        reasoning_provider: object | None = None,
        session_facts_provider: Callable[[], str | None] | None = None,
        confirmations_dir: Path | None = None,
        confirmation_timeout_s: float = 300.0,
        sandbox: DockerSandbox | None = None,
        poc_dir: Path | None = None,
        poc_generator: Callable[[str], str] | None = None,
    ) -> None:
        self._session = session
        self._memory = memory
        self._audit_root = audit_root
        self._confirmations_dir = confirmations_dir or config.confirmations_root
        self._confirmation_timeout_s = confirmation_timeout_s
        # PoC execution (audit-pack, contracts/poc-execution.md): PoCs land in
        # <audit_root>/audit/poc/ and run in the sandbox. Injectable for tests.
        self._sandbox = sandbox or DockerSandbox()
        self._poc_dir = poc_dir or (audit_root / "audit" / "poc")
        self._poc_generator = poc_generator
        # Chat mode injects a ChatReasoningProvider (.complete() -> ReasoningOutcome).
        # The non-chat run() path lazily constructs a ClaudeClient instead — chat
        # never touches the paid API (Constitution V).
        self._reasoning = reasoning_provider
        self._session_facts_provider = session_facts_provider
        self._audit_client: ClaudeClient | None = None
        self._findings: list[Finding] = []

        # Verify tool descriptions haven't been tampered with
        verify_all_hashes()

    def run(self, system_prompt: str) -> AuditResult:
        """Execute the ReAct loop until completion or resource limit."""
        iterations = 0
        last_tool_output: str | None = None

        while iterations < MAX_ITERATIONS:
            iterations += 1
            self._session.iterations = iterations

            # ── Build context ────────────────────────────────────────────
            messages = build_messages(
                session=self._session,
                system_prompt=system_prompt,
                tool_output=last_tool_output,
                model=config.stage1_model,
            )

            # ── LLM call ─────────────────────────────────────────────────
            if self._audit_client is None:
                self._audit_client = ClaudeClient()
            try:
                agent_action = self._audit_client.complete(messages)
            except ValueError as e:
                logger.warning("Malformed LLM response (iter %d): %s", iterations, e)
                continue

            logger.info(
                "Iter %d: next_action=%s reasoning=%s",
                iterations,
                agent_action.next_action,
                agent_action.reasoning_summary[:80],
            )

            # ── Persist finding if LLM reported one ──────────────────────
            if agent_action.finding:
                finding = self._persist_finding(agent_action)
                if finding:
                    self._findings.append(finding)

            # ── Terminal actions ─────────────────────────────────────────
            if agent_action.next_action == ActionType.escalate.value:
                return AuditResult(
                    session_id=self._session.session_id,
                    findings=self._findings,
                    iterations=iterations,
                    completed=False,
                    stop_reason=f"escalated:{agent_action.escalation_trigger}",
                )

            if agent_action.next_action == "complete":
                save_checkpoint(self._session, self._session.current_stage, self._memory)
                return AuditResult(
                    session_id=self._session.session_id,
                    findings=self._findings,
                    iterations=iterations,
                    completed=True,
                    stop_reason="completed",
                )

            # ── Validate action ──────────────────────────────────────────
            action = Action(
                action_type=ActionType(agent_action.next_action),
                params=agent_action.tool_params,
            )
            result = validate_action(action, self._audit_root)

            if result.status == ValidationStatus.rejected:
                logger.warning(
                    "Action rejected: %s — %s", action.action_type, result.rejection_reason
                )
                last_tool_output = wrap_data(
                    f"ACTION REJECTED: {result.rejection_reason}",
                    tool="orchestrator",
                    path="",
                )
                continue

            # ── Out-of-band confirmation gate ────────────────────────────
            # Irreversible WRITE_EXECUTE actions pause here. The agent writes a
            # pending request and blocks; only a separate `sr-agent confirm`
            # process may approve it. Rejection or timeout (fail-safe) cancels
            # the action and feeds the outcome back as an observation.
            if action.human_confirmation is False:
                req = request_confirmation(action, self._confirmations_dir)
                logger.info(
                    "Pausing for out-of-band confirmation %s (action %s)",
                    req.confirmation_id, action.action_type.value,
                )
                status = check_confirmation(
                    req.confirmation_id,
                    self._confirmations_dir,
                    timeout_s=self._confirmation_timeout_s,
                )
                if status is not ConfirmationStatus.approved:
                    logger.warning(
                        "blocked_attempt: action %s confirmation %s was %s",
                        action.action_type.value, req.confirmation_id, status.value,
                    )
                    last_tool_output = wrap_data(
                        f"Action {action.action_type.value!r} was {status.value} "
                        "via out-of-band confirmation — not executed.",
                        tool="orchestrator",
                        path="",
                    )
                    continue
                action.human_confirmation = True  # approved out-of-band
                logger.info(
                    "Action %s approved out-of-band, proceeding", action.action_type.value
                )

            # ── Execute (stub — tools implemented in later phases) ───────
            last_tool_output = self._dispatch(action)

        return AuditResult(
            session_id=self._session.session_id,
            findings=self._findings,
            iterations=iterations,
            completed=False,
            stop_reason="max_iterations_reached",
        )

    def run_turn(self, user_message: str, system_prompt: str) -> TurnResult:
        """Execute one chat turn (feature 003, FR-006/R4).

        Local-first reasoning via the injected provider, read-only tool calls
        bounded by a per-turn budget that resets every turn. The session spans
        unbounded turns; a single turn stops at MAX_TOOL_CALLS_PER_TURN.

        Returns control to the caller (does NOT block) on a paused outcome —
        blocked_local_unavailable (FR-011), paused_relay (R3), or
        paused_confirmation (R8: the OOB gate is filed, not polled here).
        """
        assert self._reasoning is not None, "run_turn requires a reasoning_provider"

        tool_calls = 0
        turn_findings: list[Finding] = []
        # The user's own message is human_input, but it enters model context as
        # DATA like everything else — its wording carries no authority (FR-004).
        last_tool_output: str | None = wrap_data(user_message, tool="user", path="chat")
        facts = self._session_facts_provider() if self._session_facts_provider else None
        routing: RoutingDecision | None = None

        while tool_calls <= MAX_TOOL_CALLS_PER_TURN:
            messages = build_messages(
                session=self._session, system_prompt=system_prompt,
                tool_output=last_tool_output, session_facts=facts,
                model=config.stage2_model,
            )
            try:
                outcome = self._reasoning.complete(messages)
            except ValueError as e:  # malformed model JSON — do not fall to relay
                logger.warning("chat turn: malformed model response: %s", e)
                return TurnResult(
                    status="completed", answer="(could not parse a valid response)",
                    tool_calls=tool_calls, findings=turn_findings,
                )

            routing = RoutingDecision(
                tier=outcome.tier,
                escalation_trigger=outcome.escalation_trigger,
                escalation_source=outcome.escalation_source,
            )

            if outcome.kind == "blocked_local_unavailable":
                return TurnResult(
                    status="blocked_local_unavailable", routing=routing,
                    tool_calls=tool_calls, findings=turn_findings,
                )
            if outcome.kind == "paused_relay":
                return TurnResult(
                    status="paused_relay", routing=routing,
                    relay_request_id=outcome.relay_request_id,
                    tool_calls=tool_calls, findings=turn_findings,
                )

            agent_action = outcome.agent_action
            assert agent_action is not None  # kind == "action" guarantees this

            if agent_action.finding:
                finding = self._persist_finding(agent_action)
                if finding:
                    turn_findings.append(finding)
                    self._findings.append(finding)

            # Terminal: the model answered directly (no tool). "complete" carries
            # the answer in reasoning_summary; "escalate" ends the turn too.
            na = agent_action.next_action
            if na in ("complete", ActionType.escalate.value):
                return TurnResult(
                    status="completed", answer=agent_action.reasoning_summary,
                    routing=routing, tool_calls=tool_calls, findings=turn_findings,
                )

            # Unknown next_action → feed the rejection back as data, don't crash.
            if na not in ActionType._value2member_map_:
                last_tool_output = wrap_data(
                    f"ACTION REJECTED: unknown next_action {na!r}",
                    tool="orchestrator", path="",
                )
                tool_calls += 1
                continue

            action = Action(action_type=ActionType(na), params=agent_action.tool_params)
            result = validate_action(action, self._audit_root)
            if result.status == ValidationStatus.rejected:
                last_tool_output = wrap_data(
                    f"ACTION REJECTED: {result.rejection_reason}",
                    tool="orchestrator", path="",
                )
                tool_calls += 1
                continue

            # Irreversible write_execute → file the OOB confirmation and PAUSE the
            # turn (R8). No shortcut around the gate (Constitution II); the CLI
            # resumes once the human approves out-of-band.
            if action.human_confirmation is False:
                req = request_confirmation(action, self._confirmations_dir)
                logger.info(
                    "chat turn paused for confirmation %s (action %s)",
                    req.confirmation_id, action.action_type.value,
                )
                return TurnResult(
                    status="paused_confirmation", routing=routing,
                    pending_confirmation_id=req.confirmation_id,
                    pending_action_type=action.action_type.value,
                    pending_action_params=dict(action.params),
                    tool_calls=tool_calls, findings=turn_findings,
                )

            # Read-only / approved dispatch — result feeds the next iteration as DATA.
            last_tool_output = self._dispatch(action)
            tool_calls += 1

        return TurnResult(
            status="budget_exhausted",
            answer="(per-turn tool-call budget reached — stopping this turn)",
            routing=routing, tool_calls=tool_calls, findings=turn_findings,
        )

    def execute_confirmed(self, action: Action) -> tuple[str, PoCStatusEvent | None]:
        """Execute a write_execute action AFTER out-of-band approval (US2/R9).

        Called only from the resume path, once a human has approved the pending
        confirmation. Returns (human-readable summary, PoC status event to record).
        The generator's/tool's output is data, executed only in the sandbox.
        """
        at = action.action_type
        finding_id = str(action.params.get("finding_id", "UNKNOWN"))

        if at == ActionType.write_poc:
            res = write_poc(finding_id, self._poc_dir, generator=self._poc_generator)
            event = PoCStatusEvent(finding_id=finding_id, status="written", poc_path=str(res.path))
            return (f"PoC written to {res.path}", event)

        if at == ActionType.run_tests:
            test_path = action.params.get("test_path")
            try:
                result = run_tests(
                    self._audit_root, self._sandbox, test_path=test_path,
                    foundry_test_dir="audit/poc",  # PoCs live outside default test/ (poc-execution.md)
                )
            except SandboxError as e:
                event = PoCStatusEvent(finding_id=finding_id, status="errored", skip_reason=None)
                return (f"run_tests could not execute: {e}", event)
            # Mechanical status only — a pass means a reproduction exists, NOT a
            # confirmed/safe verdict (Constitution II).
            status = "passed" if result.passed else "failed"
            summary = f"forge test {'PASSED' if result.passed else 'FAILED'} (exit {result.exit_code})"
            return (summary, PoCStatusEvent(finding_id=finding_id, status=status))

        # Any other write_execute (e.g. deploy_test_contract) — no PoC status.
        return (self._dispatch(action), None)

    def _persist_finding(self, agent_action: AgentAction) -> Finding | None:
        """Validate and persist a finding reported by the LLM."""
        payload = agent_action.finding
        if payload is None:
            return None

        try:
            finding = Finding(
                finding_id=payload.finding_id,
                location=payload.location,
                function_name=payload.function_name,
                severity=Severity(payload.severity),
                preconditions=payload.preconditions,
                mitigations_present=payload.mitigations_present,
            )
        except Exception as e:
            logger.warning("Invalid finding payload: %s", e)
            return None

        # Sanitize notes before they enter memory
        sanitized = sanitize(payload.notes)
        if sanitized.flags:
            logger.info("Finding notes sanitized, flags: %s", sanitized.flags)

        record = MemoryRecord(
            project_id=self._session.principal.project_id,
            target=payload.location.split(":")[0],
            # A finding produced by the reasoning provider (local model or relay) is
            # external_llm_output — same tier planner/stage2.py uses. NOT llm_inference
            # (automation != authoring). Never promoted to human_input (Constitution I).
            source_type=SourceType.external_llm_output,
            tool=None,
            session_id=self._session.session_id,
            finding=finding.model_dump(),
        )
        self._memory.write(record, principal=self._session.principal)
        self._session.finding_ids.append(payload.finding_id)
        return finding

    def _dispatch(self, action: Action) -> str:
        """Execute a validated action. Read-only tools are live; others are stubs.

        Tool output is always wrapped in [DATA START]..[DATA END] — it is
        external data that informs the LLM but is never executed as commands.
        """
        at = action.action_type
        params = action.params

        try:
            if at == ActionType.read_file:
                content = read_file(params["path"], self._audit_root)
                return wrap_data(content, tool="read_file", path=str(params.get("path", "")))

            if at == ActionType.search_code:
                root = params.get("root", str(self._audit_root))
                hits = search_code(params["pattern"], root)
                body = "\n".join(f"{h.file}:{h.line}: {h.text}" for h in hits) or "(no matches)"
                return wrap_data(body, tool="search_code", path=str(root))

            if at == ActionType.analyze_transactions:
                from sr_agent.config import config
                from sr_agent.tools.onchain import (
                    OnChainError, analyze_transactions, make_alchemy_fetcher,
                )
                try:
                    fetcher = make_alchemy_fetcher(config.alchemy_api_key)
                    res = analyze_transactions(
                        params["address"], int(params.get("from_block", 0)),
                        int(params.get("to_block", 0)), fetcher, focus=params.get("focus"),
                    )
                    body = "\n".join(res.notes) or "(no notable transactions)"
                    return wrap_data(body, tool="analyze_transactions", path=params["address"])
                except OnChainError as e:
                    return wrap_data(f"TOOL ERROR: {e}", tool="analyze_transactions", path="")
        except ReadOnlyToolError as e:
            return wrap_data(f"TOOL ERROR: {e}", tool=at.value, path="")
        except KeyError as e:
            return wrap_data(f"TOOL ERROR: missing required param {e}", tool=at.value, path="")

        # Slither/Mythril, on-chain, and write/execute dispatch land in later blocks.
        return wrap_data(
            f"[STUB] Tool {at.value!r} not yet implemented.",
            tool=at.value,
            path=str(params.get("path", "")),
        )
