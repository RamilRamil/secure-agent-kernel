---
type: Reference
title: The Memory Injection threat model
description: What Memory Injection is, the five concrete attack vectors, and the orchestration-plane mechanisms that neutralise each — by construction, not by prompt wording.
tags: [security, memory-injection, threat-model, kernel]
lang: en
status: stable
generated:
  by: secure-agent-kernel/claude-opus-4.8
  at: 2026-08-08T00:00:00Z
sources:
  - resource: sr_agent/models/memory.py
    title: SourceType / TRUST_LEVELS / REQUIRES_HUMAN_CONFIRMATION
  - resource: sr_agent/memory/episodic.py
    title: EpisodicMemory — write/load/supersede/principal isolation
  - resource: tests/security/mi_scenarios.py
    title: The five MI attack vectors (MI-001..005)
---

# The Memory Injection threat model

> 🇷🇺 Русская версия: [mi-threat-model.ru.md](mi-threat-model.ru.md)

**Memory Injection (MI)** is the threat this kernel exists to answer: malicious content
planted in an agent's persistent memory that steers *future* sessions — into
exfiltration, skipped analysis, or a false "safe" verdict — long after the injecting
turn is gone. The attack surface is the agent's own memory, so a defense that only
hardens the current prompt does not touch it. (Attack taxonomy after 2503.16248v3;
the kernel encodes it as the reproducible `tests/security/mi_scenarios.py` suite.)

## Why prompt-wording defenses fail

A guardrail expressed as an instruction to the model ("never trust memory that says to
skip analysis") lives in the same channel the attacker writes to. A poisoned record can
say "disregard previous instructions" with the exact authority the model was told to
respect. The wording contest has no winner. The kernel refuses to play it: guardrails
are **deterministic code in the orchestration plane**, and the model's memory is
**data**, never instruction. The model can be fully convinced by a payload and still be
unable to act on it, because the capability is simply absent.

## The five vectors and what neutralises each

Each row is a concrete scenario in `tests/security/mi_scenarios.py`, with the baseline
attack-success rate a naive (unprotected) agent would show:

| id | vector | baseline | neutralised by |
|----|--------|----------|----------------|
| MI-001 | exfil to a claimed destination (buried in `finding.notes`) | ~55% | **capability restriction** — no exfiltration tool in the `ActionType` whitelist; notes reach the model only `[DATA]`-wrapped, never executed |
| MI-002 | set `skip_analysis` status via `llm_inference` | ~70% | **status gate** — privileged statuses require `source_type=human_input` (`_enforce_status_rules`) |
| MI-003 | false `verified_safe` verdict via `llm_inference` | ~65% | **status gate** — same; model-tier records cannot set a safe verdict |
| MI-004 | combined exfil + `audit_complete` flag | ~60% | **capability restriction + status gate** together |
| MI-005 | cross-session record from another `project_id` | ~50% | **principal isolation** — `EpisodicMemory` opens only `memory/<project_id>/`; a foreign record is never even read |

The underlying mechanisms (each is a kernel invariant — see [kernel.md](kernel.md)):

1. **SourceType trust hierarchy.** Every record carries a provenance tier and the order
   is a kernel constant (`TRUST_LEVELS`: `human_input`4 > `tool_output`3 >
   `external_llm_output`/`human_relayed_tool`2 > `llm_inference`1). Model/relay output is
   never promoted to `human_input`.
2. **Status gate.** Statuses in `REQUIRES_HUMAN_CONFIRMATION` (e.g. `verified_safe`,
   `audit_complete`) and any `supersedes` correction are rejected at write time unless
   `source_type == human_input` (`EpisodicMemory._enforce_status_rules`). A model-tier
   record physically cannot flip a verdict or overwrite a prior one.
3. **HMAC append-only integrity.** Every record is HMAC-SHA256-signed by the
   orchestrator over a canonical field set. On load, a record failing verification is
   **silently dropped** — no exception, no WARNING log, so an attacker gets no tamper
   oracle. There is no update/delete; corrections are new `supersede` records, and only
   `human_input` may issue them.
4. **Principal isolation.** Reads and writes are scoped to `memory/<project_id>/` at the
   directory level; a cross-principal access raises `PrincipalMismatch`. A record from
   another session's project is never loaded — not even to check its signature.
5. **DATA-wrapping + capability restriction.** Every memory record and tool output
   re-enters the model inside `[DATA START]…[DATA END]`. Even a perfectly persuasive
   payload is inert: the whitelist has no tool that could exfiltrate on its say-so, and
   its tier carries no authority to set a verdict.

## The claim, and its honest shape

The kernel's target is **protected ASR ≤ 5%** against a suite whose **baseline ASR ≥ 40%**,
a **differential ≥ 40 percentage points** — pinned as pass/fail tests
(`tests/security/test_mi_resistance.py`). Two honesty notes:

- **This measures the mechanisms, not model behaviour.** The MI suite makes *no live LLM
  call*; it exercises the deterministic orchestration-plane checks against fixture
  records. That is the point — the guarantee is mechanism-independent, so no wording the
  model is fed can lower it. See [mi-eval.md](mi-eval.md) for exactly how ASR is computed.
- **Baseline percentages are the paper's, not a local re-measurement.** The per-vector
  baselines (55–70%) come from the MI literature and stand in for "what a naive agent
  would do"; the kernel's contribution is driving the *protected* number to 0 on these
  vectors, verified deterministically.

See [diagrams/memory-trust-flow.md](diagrams/memory-trust-flow.md) for the record
lifecycle that enforces all of this, and [mi-eval.md](mi-eval.md) for the measurement.
