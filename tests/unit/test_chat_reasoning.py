"""Tests for ChatReasoningProvider (feature 003, T005).

Exercises the chat-turn contract with fakes — no Ollama, no Docker, no real relay
round-trip. Only the request-file side effect of relay escalation is checked.
"""
from __future__ import annotations

from pathlib import Path

from sr_agent.guardrails.escalation import EscalationResult
from sr_agent.llm_core.chat_reasoning import ChatReasoningProvider
from sr_agent.llm_core.schemas import EscalationTrigger
from sr_agent.packs.audit.session import AuditInput, AuditSession, Principal

_ACTION_JSON = (
    '{"next_action":"complete","tool_params":{},"finding":null,'
    '"reasoning_summary":"the answer","escalation_trigger":null}'
)
_SELF_ESCALATE_JSON = (
    '{"next_action":"complete","tool_params":{},"finding":null,'
    '"reasoning_summary":"unsure","escalation_trigger":"unknown_pattern"}'
)


def _session() -> AuditSession:
    p = Principal(user_id="u", platform="cli", project_id="proj")
    return AuditSession(principal=p, audit_input=AuditInput(path=Path("."), principal=p))


class FakeLocal:
    def __init__(self, ready_seq=(True,), gen: str = _ACTION_JSON):
        self._ready = list(ready_seq)
        self._gen = gen
        self.gen_calls = 0

    def ready(self) -> bool:
        return self._ready.pop(0) if len(self._ready) > 1 else self._ready[0]

    def generate(self, prompt: str, fmt=None) -> str:
        self.gen_calls += 1
        return self._gen


def _no_escalation(**_kw) -> EscalationResult:
    return EscalationResult(triggered=False)


def _provider(tmp_path: Path, local, evaluate_fn=_no_escalation) -> ChatReasoningProvider:
    return ChatReasoningProvider(
        local=local, session=_session(), relay_dir=tmp_path, evaluate_fn=evaluate_fn
    )


def _relay_files(tmp_path: Path) -> list[Path]:
    return list(tmp_path.glob("requests/*"))


# ── FR-011: refuse-and-wait, never relay-as-substitute ─────────────────────

def test_local_not_ready_blocks_and_never_files_relay(tmp_path):
    local = FakeLocal(ready_seq=(False,))
    outcome = _provider(tmp_path, local).complete([{"role": "user", "content": "hi"}])
    assert outcome.kind == "blocked_local_unavailable"
    assert outcome.tier == "blocked_local_unavailable"
    assert local.gen_calls == 0                       # never even generated
    assert _relay_files(tmp_path) == []               # never escalated to relay


def test_reachable_but_wedged_blocks(tmp_path):
    # models available()==True but ready()==False (wedged Ollama, R10)
    local = FakeLocal(ready_seq=(False,))
    outcome = _provider(tmp_path, local).complete([{"role": "user", "content": "hi"}])
    assert outcome.kind == "blocked_local_unavailable"
    assert local.gen_calls == 0


# ── Common path + escalation routing (R3) ──────────────────────────────────

def test_local_action_no_escalation(tmp_path):
    outcome = _provider(tmp_path, FakeLocal()).complete([{"role": "user", "content": "q"}])
    assert outcome.kind == "action"
    assert outcome.tier == "local"
    assert outcome.agent_action is not None
    assert outcome.agent_action.next_action == "complete"
    assert _relay_files(tmp_path) == []


def test_deterministic_guard_escalates_to_relay(tmp_path):
    def guard(**_kw):
        return EscalationResult(triggered=True, trigger=EscalationTrigger.critical_finding)

    outcome = _provider(tmp_path, FakeLocal(), evaluate_fn=guard).complete(
        [{"role": "user", "content": "q"}]
    )
    assert outcome.kind == "paused_relay"
    assert outcome.tier == "relay"
    assert outcome.escalation_source == "deterministic_guard"
    assert outcome.escalation_trigger == EscalationTrigger.critical_finding
    assert outcome.relay_request_id
    assert len(_relay_files(tmp_path)) >= 1            # relay request was filed


def test_model_self_report_escalates_when_guard_silent(tmp_path):
    # deterministic guard silent, but the model itself asks to escalate
    local = FakeLocal(gen=_SELF_ESCALATE_JSON)
    outcome = _provider(tmp_path, local, evaluate_fn=_no_escalation).complete(
        [{"role": "user", "content": "q"}]
    )
    assert outcome.kind == "paused_relay"
    assert outcome.escalation_source == "model_self_report"
    assert outcome.escalation_trigger == EscalationTrigger.unknown_pattern


# ── FR-011 auto-recovery: blocked → available again → normal, no manual step ─

def test_auto_recovers_when_ready_again(tmp_path):
    local = FakeLocal(ready_seq=(False, True))       # not ready, then ready
    prov = _provider(tmp_path, local)
    first = prov.complete([{"role": "user", "content": "q"}])
    assert first.kind == "blocked_local_unavailable"
    second = prov.complete([{"role": "user", "content": "q"}])
    assert second.kind == "action"                    # recovered automatically
