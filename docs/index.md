---
type: Reference
title: secure-agent-kernel documentation
description: OKF knowledge bundle for the task-agnostic, memory-injection-resistant agent kernel (sr_agent).
okf_version: "0.2"
tags: [index, documentation, kernel]
lang: en
status: stable
generated:
  by: secure-agent-kernel/claude-opus-4.8
  at: 2026-08-08T00:00:00Z
---

# secure-agent-kernel documentation

> 🇷🇺 Русская версия: [index.ru.md](index.ru.md)

This directory is an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf)
(OKF v0.2) bundle: every concept is a Markdown file with YAML frontmatter, versioned in
git alongside the code it describes. Change history is in [log.md](log.md).

**Bilingual convention.** Each concept has an English base file (`name.md`) and a Russian
sibling (`name.ru.md`), cross-linked at the top and carrying `lang: en` / `lang: ru`. Keep
the pair in sync when the wiring changes.

**Provenance.** `generated.by` follows the OKF actor convention: `human:<id>` for
human-authored content, `<producer>/<model>` for agent-drafted content. Agent-drafted docs
have not yet been human-`verified`; add a `verified` entry after review.

## Concepts

- [kernel.md](kernel.md) · [🇷🇺](kernel.ru.md) — the reusable secure-agent core: the
  two-plane split, the MI invariants (DATA-wrapping, `SourceType` trust hierarchy, HMAC
  append-only memory, the kernel-derived OOB gate), the `CapabilityPack` boundary, and an
  honest note on task-agnosticism naming residue.
- [mi-threat-model.md](mi-threat-model.md) · [🇷🇺](mi-threat-model.ru.md) — Memory Injection
  in depth: the five attack vectors (MI-001..005), what neutralises each, and the honest
  shape of the ≤5% ASR claim.
- [capability-pack-interface.md](capability-pack-interface.md) · [🇷🇺](capability-pack-interface.ru.md) —
  the declarative pack contract and the tested property that a pack can never lower a
  guardrail (hostile-pack H1/H2/H3, boundary B1..B5).
- [mi-eval.md](mi-eval.md) · [🇷🇺](mi-eval.ru.md) — the Secure axis: how ASR is computed, the
  security + boundary test suite, and what the numbers do and do not claim.

## Diagrams

- [diagrams/kernel-architecture.md](diagrams/kernel-architecture.md) · [🇷🇺](diagrams/kernel-architecture.ru.md) —
  the two-plane split and `sr_agent` module map, with the downstream composition root /
  pack drawn as external.
- [diagrams/turn-flow.md](diagrams/turn-flow.md) · [🇷🇺](diagrams/turn-flow.ru.md) — one
  `OrchestratorLoop.run_turn`: DATA-wrap → propose → `validate_action` → OOB gate → bounded dispatch.
- [diagrams/memory-trust-flow.md](diagrams/memory-trust-flow.md) · [🇷🇺](diagrams/memory-trust-flow.ru.md) —
  the HMAC record lifecycle: principal isolation → status gate → sign → verify/drop → supersede.
- [diagrams/README.md](diagrams/README.md) · [🇷🇺](diagrams/README.ru.md) — the diagrams
  sub-index.

## Related (outside this bundle)

- `../README.md` — the kernel's own overview and "what is NOT here".
- The downstream [araratsec-agent](https://github.com/RamilRamil/araratsec-agent) repo —
  the first `CapabilityPack` that rides on this kernel, with its own OKF `docs/` bundle.
