# secure-agent-kernel

A task-agnostic agent runtime (`sr_agent`) whose orchestration-plane controls resist memory injection independently of the task a CapabilityPack supplies.

secure-agent-kernel (importable as `sr_agent`) is a reusable agent runtime that treats memory injection as an orchestration-plane threat: malicious content planted in persistent memory must not gain authority over future sessions. Against five Memory Injection vectors (MI-001 through MI-005), the in-repo Secure-axis suite pins a protected Attack Success Rate (ASR) of at most 0.05, measured with no live LLM call. The suite exercises HMAC integrity, SourceType trust tiers, privileged-status gates, principal isolation, and DATA-wrapping. A CapabilityPack may register tools and domain actions, but cannot lower those kernel guardrails.

## The problem

Memory injection is a threat class in which content written into an agent's persistent memory steers later sessions - into exfiltration, skipped analysis, or a false "safe" verdict - after the injecting turn is gone. A defense that only hardens the current prompt does not touch this surface: the attack lives in the store the agent will trust next time.

The class is recognized in industry guidance as [ASI06: Memory & Context Poisoning](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications/) in the [OWASP Top 10 for Agentic Applications](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications/). Prompt-wording guardrails share a channel with the attacker; a poisoned record can instruct the model to disregard them with the same authority the model was told to respect.

This repository's threat model, five concrete vectors (MI-001..005), and the mechanisms that neutralize each are in [`docs/mi-threat-model.md`](docs/mi-threat-model.md).

## What the kernel guarantees

Each item is a checkable claim enforced in code and covered by tests (including a hostile-pack property: a pack that tries to skip confirmation or author `human_input`-tier content is rejected or ineffective).

| Invariant | Verifiable claim | Where |
|-----------|------------------|--------|
| DATA-wrapping | Every tool output and prior-turn artifact re-entering the model is wrapped in `[DATA START]…[DATA END]` and treated as data, never as an instruction. | `sr_agent/orchestrator/context.py`; `tests/security/test_report_not_instruction.py` |
| SourceType trust hierarchy | Every memory record carries a provenance tier; the trust order is a kernel constant (`human_input` > `tool_output` > `external_llm_output` / `human_relayed_tool` > `llm_inference`). Model/relay output is never promoted to `human_input`. | `sr_agent/models/memory.py` (`TRUST_LEVELS`); `tests/security/mi_scenarios.py` |
| HMAC append-only memory | Every record is HMAC-signed by the orchestrator; records that fail verification are silently dropped before reaching the model. No update/delete - corrections are new `supersede` records, and only `human_input` may issue them or set privileged statuses. | `sr_agent/memory/hmac.py`, `sr_agent/memory/episodic.py`; `tests/security/test_mi_resistance.py` |
| Out-of-band confirmation gate | An irreversible/privileged action pauses the run and requires approval on a separate channel. Confirmation is kernel-derived from `action.action_class == write_execute`, not from a pack-set flag. | `sr_agent/orchestrator/action.py` (`validate_action`); `tests/security/test_hostile_pack.py` |
| Per-turn tool-call budget | Tool calls per turn are hard-capped (`MAX_TOOL_CALLS_PER_TURN`); on reaching the budget the turn stops calling tools. | `sr_agent/models/chat.py`, `sr_agent/orchestrator/loop.py`; `tests/security/test_chat_mi_scenarios.py` (SC-005) |
| Escalation machinery | The reasoning path may escalate (local → relay/stronger model) on low confidence; the tier remains visible and does not change trust promotion rules. | `sr_agent/guardrails/escalation.py`, `sr_agent/orchestrator/relay.py` |

Full write-up: [`docs/kernel.md`](docs/kernel.md). Pack boundary (a pack cannot lower a guardrail): [`docs/capability-pack-interface.md`](docs/capability-pack-interface.md), `tests/security/test_hostile_pack.py`, `tests/architecture/test_kernel_pack_boundary.py`.

## What it does NOT claim

These limits are part of the Secure-axis definition in [`docs/mi-eval.md`](docs/mi-eval.md) and [`docs/mi-threat-model.md`](docs/mi-threat-model.md). They are not caveats to hide; they are the measurement boundary.

- **Mechanisms, not model behaviour.** The MI suite measures deterministic orchestration-plane checks (principal isolation → HMAC → status gate → capability restriction → write-time policy). It does not measure how a live model behaves under adversarial prompts.
- **No live LLM calls on the Secure axis.** `measure_asr` / `run_scenario` exercise fixture records and kernel gates in pure Python. A mechanism-independent guarantee is exactly one that model wording cannot move.
- **Baseline percentages are literature stand-ins.** Per-vector baseline figures cited in the threat model come from Memory Injection literature and stand in for "what a naive agent would do." They are not a local re-measurement of an unprotected production agent in this repository. The pinned tests assert baseline ASR ≥ 0.40 (scenario realism), protected ASR ≤ 0.05, and baseline - protected ≥ 0.40 (`tests/security/test_mi_resistance.py`).
- **Not a behavioural red-team benchmark.** This is not an adversarial-prompt behavioural benchmark against a hosted or local model. A live-model red-team harness would be a separate, additive eval axis.
- **Langfuse / Find / Prove are out of scope here.** `eval/tracer.py` supports downstream Find scoring; it is not part of the MI ASR gate. Find recall/FPR and Prove PoC-workability belong to the downstream [araratsec-agent](https://github.com/RamilRamil/araratsec-agent) pack, not this repo.
- **No composition root ships here.** The package is import-only (`sr_agent`). Audit tools, Docker-backed PoC execution, and the operator CLI live downstream.

## Related work

One line each; differences only - not rankings.

| Work | How it differs from this kernel |
|------|----------------------------------|
| [arXiv:2503.16248](https://arxiv.org/abs/2503.16248) - *Real AI Agents with Fake Memories* | Provides the attack taxonomy the kernel's threat model follows; this repo encodes that taxonomy as the reproducible MI-001..005 suite and orchestration-plane controls. |
| [arXiv:2606.24322](https://arxiv.org/abs/2606.24322) - TMA-NM | Formal treatment of non-malleable, origin-bound authority; this kernel is an engineering runtime with code-enforced trust tiers, HMAC memory, and a CapabilityPack boundary, not a formal NM proof system. |
| [arXiv:2604.02623](https://arxiv.org/abs/2604.02623) - *Poison Once, Exploit Forever* | Studies poisoning via environment observation; the kernel's Secure axis focuses on persistent memory records and pack-boundary properties under the MI suite. |
| [arXiv:2607.27080](https://arxiv.org/abs/2607.27080) - MemSecBench | A benchmark of the memory lifecycle; this repository's Secure axis is a deterministic mechanism suite with pinned ASR thresholds, not that benchmark. |

## Quick start

Secure axis only - no reasoning backend, no Docker, no network:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export SR_SECRET_KEY=<64-hex>   # HMAC key material for memory records
pytest -q tests/security tests/architecture
```

`gemini` is an optional extra (`pip install -e ".[dev,gemini]"`). The MI and boundary suites do not need it.

Measurement details: [`docs/mi-eval.md`](docs/mi-eval.md).

## Documentation

This repository ships an [OKF v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf) knowledge bundle under `docs/`. **Bilingual convention:** each concept has an English base file (`name.md`) and a Russian sibling (`name.ru.md`), cross-linked at the top.

| Document | Contents |
|----------|----------|
| [`docs/index.md`](docs/index.md) · [ru](docs/index.ru.md) | Bundle index |
| [`docs/kernel.md`](docs/kernel.md) · [ru](docs/kernel.ru.md) | Two-plane split, MI invariants, CapabilityPack boundary |
| [`docs/mi-threat-model.md`](docs/mi-threat-model.md) · [ru](docs/mi-threat-model.ru.md) | MI-001..005, neutralizing mechanisms, honest shape of the ASR claim |
| [`docs/capability-pack-interface.md`](docs/capability-pack-interface.md) · [ru](docs/capability-pack-interface.ru.md) | Pack contract; hostile-pack H1–H4; boundary B1–B6 |
| [`docs/mi-eval.md`](docs/mi-eval.md) · [ru](docs/mi-eval.ru.md) | Secure axis: ASR definition, suite map, what numbers do and do not claim |
| [`docs/diagrams/`](docs/diagrams/) | Architecture, turn flow, memory-trust flow |
| [`docs/log.md`](docs/log.md) | Bundle change history |

Downstream first pack (audit agent): [araratsec-agent](https://github.com/RamilRamil/araratsec-agent).

## Articles

Updated as the series progresses.

| Title | Language | Platform | Link |
|-------|----------|----------|------|
| - | - | - | - |

## Author

**Ramil Mustafin**

- GitHub: [https://github.com/RamilRamil](https://github.com/RamilRamil)
- Medium: [https://medium.com/@ramilmustafin33](https://medium.com/@ramilmustafin33)
- X: [https://x.com/Donram33](https://x.com/Donram33)
- LinkedIn: [https://www.linkedin.com/in/ramil-mustafin-web3/](https://www.linkedin.com/in/ramil-mustafin-web3/)
- Telegram (Russian): [https://t.me/web3securityresearch](https://t.me/web3securityresearch)
