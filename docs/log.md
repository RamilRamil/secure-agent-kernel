# Changelog

Chronological history for the `docs/` OKF bundle, newest first (OKF v0.2 reserved `log.md`).

## 2026-09-11

- **Feature 006 — rollback anchor.** Documented the whole-directory rollback guard: a
  per-project HMAC-signed watermark held outside `memory_root` (`SR_ANCHOR_ROOT`) that
  fails `snapshot`/`write` closed when the log is shorter than a point it provably once
  passed. Updated [kernel.md](kernel.md) (mechanism 3) and marked the rollback item in
  `specs/004-memory-composition-integrity/spec.md` "Out of scope" as closed. The guarantee
  depends on the operator's `anchor_root` vs `memory_root` access separation; the residual
  adversary who controls the anchor location stays out of scope.

## 2026-08-22

- **Feature 003 — dispatch / result / resume.** Documented the shared
  `KernelActionExecutor`, the trusted prompt registry, and the snapshot
  capacity envelope (10000 items / 32 MiB). Over-capacity is fail-closed;
  the operator completes the session and starts a new one (FR-009b). See
  [kernel.md](kernel.md).

## 2026-08-08

- **Completed the parity set** — added the five deferred concepts (EN + RU): the
  [MI threat model](mi-threat-model.md), the [CapabilityPack interface](capability-pack-interface.md),
  the [MI eval](mi-eval.md), and the two flow diagrams [turn-flow](diagrams/turn-flow.md)
  and [memory-trust-flow](diagrams/memory-trust-flow.md). All grounded in real kernel code
  (`memory/episodic.py`, `memory/hmac.py`, `orchestrator/loop.py`/`action.py`/`confirmation.py`,
  `guardrails/sanitize.py`, and the `tests/security` / `tests/architecture` suites). Promoted
  them from "Planned" in [index.md](index.md) and the diagrams sub-index; the kernel bundle
  now mirrors the araratsec bundle.
- **Adopted OKF v0.2** for the kernel `docs/` bundle: YAML frontmatter on every concept,
  a bilingual EN/RU convention (`name.md` / `name.ru.md`), a bundle root
  [index.md](index.md) declaring `okf_version: "0.2"`, and this `log.md`. Provenance seeded
  via `generated.by`/`at` (owner for the pre-existing `kernel.md` prose, agent for
  session-drafted/translated content). No `verified` entries yet.
- **Reworked** [kernel.md](kernel.md) and added its Russian sibling
  [kernel.ru.md](kernel.ru.md). Fixed layout drift carried over from the pre-split
  monorepo: the kernel ships **no** composition root (no `cli.py`, no `[project.scripts]`)
  — the CLI and frontend live downstream in araratsec-agent; `relay` is
  `orchestrator/relay.py` (not `llm_core/`); `memory/` includes `lessons`; the
  `orchestrator/` map drops the non-existent `checkpoint`. Replaced the dead
  `audit-agent.md` / `roadmap.md` / `../frontend/` links with the external araratsec-agent
  repo URL. Added an honest "task-agnostic at the guarantee level" note: `PackContext`
  (`audit_root`/`poc_dir`/`poc_generator`) and `loop.py` (`AuditResult`) still carry
  audit vocabulary from the kernel↔pack split (feature 048), without weakening any
  invariant.
- **Added** [diagrams/kernel-architecture.md](diagrams/kernel-architecture.md) (EN + RU) —
  the two-plane split (deterministic orchestration plane vs probabilistic LLM context
  plane), the `sr_agent` module map, and the `CapabilityPack` boundary, with the downstream
  composition root / pack drawn as external.
- **Added** [diagrams/README.md](diagrams/README.md) (EN + RU) — the diagrams sub-index,
  listing `kernel-architecture` and the planned `turn-flow` / `memory-trust-flow`.
