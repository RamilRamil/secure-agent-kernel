# The Kernel — a task-agnostic, memory-injection-resistant secure agent

The kernel is the **reusable core** of this project and its primary research
contribution: a secure agent runtime whose safety guarantees hold **regardless of
what the language model is told to do** — including by malicious content planted in
its own memory. It knows nothing about smart-contract auditing (or any other task).
The audit agent is just the [first capability pack](audit-agent.md) that rides on
top of it.

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
change what the model proposes but not what the kernel permits.

The threat this targets is **Memory Injection (MI)**: malicious content planted in an
agent's memory that steers future sessions into exfiltration, skipped analysis, or a
false "safe" verdict. Unprotected agents show 55–85% attack success; the kernel's
architectural controls drive that toward ≤5% — by construction, not by prompt wording.

## Kernel invariants (the guarantees a pack can never weaken)

1. **DATA-wrapping** — every tool output and every prior-turn artifact re-entering the
   model is wrapped in `[DATA START]…[DATA END]` and treated as data, never as an
   instruction.
2. **SourceType trust hierarchy** — every memory record carries a provenance tier
   (`human_input` > `tool_output` > `external_llm_output`/`human_relayed_tool` >
   `llm_inference`). Model/relay output is never promoted to `human_input`.
3. **HMAC append-only memory** — every record is HMAC-signed by the orchestrator;
   records that fail verification are silently dropped before reaching the model.
   No update/delete — corrections are new records that `supersede`, and only
   `human_input` may issue them or set privileged statuses.
4. **Out-of-band confirmation gate** — an irreversible/privileged action pauses the
   run and requires a deliberate approval through a *separate* channel. This is
   derived from the action's `action_class == write_execute`, not from a pack-set flag.
5. **Per-turn tool-call budget** — a hard cap on tool calls per turn.
6. **Escalation machinery** — the reasoning path can escalate (local → relay/stronger
   model) on low-confidence or self-reported uncertainty; tier is always visible.

These are enforced in code and covered by tests (including a hostile-pack property
test: a pack that tries to register a `write_execute` tool as not-requiring
confirmation, or to author `human_input`-tier content, is rejected/ineffective).

## Kernel modules

```
sr_agent/
  orchestrator/   loop, action, confirmation, context, checkpoint,
                  chat_session, pack (the CapabilityPack interface), relay
  guardrails/     sanitize, escalation (generic triggers)
  memory/         episodic (HMAC store), hmac, knowledge
  models/         memory (SourceType), principal, session, chat, action
  llm_core/       local_client (Ollama), chat_reasoning, relay, router, claude_client
  tools/          sandbox (network-isolated Docker), registry (the mechanism)
  config.py       env-driven config
  cli.py          a composition root
```

Boundary rule (enforced by an architecture test): **no kernel module imports any
`sr_agent.packs` module.** Packs depend on the kernel, never the reverse.

## The CapabilityPack interface

A pack is **declarative and constrained**. It plugs in through one frozen dataclass
(`sr_agent/orchestrator/pack.py`):

```python
CapabilityPack(
    name,                 # str
    actions,              # Mapping[str, ActionSpec]  — name, action_class, param validators
    tools,                # Sequence[ToolDefinition]  — name, description, handler
    privileged_statuses,  # frozenset[str]            — domain statuses only human_input may set
    domain_escalation,    # Callable                  — extra escalation triggers
    dispatch / execute_confirmed / persist_finding,   # domain callables
)
```

Pack callables receive only a narrow `PackContext` (audit_root, sandbox, poc_dir,
wrap_data, poc_generator) — **never the loop, never a memory-write handle**. A pack
can register tools and mark actions high-risk, but it has no lever to skip the OOB
gate, forge a trust tier, or touch the HMAC store. There is intentionally **no dynamic
plugin registry** — the one pack is wired explicitly (YAGNI); the boundary is the
value.

## What it needs to run

The kernel is not run on its own — it is driven by a **composition root** that pairs it
with a pack. Requirements:

- **Python ≥ 3.11**, deps from `pyproject.toml`.
- **`SR_SECRET_KEY`** (32-byte hex) — the HMAC signing key. Required. Other roots
  (`SR_MEMORY_ROOT`, `SR_CONFIRMATIONS_ROOT`, `SR_RELAY_ROOT`) default to `./…`.
- **A reasoning backend** — a local model via Ollama (`local_client`), the manual
  file **relay**, or (opt-in) a paid API. No paid key is required to run.
- **Docker** — only if a pack executes attacker/model-influenced code, which the
  kernel always runs `--network none` in an ephemeral sandbox.

Composition roots today: `sr_agent/cli.py` (the `sr-agent` CLI) and the
[operator frontend](../frontend/) (FastAPI + Svelte). Both build the same
`OrchestratorLoop(pack=…, …)`.

See [audit-agent.md](audit-agent.md) for the pack that demonstrates all of this, and
[roadmap.md](roadmap.md) for the build history.
