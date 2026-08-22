---
type: Reference
title: The Kernel — a task-agnostic, memory-injection-resistant secure agent
description: The reusable secure-agent runtime — the two-plane split, the MI invariants, and the CapabilityPack boundary a pack can never cross.
tags: [kernel, security, memory-injection, capability-pack]
lang: en
status: stable
generated:
  by: human:ramilmustafin
  at: 2026-07-05T03:30:49+04:00
sources:
  - resource: sr_agent/orchestrator/pack.py
    title: CapabilityPack / PackContext
  - resource: sr_agent/orchestrator/action.py
    title: validate_action — the OOB gate
  - resource: sr_agent/models/memory.py
    title: SourceType trust hierarchy
---

# The Kernel — a task-agnostic, memory-injection-resistant secure agent

> 🇷🇺 Русская версия: [kernel.ru.md](kernel.ru.md)

The kernel is the **reusable core** of this project and its primary research
contribution: a secure agent runtime whose safety guarantees hold **regardless of
what the language model is told to do** — including by malicious content planted in
its own memory. It knows nothing about smart-contract auditing (or any other task).
The audit agent is just the [first capability pack](https://github.com/RamilRamil/araratsec-agent)
that rides on top of it.

## The essence

Most agent frameworks put every capability and every guardrail in one place, where
a cleverly-worded prompt (or a poisoned memory record) can talk the model out of the
guardrail. The kernel instead splits the system into two planes:

```
┌──────────────────────────── Orchestration Plane (deterministic code) ─────────┐
│  DATA-wrapping · SourceType trust hierarchy · HMAC append-only memory ·        │
│  out-of-band confirmation gate · per-turn tool-call budget · escalation ·      │
│  path-containment + network-isolated sandbox                                   │
└──────────────────────────────────────┬────────────────────────────────────────┘
                                        │ narrow, typed interface
┌──────────────────────────────────────▼────────────────────────────────────────┐
│  LLM Context Plane (probabilistic model)                                        │
│  local model / relay / paid API · every external artifact wrapped [DATA]…[DATA] │
└─────────────────────────────────────────────────────────────────────────────────┘
```

All security lives in the orchestration plane, in ordinary code. The model can only
*propose*; the kernel decides. A prompt-injection or memory-injection attack can
change what the model proposes but not what the kernel permits. The full module map is
in [diagrams/kernel-architecture.md](diagrams/kernel-architecture.md).

The threat this targets is **Memory Injection (MI)**: malicious content planted in an
agent's memory that steers future sessions into exfiltration, skipped analysis, or a
false "safe" verdict. Unprotected agents show 55–85% attack success; the kernel's
architectural controls drive that toward ≤5% — by construction, not by prompt wording.

## Kernel invariants (the guarantees a pack can never weaken)

1. **DATA-wrapping** — every tool output and every prior-turn artifact re-entering the
   model is wrapped in `[DATA START]…[DATA END]` and treated as data, never as an
   instruction (`orchestrator/context.py`).
2. **SourceType trust hierarchy** — every memory record carries a provenance tier, and
   the trust order is a kernel constant, not runtime state
   (`models/memory.py`, `TRUST_LEVELS`):

   | tier | value | who/what |
   |------|-------|----------|
   | `human_input` | 4 | the operator, in-band |
   | `tool_output` | 3 | a whitelisted tool |
   | `external_llm_output` / `human_relayed_tool` | 2 | model output / relayed tool text |
   | `llm_inference` | 1 | the model's own reasoning |

   Model/relay output is **never** promoted to `human_input`.
3. **HMAC append-only memory** — every record is HMAC-signed by the orchestrator
   (`memory/hmac.py`, `memory/episodic.py`); records that fail verification are
   silently dropped before reaching the model. No update/delete — corrections are new
   records that `supersede`, and only `human_input` may issue them or set privileged
   statuses.
4. **Out-of-band confirmation gate** — an irreversible/privileged action pauses the
   run and requires a deliberate approval through a *separate* channel. This is
   **kernel-derived** from `action.action_class == write_execute`
   (`orchestrator/action.py::validate_action`), not from a pack-set flag.
5. **Per-turn tool-call budget** — a hard cap on tool calls per turn.
6. **Escalation machinery** — the reasoning path can escalate (local → relay/stronger
   model) on low-confidence or self-reported uncertainty; tier is always visible
   (`guardrails/escalation.py`, `orchestrator/relay.py`).

These are enforced in code and covered by tests (including a hostile-pack property
test: a pack that tries to register a `write_execute` tool as not-requiring
confirmation, or to author `human_input`-tier content, is rejected/ineffective —
`tests/security/test_hostile_pack.py`, `tests/architecture/test_kernel_pack_boundary.py`).

## Kernel modules

```
sr_agent/
  orchestrator/   loop (OrchestratorLoop.run / run_turn), action (validate_action),
                  confirmation (OOB gate), context (DATA-wrapping), chat_session,
                  pack (the CapabilityPack interface), relay (manual/file reasoning)
  guardrails/     sanitize, escalation (generic triggers)
  memory/         episodic (HMAC store), hmac, knowledge, lessons
  models/         memory (SourceType/MemoryRecord), action, principal, session, chat
  llm_core/       local_client, claude_client, gemini_client, openrouter_client,
                  router (resolves routing[role]), chat_reasoning, schemas
  tools/          registry (the whitelist mechanism), sandbox (--network none Docker),
                  readonly
  config.py       env-driven config      io/progress   eval/tracer (Langfuse)
```

Boundary rule (enforced by an architecture test): **no kernel module imports any
downstream pack module.** Packs depend on the kernel, never the reverse.

> **Note — no composition root ships here.** The kernel is import-only (`sr_agent`,
> no `[project.scripts]`, no `cli.py`). It is driven by a composition root that lives
> **downstream** — in the [araratsec-agent](https://github.com/RamilRamil/araratsec-agent)
> repo: the `sr-agent` CLI (`audit_agent/cli.py`) and the operator frontend
> (`frontend/backend/app.py`). Both build the same `OrchestratorLoop(pack=…, …)`.

## The CapabilityPack interface

A pack is **declarative and constrained**. It plugs in through one frozen dataclass
(`sr_agent/orchestrator/pack.py`):

```python
CapabilityPack(
    name,                 # str
    actions,              # Mapping[str, ActionSpec]  — action_class, is_reversible, validate_params
    tools,                # Sequence[ToolDefinition]  — name, description, handler
    privileged_statuses,  # frozenset[str]            — domain statuses only human_input may set
    reasoning_prompt,     # str
    dispatch / execute_confirmed / persist_finding,   # domain callables
    domain_escalation, signal_from,                   # domain callables
)
```

Pack callables receive only a narrow `PackContext` — **never the loop, never a
memory-write handle**. A pack can register tools and mark actions high-risk, but it has
no lever to skip the OOB gate, forge a trust tier, or touch the HMAC store. There is
intentionally **no dynamic plugin registry** — the one pack is wired explicitly (YAGNI);
the boundary is the value.

## Scope — task-agnostic at the guarantee *and* the identifier level

The kernel's **guarantees** are task-agnostic and MI-proven against an in-repo
[fixture pack](../tests/fixtures/pack/) with no audit code present. Feature 001
(task-agnostic action contract) closed the naming residue the pre-split monorepo
left behind:

- **Action taxonomy is OPEN.** `Action.action_type` is a free `str`, not a closed
  domain enum. Domain analyzer ids (`run_slither`, `write_poc`, …) live in the
  pack's `actions`; the kernel keeps only the generic, non-domain ids it provides
  to every pack — the control/memory machinery (`write_memory`,
  `request_human_confirmation`) and the scope-bounded reads (`read_file`,
  `search_code`, decision D6). `validate_action` resolves an id against
  `KERNEL_GENERIC_ACTIONS ∪ pack.actions`, fail-closed on a miss. `write_memory`
  is kernel-executed — `KernelActionExecutor.execute` intercepts it before
  `pack.dispatch`; a pack is never invoked and must not stub it (feature 002,
  see below).
- **Privileged statuses are pack-declared.** The kernel hardcodes none; each pack
  declares its own and the kernel binds that set into `EpisodicMemory` at session
  construction (decision D5). Empty = "this pack gates nothing", not "gate off".
- **`PackContext` is domain-neutral:** `scope_root`, `sandbox`, `wrap_data`, plus
  the feature-003 values `operation_id`, `transition_key`, and `scope_policy`
  (a bound include set, not a memory handle). PoC state (`poc_dir`/`poc_generator`)
  left the context (decision D2) — a pack that runs a write_execute PoC path
  carries its own.
- `orchestrator/loop.py` returns a domain-neutral `RunResult`; the read validators
  take `scope_root`.

These are pinned by boundary test **B6** (`tests/architecture/test_kernel_pack_boundary.py`):
an open `action_type`, no domain id or privileged status operative in `sr_agent/`,
and the exact kernel-generic / loop-terminal sets.

**Remaining residue (separate concern):** the *finding model* still carries audit
vocabulary — `FindingPayload.bastet_tag` in `llm_core/schemas.py` and the relay
`_RESPONSE_SCHEMA`. De-domaining the finding schema is out of feature 001's scope
(action taxonomy + statuses) and is tracked as its own follow-up.

## What it needs to run

The kernel is not run on its own — it is driven by a **composition root** that pairs it
with a pack. Requirements:

- **Python ≥ 3.11**, deps from `pyproject.toml`.
- **`SR_SECRET_KEY`** (32-byte hex) — the HMAC signing key. Required. Other roots
  (`SR_MEMORY_ROOT`, `SR_CONFIRMATIONS_ROOT`, `SR_RELAY_ROOT`) default to `./…`.
- **A reasoning backend** — a local model via Ollama (`local_client`), the manual
  file **relay** (`orchestrator/relay.py`), or (opt-in) a hosted API
  (`claude_client` / `gemini_client` / `openrouter_client`). No paid key is required to
  run; the reasoning backend is a runtime choice, not a build-time one.
- **Docker** — only if a pack executes attacker/model-influenced code, which the
  kernel always runs `--network none` in an ephemeral sandbox (`tools/sandbox.py`).

See the downstream [araratsec-agent](https://github.com/RamilRamil/araratsec-agent) repo
for the pack that demonstrates all of this, and [log.md](log.md) for this bundle's
change history.

## Dispatch, resume, and snapshot capacity (feature 003)

Feature `003-dispatch-result-resume` makes chat and batch share one
`KernelActionExecutor`. A pending dispatch writes exactly one `pause_checkpoint`
and drops the writer lease; resume rebuilds the Action from that snapshot and
does not ask the model to restate it. System prompt bytes come from a trusted
`PromptRegistry` (pack-registered content, kernel-owned mechanism). A checkpoint
stores only `system_prompt_id` / `system_prompt_hash` as a reference.

`MemorySnapshot` is the pack's only read seam. Capacity is fixed:
**10000 items / 32 MiB**. Crossing it fails closed. The operator-facing remedy
is to **complete the session and start a new one** — not to truncate, clamp, or
silently drop items (FR-009b).

## `write_memory` and memory observability (feature 002)

`write_memory` is kernel machinery, not a pack capability: `KernelActionExecutor`
recognizes the action id before `derive_ids`/`pack.dispatch` run and handles it
itself, from a fixed record shape — never from `pack.dispatch`, and never from
`action.params` directly. A pack declares nothing for it and is never asked to
stub it.

- **Provenance is kernel-set.** The written record always carries
  `source_type=llm_inference`. Model params may supply only the note text (and
  an optional bounded `target`); any attempt to set `source_type`, `hmac`,
  `supersedes`, `status_change`, or another identity field is **rejected** at
  validation, not silently stripped.
- **Content is a plain note**, not a finding: `payload={"note": …}`,
  `payload_kind="model_note"`. Reporting a finding stays on the existing
  `persist_finding` path — a model does not call `write_memory` to report one.
- **Not a transition.** `write_memory` has no `operation_id`, no
  `commit_if_absent`, and does not move `session_revision`; two identical notes
  are two distinct records, not a deduplicated one.
- **Excluded from `SNAPSHOT_KINDS`.** A `model_note` is `llm_inference`-tier and
  is deliberately never surfaced through `MemorySnapshot` — a model's own note
  can never become a premise for a pack's projection.
- **No new OOB gate.** `ActionClass.memory` still does not gain out-of-band
  confirmation; that gate remains `write_execute` plus pack-declared privileged
  statuses only.

Separately, every successful `EpisodicMemory.write` — a model note, a finding
persist, a chat turn, a session snapshot, anything durable — now fires a
first-class `memory_write` live-trace event, distinct from `tool`/`reasoning`/
`routing` events. The event carries metadata only (never a record body, never
`hmac`/`seq`/`chain_prev`). The sink is bound once at `EpisodicMemory`
construction; an absent sink, or one that raises, never blocks or reverses the
write it would have reported.
