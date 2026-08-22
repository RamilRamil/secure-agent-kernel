from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from sr_agent.config import config
from sr_agent.llm_core.claude_client import ClaudeClient
from sr_agent.llm_core.schemas import AgentAction
from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.action import Action, LOOP_TERMINALS, ValidationStatus
from sr_agent.models.chat import MAX_TOOL_CALLS_PER_TURN, PoCStatusEvent, RoutingDecision
from sr_agent.models.memory import MemoryRecord, SourceType
from sr_agent.tools.sandbox import DockerSandbox
from sr_agent.orchestrator.action import KERNEL_GENERIC_ACTIONS, validate_action
from sr_agent.orchestrator.context import build_messages, wrap_data
from sr_agent.models.dispatch import DispatchStatus, PendingKind
from sr_agent.orchestrator.executor import KernelActionExecutor, ResumeError
from sr_agent.orchestrator.pack import PackContext
from sr_agent.orchestrator.prompts import PromptRegistry, PromptRegistryError, prompt_digest
from sr_agent.tools.registry import verify_all_hashes

if TYPE_CHECKING:
    from sr_agent.models.session import Session
    from sr_agent.orchestrator.pack import CapabilityPack

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 50


def _paused_status(result) -> str:
    if result.pending is None:
        return "paused_relay"
    return {
        PendingKind.external_response: "paused_relay",
        PendingKind.human_confirmation: "paused_confirmation",
        PendingKind.local_model_retry: "blocked_local_unavailable",
    }[result.pending.kind]


@dataclass
class RunResult:
    session_id: str
    findings: list  # pack-domain findings (e.g. audit Finding); kernel treats opaque
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
    findings: list = field(default_factory=list)
    pending_confirmation_id: str | None = None
    relay_request_id: str | None = None
    # ConsequentialActionNotice (FR-008/R8) — what the pending confirmation will run.
    pending_action_type: str | None = None
    pending_action_params: dict = field(default_factory=dict)
    # Short orchestrator-written summaries of tools called this turn (US4 grounding).
    tool_summaries: list[str] = field(default_factory=list)


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
        session: "Session",
        memory: EpisodicMemory,
        scope_root: Path,
        *,
        pack: "CapabilityPack",
        reasoning_provider: object | None = None,
        session_facts_provider: Callable[[], str | None] | None = None,
        confirmations_dir: Path | None = None,
        confirmation_timeout_s: float = 300.0,
        sandbox: DockerSandbox | None = None,
        checkpoint_fn: Callable | None = None,
        event_sink: Callable[[dict], None] | None = None,
        discovery_model: str | None = None,
        chat_model: str | None = None,
        prompt_registry: PromptRegistry | None = None,
    ) -> None:
        self._session = session
        self._memory = memory
        self._scope_root = scope_root
        self._confirmations_dir = confirmations_dir or config.confirmations_root
        self._confirmation_timeout_s = confirmation_timeout_s
        # The sandbox is a kernel-provided containment capability. PoC output dirs
        # / generators are NOT kernel state any more (decision D2/US5): a pack that
        # runs a write_execute PoC path carries its own — the kernel loop stays
        # task-agnostic and threads no PoC vocabulary into PackContext.
        self._sandbox = sandbox or DockerSandbox()
        # Chat mode injects a ChatReasoningProvider (.complete() -> ReasoningOutcome).
        # The non-chat run() path lazily constructs a ClaudeClient instead — chat
        # never touches the paid API (Constitution V).
        self._reasoning = reasoning_provider
        self._session_facts_provider = session_facts_provider
        # Model ids arrive by injection (feature 048) — the kernel loop owns no
        # routing vocabulary. The composition root resolves them from the audit
        # model-roles map and passes opaque strings; these two just size context
        # windows here (and seed the paid ClaudeClient in run()). None keeps the
        # pre-split default sizing behaviour for callers that don't inject.
        self._discovery_model = discovery_model
        self._chat_model = chat_model
        self._prompt_registry = prompt_registry
        self._audit_client: ClaudeClient | None = None
        self._findings: list = []

        # The pack supplies dispatch/execute_confirmed/persist_finding; the loop
        # keeps the control flow + invariants. Pack callables get only this narrow
        # PackContext (least privilege) — never the loop, never memory-write.
        self._pack = pack
        self._checkpoint_fn = checkpoint_fn
        # Observability only (feature 005, R3): a surface can watch the ReAct
        # steps live. None-safe; exceptions in the sink NEVER affect the loop —
        # it cannot change control flow or any invariant.
        self._event_sink = event_sink
        self._ctx = PackContext(
            scope_root=scope_root, sandbox=self._sandbox, wrap_data=wrap_data,
        )
        self._executor = KernelActionExecutor(
            memory=memory,
            scope_root=scope_root,
            pack_id=pack.name,
            pack_contract_version=getattr(pack, "contract_version", "1"),
            confirmations_dir=self._confirmations_dir,
            sandbox=self._sandbox,
        )

        # Verify tool descriptions haven't been tampered with
        verify_all_hashes()

    def resolve_prompt(self, system_prompt: str) -> tuple[str, str, str]:
        """Return (prompt_id, hash, body). Body comes from the registry when bound."""
        if self._prompt_registry is None:
            return ("inline", prompt_digest(system_prompt), system_prompt)
        entry = self._prompt_registry.get(system_prompt)
        return (entry.prompt_id, entry.digest, entry.body)

    def resolve_resume_instruction(self) -> str:
        """Load instruction bytes from the registry. Checkpoint hash is a check.

        An attacker-controlled `system_prompt_hash` fails closed and is never
        used as the system instruction (FR-020 / D18).
        """
        if self._prompt_registry is None:
            raise ResumeError(
                "No prompt registry; resume will not treat a checkpoint hash "
                "as the system instruction."
            )
        cont = getattr(self._session, "continuation", None)
        prompt_id = getattr(cont, "system_prompt_id", "") if cont else ""
        prompt_hash = getattr(cont, "system_prompt_hash", "") if cont else ""
        version = getattr(cont, "system_prompt_version", None) if cont else None
        if not prompt_id:
            ckpt = self._executor.latest_checkpoint(self._session)
            if ckpt and ckpt.payload:
                prompt_id = ckpt.payload.get("system_prompt_id") or ""
                prompt_hash = ckpt.payload.get("system_prompt_hash") or ""
                version = ckpt.payload.get("system_prompt_version")
        if not prompt_id:
            raise ResumeError(
                "Checkpoint has no system_prompt_id; cannot load instruction "
                "from the registry."
            )
        try:
            return self._prompt_registry.load(prompt_id, prompt_hash, version)
        except PromptRegistryError as exc:
            raise ResumeError(str(exc)) from exc

    @staticmethod
    def _reenter_tool_body(body: str, action_type: str) -> str:
        if body and "[DATA START" not in body:
            return wrap_data(body, tool=action_type, path="dispatch")
        return body

    def _emit(self, type: str, **payload) -> None:
        """Fire a live-trace event (observability only; never affects the loop)."""
        if self._event_sink is None:
            return
        try:
            self._event_sink({"type": type, **payload})
        except Exception:  # a broken observer must never break the loop
            logger.debug("event_sink raised; ignoring", exc_info=True)

    def run(self, system_prompt: str) -> RunResult:
        """Execute the ReAct loop until completion or resource limit."""
        iterations = 0
        last_tool_output: str | None = None
        prompt_id, prompt_hash, instruction = self.resolve_prompt(system_prompt)

        while iterations < MAX_ITERATIONS:
            iterations += 1
            self._session.iterations = iterations

            # ── Build context ────────────────────────────────────────────
            messages = build_messages(
                session=self._session,
                system_prompt=instruction,
                tool_output=last_tool_output,
                model=self._discovery_model or "claude-opus-4-8",
            )

            # ── LLM call ─────────────────────────────────────────────────
            if self._audit_client is None:
                self._audit_client = ClaudeClient(self._discovery_model)
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
            if agent_action.next_action == "escalate":
                return RunResult(
                    session_id=self._session.session_id,
                    findings=self._findings,
                    iterations=iterations,
                    completed=False,
                    stop_reason=f"escalated:{agent_action.escalation_trigger}",
                )

            if agent_action.next_action == "complete":
                if self._checkpoint_fn is not None:
                    self._checkpoint_fn(self._session, self._memory)
                return RunResult(
                    session_id=self._session.session_id,
                    findings=self._findings,
                    iterations=iterations,
                    completed=True,
                    stop_reason="completed",
                )

            # ── Validate action ──────────────────────────────────────────
            action = Action(
                action_type=agent_action.next_action,
                params=agent_action.tool_params,
            )
            result = validate_action(action, self._scope_root, self._pack)

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

            dispatched = self._executor.execute(
                self._pack, self._session, action,
                system_prompt_id=prompt_id, system_prompt_hash=prompt_hash,
            )
            if dispatched.status is DispatchStatus.pending:
                return RunResult(
                    session_id=self._session.session_id,
                    findings=self._findings,
                    iterations=iterations,
                    completed=False,
                    stop_reason=_paused_status(dispatched),
                )
            last_tool_output = self._reenter_tool_body(dispatched.body, action.action_type)

        return RunResult(
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

        prompt_id, prompt_hash, instruction = self.resolve_prompt(system_prompt)
        tool_calls = 0
        turn_findings: list[Finding] = []
        tool_summaries: list[str] = []
        # The user's own message is human_input, but it enters model context as
        # DATA like everything else — its wording carries no authority (FR-004).
        last_tool_output: str | None = wrap_data(user_message, tool="user", path="chat")
        facts = self._session_facts_provider() if self._session_facts_provider else None
        routing: RoutingDecision | None = None

        # Strict `<` — at most MAX_TOOL_CALLS_PER_TURN tool dispatches (SC-005:
        # the per-turn tool-call count never exceeds the configured budget).
        while tool_calls < MAX_TOOL_CALLS_PER_TURN:
            messages = build_messages(
                session=self._session, system_prompt=instruction,
                tool_output=last_tool_output, session_facts=facts,
                model=self._chat_model,
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
            self._emit(
                "routing", tier=outcome.tier,
                escalation_trigger=(outcome.escalation_trigger.value if outcome.escalation_trigger else None),
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
            self._emit(
                "reasoning", next_action=agent_action.next_action,
                tool_params=agent_action.tool_params,
                reasoning_summary=agent_action.reasoning_summary,
            )

            if agent_action.finding:
                finding = self._persist_finding(agent_action)
                if finding:
                    turn_findings.append(finding)
                    self._findings.append(finding)

            # Terminal: the model answered directly (no tool). "complete" carries
            # the answer in reasoning_summary; "escalate" ends the turn too.
            na = agent_action.next_action
            if na in LOOP_TERMINALS:
                return TurnResult(
                    status="completed", answer=agent_action.reasoning_summary,
                    routing=routing, tool_calls=tool_calls, findings=turn_findings,
                    tool_summaries=tool_summaries,
                )

            # Unknown next_action → feed the rejection back as data, don't crash.
            # Consult the same resolvable set validate_action uses
            # (KERNEL_GENERIC_ACTIONS ∪ pack.actions); terminals handled above.
            if na not in KERNEL_GENERIC_ACTIONS and not (self._pack and na in self._pack.actions):
                last_tool_output = wrap_data(
                    f"ACTION REJECTED: unknown next_action {na!r}",
                    tool="orchestrator", path="",
                )
                tool_calls += 1
                continue

            action = Action(action_type=na, params=agent_action.tool_params)
            result = validate_action(action, self._scope_root, self._pack)
            if result.status == ValidationStatus.rejected:
                last_tool_output = wrap_data(
                    f"ACTION REJECTED: {result.rejection_reason}",
                    tool="orchestrator", path="",
                )
                tool_calls += 1
                continue

            dispatched = self._executor.execute(
                self._pack, self._session, action,
                user_message=user_message, tool_calls_used=tool_calls,
                system_prompt_id=prompt_id, system_prompt_hash=prompt_hash,
            )
            if dispatched.status is DispatchStatus.pending:
                pending_id = dispatched.pending.correlation_id if dispatched.pending else None
                return TurnResult(
                    status=_paused_status(dispatched),
                    routing=routing,
                    pending_confirmation_id=pending_id,
                    pending_action_type=action.action_type,
                    pending_action_params=dict(action.params),
                    relay_request_id=pending_id if dispatched.pending and dispatched.pending.kind is PendingKind.external_response else None,
                    tool_calls=tool_calls, findings=turn_findings,
                )
            last_tool_output = self._reenter_tool_body(dispatched.body, action.action_type)
            detail = action.params.get("path") or action.params.get("pattern") or ""
            tool_summaries.append(f"{action.action_type} {detail}".strip())
            tool_calls += 1
            self._emit(
                "tool", tool=action.action_type, detail=detail,
                budget_used=tool_calls, budget_limit=MAX_TOOL_CALLS_PER_TURN,
            )

        return TurnResult(
            status="budget_exhausted",
            answer="(per-turn tool-call budget reached — stopping this turn)",
            routing=routing, tool_calls=tool_calls, findings=turn_findings,
            tool_summaries=tool_summaries,
        )

    def resume_turn(self, system_prompt: str) -> TurnResult:
        """Continue a paused turn from its checkpoint (FR-011).

        Must not call `run_turn(user_message)`: that would re-ask the model to
        invent the in-flight action. The Action snapshot on the checkpoint is
        the only source for re-dispatch.
        """
        if hasattr(self._session, "scope_root") and not getattr(self._session, "scope_root", None):
            raise ResumeError(
                "Session has no scope_root; resume is refused rather than "
                "defaulting to '.'."
            )
        if self._prompt_registry is not None:
            self.resolve_resume_instruction()
        result = self._executor.resume(self._pack, self._session)
        if result.status is DispatchStatus.pending:
            return TurnResult(status=_paused_status(result), answer=result.body)
        return TurnResult(status="completed", answer=result.body)

    def execute_confirmed(self, action: Action) -> tuple[str, PoCStatusEvent | None]:
        """Execute a write_execute action AFTER out-of-band approval (US2/R9).

        Delegates to the pack; reached only from the resume path once a human has
        approved the pending confirmation (never from within a model turn). The
        pack runs it inside the kernel-provided sandbox (PackContext).
        """
        return self._pack.execute_confirmed(action, self._ctx)

    def _persist_finding(self, agent_action: AgentAction):
        """Persist a finding the model reported.

        The pack builds + validates the domain Finding; the KERNEL owns the write
        and sets source_type=external_llm_output (FR-006) — a pack cannot set the
        tier or reach memory. Never promoted to human_input (Constitution I).
        """
        finding = self._pack.persist_finding(agent_action.finding, self._ctx)
        if finding is None:
            return None

        location = getattr(finding, "location", "") or ""
        record = MemoryRecord(
            project_id=self._session.principal.project_id,
            target=location.split(":")[0],
            source_type=SourceType.external_llm_output,
            tool=None,
            session_id=self._session.session_id,
            finding=finding.model_dump(),
        )
        self._memory.write(record, principal=self._session.principal)
        # session.finding_ids is pack-session bookkeeping — append if the session
        # tracks it (kernel Session protocol doesn't require it).
        ids = getattr(self._session, "finding_ids", None)
        if ids is not None:
            ids.append(getattr(finding, "finding_id", None))
        return finding
