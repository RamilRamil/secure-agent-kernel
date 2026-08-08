---
type: Reference
title: Kernel diagrams index
description: Index of secure-agent-kernel architecture and flow diagrams; bilingual EN/RU convention.
tags: [index, diagrams, kernel]
lang: en
status: stable
generated:
  by: secure-agent-kernel/claude-opus-4.8
  at: 2026-08-08T00:00:00Z
---

# Diagrams

Architecture and flow diagrams for the **secure-agent-kernel**, reflecting **what is
actually wired up today**. Mermaid source, renders in GitHub/VS Code/most markdown
viewers.

**Bilingual docs.** Each doc has an English base file (`name.md`) and a Russian sibling
(`name.ru.md`); they cross-link at the top. Keep the two in sync when the wiring changes.

- [kernel-architecture.md](kernel-architecture.md) · [🇷🇺](kernel-architecture.ru.md) —
  the **two-plane split**: the deterministic orchestration plane (where every guarantee
  lives), the probabilistic LLM context plane, the `sr_agent` module map, and the
  `CapabilityPack` boundary. Marks the downstream composition root / pack as external and
  flags the task-agnosticism naming residue.
- [turn-flow.md](turn-flow.md) · [🇷🇺](turn-flow.ru.md) — one `OrchestratorLoop.run_turn`
  step-by-step: DATA-wrap → model proposes → `validate_action` → OOB gate on `write_execute`
  → bounded read-only dispatch.
- [memory-trust-flow.md](memory-trust-flow.md) · [🇷🇺](memory-trust-flow.ru.md) — the memory
  record lifecycle: principal isolation → status gate → HMAC sign → append-only → load →
  verify / silent-drop-on-fail → `supersede` (human_input-only).

Update these when the wiring changes — a diagram that lies about what's connected is
worse than no diagram.
