"""Chat reasoning provider (feature 003, T004).

The seam R2 introduces: what `OrchestratorLoop` needs in place of `ClaudeClient`
for chat mode. Implements the behavior contract in
specs/003-interactive-chat-mode/contracts/chat-turn-contract.md.

Local-first, no paid API (Constitution V). Never falls back to relay when the
local model is unavailable (FR-011) — that returns `blocked_local_unavailable`.
Relay is reached ONLY on a deterministic escalation trigger or the model's own
self-report (research R3). Every outcome is exactly one of three kinds — no
silent tier retry (FR-010).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal

from sr_agent.guardrails.escalation import EscalationResult, evaluate_triggers
from sr_agent.llm_core.gemini_client import GeminiClient, GeminiUnavailable
from sr_agent.llm_core.local_client import LocalClient, ModelUnavailableError
from sr_agent.llm_core.schemas import AgentAction, EscalationTrigger
from sr_agent.orchestrator.relay import request_analysis

if TYPE_CHECKING:
    from sr_agent.models.session import Session

logger = logging.getLogger(__name__)


def _no_signal(_agent_action: AgentAction) -> object | None:
    """Default `signal_from`: no domain finding-signal (kernel-only escalation)."""
    return None


@dataclass
class ReasoningOutcome:
    """One of exactly three outcomes for a chat turn (chat-turn-contract.md)."""
    kind: Literal["action", "blocked_local_unavailable", "paused_relay"]
    agent_action: AgentAction | None = None          # iff kind == "action"
    relay_request_id: str | None = None              # iff kind == "paused_relay"
    # FR-010 visibility — which tier produced this and why it escalated.
    tier: Literal["local", "relay", "additional", "blocked_local_unavailable"] = "local"
    escalation_trigger: EscalationTrigger | None = None
    escalation_source: Literal["model_self_report", "deterministic_guard"] | None = None


@dataclass
class ChatReasoningProvider:
    """Local-first reasoning with deterministic escalation routing to relay.

    `session` and `existing_findings` are needed only to run `evaluate_triggers`;
    the provider never touches ChatSession/SessionFacts (those are folded into
    `messages` by the caller — see the contract).
    """
    # Any reasoning client that is duck-compatible on the two methods used below
    # (`ready()` and `generate(prompt, fmt=…)`): the local Ollama client, or the
    # optional GeminiClient (spec 018). Only those two methods are ever called.
    local: LocalClient | GeminiClient
    session: "Session"
    relay_dir: Path
    # Optional ADDITIONAL agent (spec 019) consulted on escalation. Same duck
    # interface (`generate(prompt, fmt=…)`). None → escalation uses the file relay.
    additional: LocalClient | GeminiClient | None = None
    existing_findings: list = field(default_factory=list)
    evaluate_fn: Callable[..., EscalationResult] = evaluate_triggers
    # Audit-domain pieces, injected by the composition root (feature 004, R6).
    # Defaults keep the provider runnable pack-less (generic escalation only).
    system_prompt: str = ""
    signal_from: Callable[[AgentAction], object | None] = _no_signal
    domain_escalation: Callable[..., "EscalationResult | None"] | None = None

    def complete(self, messages: list[dict]) -> ReasoningOutcome:
        # 1. Readiness (R10) — refuse-and-wait, NEVER relay-as-substitute (FR-011).
        if not self.local.ready():
            logger.info("chat turn blocked: local model not ready")
            return ReasoningOutcome(
                kind="blocked_local_unavailable", tier="blocked_local_unavailable"
            )

        # 2. Local generation, strict AgentAction parse (ValueError propagates to
        #    the caller's existing malformed-response handling — never falls to relay).
        raw = self.local.generate(self._render(messages), fmt="json")
        agent_action = self._parse(raw)

        # 3. Escalation check — deterministic guard FIRST, then model self-report (R3).
        #    The finding-signal + domain triggers are pack-supplied (R6); with no
        #    pack the generic guards still run.
        finding = self.signal_from(agent_action)
        esc = self.evaluate_fn(
            action=None, record=None, finding=finding,
            session=self.session, existing_findings=self.existing_findings,
            domain_escalation=self.domain_escalation,
        )
        if esc.triggered:
            return self._escalate(messages, esc.trigger, "deterministic_guard")
        if agent_action.escalation_trigger is not None:
            return self._escalate(messages, agent_action.escalation_trigger, "model_self_report")

        # 4. Common fast path — local answer, no escalation.
        return ReasoningOutcome(kind="action", agent_action=agent_action, tier="local")

    # ── internals ────────────────────────────────────────────────────────────

    def _escalate(self, messages, trigger, source) -> ReasoningOutcome:
        context = self._render(messages)
        # ADDITIONAL agent configured (spec 019): consult it automatically instead
        # of the manual file relay. Its answer is a normal AgentAction that RE-ENTERS
        # run_turn's own action path (tool dispatch / request_confirmation) — it is
        # NOT re-fed through _escalate, so a privileged action still hits the human
        # gate (paused_confirmation) and no re-escalation loop can form (C2). The
        # resulting ChatTurn is external_llm_output like every model turn.
        if self.additional is not None:
            try:
                raw = self.additional.generate(context, fmt="json")
            except (ModelUnavailableError, GeminiUnavailable) as e:
                # C1: additional unreachable / bad key / missing SDK → fall back to
                # the relay so the escalation still resolves; never an unhandled raise.
                logger.warning("additional agent unavailable (%s) — falling back to relay", e)
            else:
                action = self._parse(raw)  # malformed JSON → ValueError, handled by run_turn
                logger.info("chat turn escalated to ADDITIONAL agent (%s)", source)
                return ReasoningOutcome(
                    kind="action", agent_action=action, tier="additional",
                    escalation_trigger=trigger, escalation_source=source,
                )
        req = request_analysis(target=f"chat:{self.session.session_id}", context=context, relay_dir=self.relay_dir)
        logger.info("chat turn escalated to relay (%s, trigger=%s)", source, trigger)
        return ReasoningOutcome(
            kind="paused_relay", relay_request_id=req.request_id, tier="relay",
            escalation_trigger=trigger, escalation_source=source,
        )

    def _render(self, messages: list[dict]) -> str:
        body = "\n\n".join(m["content"] for m in messages if m.get("content"))
        return f"{self.system_prompt}\n\n{body}" if body else self.system_prompt

    def _parse(self, raw: str) -> AgentAction:
        """Strict AgentAction parse — mirrors ClaudeClient._parse_response."""
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"AgentAction parse failed: {e}") from e
        return AgentAction.model_validate(data)
