# secure-agent-kernel

A task-agnostic, memory-injection-resistant agent kernel. Importable as `sr_agent`.

The kernel runs an LLM orchestration loop whose security invariants hold **independently
of the task it is pointed at** (mechanism independence, MI). A task is supplied at runtime as
a `CapabilityPack` injected through a narrow `PackContext`; the pack declares actions, tools,
and prompts, but **cannot lower a guardrail** — confirmation requirements, trust tiers, and
sandbox posture are kernel-derived and untouchable by any pack.

## What the kernel guarantees (MI)

- **DATA-wrapping** — every external / tool output re-enters the model context inside
  `[DATA START …] … [DATA END]` sentinels, so untrusted data is inert, never instructions.
- **Out-of-band write gate** — the kernel derives "write_execute ⇒ confirm"; a pack cannot
  mark a state-changing action as auto-approved.
- **Kernel-set trust tier** — `SourceType` and the HMAC over memory records are set by the
  kernel; a pack has no memory handle and cannot forge a `human_input`-tier record.
- **Shape-agnostic model routing** — the kernel resolves `routing[role]` from an arbitrary
  `Mapping[str, str]` injected via `PackContext`; it knows no task-specific role names and
  carries no task-specific default. A missing role raises, it never silently falls back.

These are proven in this repo against an in-repo **fixture pack** (`tests/fixtures/pack/`),
with no task/capability code present — see `tests/security/test_chat_mi_scenarios.py` and
`tests/architecture/test_kernel_pack_boundary.py`.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export SR_SECRET_KEY=<64-hex>   # HMAC key material for memory records
pytest -q
```

`gemini` is an optional extra (`pip install -e ".[dev,gemini]"`); the core loop runs on the
local model / relay without it.

## What is NOT here

Audit capability — on-chain reads (`web3`), sandbox containers (`docker`), the Solidity
toolchain, PoC generation, and the audit `CapabilityPack` — lives in the downstream
**araratsec-agent** repo, which depends on this package. The kernel has no `web3`/`docker`
dependency and no task-specific code.
