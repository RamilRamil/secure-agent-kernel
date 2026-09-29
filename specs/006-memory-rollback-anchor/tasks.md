---
description: "Task list — Rollback detection for the episodic store (kernel feature 006)"
---

# Tasks: Rollback detection for the episodic store

**Input**: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/rollback-anchor.md](contracts/rollback-anchor.md),
[quickstart.md](quickstart.md)

**Repository**: secure-agent-kernel (Repo A, `sr_agent`).

**Tests**: INCLUDED and **mandatory** — this is security-critical behaviour, so every
guarantee is written as a FAILING test before the code that satisfies it (Constitution:
Development Workflow & Quality Gates; Security Requirements).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: different files, no dependency on an incomplete task → parallelizable.
- **[Story]**: US1 / US2 / US3 (traceability to spec user stories). Setup / Foundational /
  Polish carry no story label.

> **Sequencing refinement over plan.md (recorded, not silent).** The plan put the monotonic
> anchor *bump* in the US2 phase (Phase C). It is moved here into **Foundational**: both US1
> (detect at `snapshot`) and US2 (detect at `write`) need the anchor to already be established
> and maintained on writes, or neither story is independently testable — the read check has
> nothing to compare against until a write has recorded a watermark. Same code, grouped so each
> user story is a standalone increment.

---

## Phase 1: Setup

- [X] T001 Capture the before-baseline: run `.venv/bin/python -m pytest tests/security tests/unit -q` green and record protected ASR / 004 composition results — the "before" half of SC-004. No code yet. (SC-004)
- [X] T002 [P] Add `anchor_root: Path | None` to `KernelConfig` and read `SR_ANCHOR_ROOT` (default unset → `None`) in `sr_agent/config.py`; document the var in `.env.example`. (D006-1, FR-001)

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: the anchor primitive and its monotonic maintenance must exist before either
detection story can be written or tested.

- [X] T003 Add `MemoryRollbackDetected(MemoryWriteError)` in `sr_agent/memory/episodic.py` with the message shape from [contracts/rollback-anchor.md](contracts/rollback-anchor.md). (contract)
- [X] T004 [P] Write FAILING unit tests for the anchor primitive in `tests/unit/test_rollback_rule.py`: a signed watermark round-trips through write→read; a forged/tampered anchor file reads as `None`; an absent anchor reads as `None`; constructing `EpisodicMemory` with an `anchor_root` inside `memory_root` raises. (D006-1, D006-2, FR-004)
- [X] T005 Implement the anchor primitive in `sr_agent/memory/episodic.py`: `anchor_root` param on `__init__` with the inside-`memory_root` guard; `_anchor_path` (per-project `<anchor_root>/<project_id>.rollback.json`), `_read_anchor` (verify HMAC → `int | None`), `_write_anchor` (atomic tmp-swap, signed over `{project_id, watermark}`) — structurally mirroring `_read_head`/`_write_head`. Makes T004 pass. (D006-1, D006-2, FR-001, FR-004)
- [X] T006 [P] Write a FAILING test in `tests/unit/test_rollback_rule.py`: after N durable writes under a held lease with an `anchor_root`, the anchor file exists and its watermark equals the project `log_max`; after a further write it advances (monotonic, never decreases). (FR-002, D006-5)
- [X] T007 Implement `_bump_anchor(project_id, new_max)` and call it in `write()` **after** `_write_head`, before `_emit_write`, so the anchor is the last durable artifact of a write. Makes T006 pass. (FR-002, D006-5)

**Checkpoint**: the anchor is established and monotonically maintained on every write; the
primitive is tested. Detection stories can now begin.

---

## Phase 3: User Story 1 — rolled-back store refused at the read seam (Priority: P1) 🎯 MVP

**Goal**: `snapshot()` fails closed when the store has been restored to an older whole copy.

**Independent Test**: build a store under a lease (writes populate the anchor), copy
`memory/<project>/` aside, advance the log, restore the older copy while `anchor_root` is left
untouched → `snapshot()` raises `MemoryRollbackDetected`; a healthy store still snapshots.

- [X] T008 [P] [US1] Write the FAILING rollback harness test in `tests/security/test_memory_rollback.py`: `anchor_root` in a separate tmp subtree; advance the log (incl. a `human_input` supersede); `copytree` the project dir aside; advance more; `rmtree`+`copytree` the older dir back over `memory/<project>/` (records+head+lease restored together); assert `snapshot(project_id=…, session_id=…)` raises `MemoryRollbackDetected`. (US1-AS1, D006-9, SC-001)
- [X] T009 [P] [US1] Add a control test in `tests/security/test_memory_rollback.py`: a store that has only grown (no rollback) snapshots normally and returns the expected items. (SC-002 anchor case)
- [X] T010 [US1] Implement `_check_rollback(project_id, log_max)` in `sr_agent/memory/episodic.py` — the ∅ / `≤` / `>` rule (absent→proceed, `V≤L`→proceed, `V>L`→raise `MemoryRollbackDetected`). (FR-003, D006-3)
- [X] T011 [US1] Wire `_check_rollback` into `snapshot()` right after `_authenticated_project` and the watermark pin (use its `current_max` as `log_max`). Makes T008 pass, keeps T009 green. (FR-007)

**Checkpoint**: US1 fully functional — the pack read seam refuses a rolled-back projection.

---

## Phase 4: User Story 2 — write refused onto a rolled-back log (Priority: P1)

**Goal**: a durable write onto a rolled-back log is refused before anything is appended.

**Independent Test**: roll a store back as in US1, then `write()` → raises
`MemoryRollbackDetected` and no record is appended.

- [X] T012 [P] [US2] Write the FAILING test in `tests/security/test_memory_rollback.py`: after a full-directory rollback, `write()` raises `MemoryRollbackDetected` and the target file length is unchanged (nothing appended, no head/anchor bump). (US2-AS1, SC-001)
- [X] T013 [US2] Wire `_check_rollback` into `write()` — after `_recover_torn_tails` and the authenticated-log read that yields `log_max`, before allocating the sequence / appending. Makes T012 pass. (FR-007)

**Checkpoint**: US1 + US2 both hold — rollback is refused on read and on write.

---

## Phase 5: User Story 3 — legitimate operation never mistaken for a rollback (Priority: P2)

**Goal**: no false rollback across growth, crash-lag, pre-006 compat, forged anchors, and
multiple projects; plus the operator report and the no-model-exposure guarantee.

**Independent Test**: exercise each non-attack case and assert the correct outcome.

- [X] T014 [P] [US3] FAILING tests in `tests/unit/test_rollback_rule.py`: 50 sequential writes + snapshots never raise; a crash-lag case (anchor left at `L-1` by hand) reads without raising and the next write catches the anchor up to `L`. (SC-002, D006-5)
- [X] T015 [P] [US3] FAILING tests in `tests/unit/test_rollback_compat.py`: a pre-006 store (signed head, no anchor file) loads and snapshots without raising; its first post-006 durable write establishes the anchor at the current `log_max`; the anchor file then exists. (FR-006, D006-8, SC-003)
- [X] T016 [P] [US3] FAILING test in `tests/security/test_memory_rollback.py`: a forged anchor (huge watermark, bogus HMAC) is treated as absent — `snapshot()`/`write()` proceed, no permanent lockout of the legitimate session. (FR-004, SC-005)
- [X] T017 [P] [US3] FAILING test in `tests/security/test_memory_rollback.py`: a rollback (or a corrupt anchor) for project A does not fail-close reads/writes on project B. (FR-008)
- [X] T018 [US3] Confirm T014–T017 pass against the T005/T010 implementation (absent→proceed, adopt-on-write, per-project files already give this); add only the minimal code needed if a case fails (e.g. ensure no code path fail-closes on `V=∅`). (FR-004, FR-006, FR-008, D006-3)
- [X] T019 [P] [US3] FAILING test in `tests/unit/test_rollback_cache.py`: under a held lease, the verified watermark is carried in `_ProjectView`; repeated `snapshot`/read reads the anchor file at most once per view; a write updates the cached watermark. (D006-4)
- [X] T020 [US3] Implement `_ProjectView.anchor: int | None` — populate it when the view is built in `_authenticated_project`, maintain it in `_extend_cache`, and make `_check_rollback` prefer the cached value while the lease is held. Makes T019 pass. (D006-4)
- [X] T021 [P] [US3] FAILING test in `tests/security/test_memory_rollback.py`: `verify_integrity()` reports a rollback in `report.rollbacks` (and `report.has_rollback`), distinct from `report.chain_breaks`; the signal is logged on the operator channel (caplog WARNING), not returned in any model-facing structure. (FR-009, D006-6)
- [X] T022 [US3] Implement `IntegrityReport.rollbacks: dict[str,str]` + `has_rollback` property, populate it in `verify_integrity()` (`V>L`), and add `_report_rollback` (WARNING, operator channel) separate from `_report_chain_break`. Makes T021 pass. (FR-009, D006-6)
- [X] T023 [P] [US3] FAILING+then-green test in `tests/security/test_memory_rollback.py`: the watermark/signature never appear in `snapshot()` items nor in `for_llm_context()` output. (FR-011, D006-7)

**Checkpoint**: all three stories independently functional; no false positives; operator-visible.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T024 Run the MI harness + `tests/security` + full suite green; confirm protected ASR is unchanged vs the T001 before-baseline (this feature lowers no guardrail). Also verify FR-010 by inspection: the anchor path adds no network/socket/TSA/TPM/hardware-counter import or call — it is a local key-signed file only. (SC-004, FR-010)
- [X] T025 [P] Document the anchor + rollback guard in `docs/kernel.md` and `docs/kernel.ru.md`.
- [X] T026 [P] Update `docs/mi-threat-model.md` and `docs/mi-threat-model.ru.md`: move the store-rollback case from 004's "out of scope" to "neutralised by the rollback anchor", noting the residual out-of-scope key-store adversary.
- [X] T027 [P] Add the pointer in `specs/004-memory-composition-integrity/spec.md` "Out of scope" that feature 006 closes the rollback item. (docs hygiene)
- [X] T028 Run [quickstart.md](quickstart.md) end-to-end (`tests/security/test_memory_rollback.py -v`) and confirm all seven scenarios behave as written.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: after Setup. **Blocks US1 and US2** — they have nothing to
  compare against until the anchor is established and bumped (T005, T007).
- **US1 (Phase 3)** and **US2 (Phase 4)**: after Foundational. Both add a call to the shared
  `_check_rollback` (T010) — T010 lands in US1; US2 (T013) reuses it, so US2 depends on T010.
- **US3 (Phase 5)**: after Foundational; its cache task (T020) and report task (T022) are
  independent of US1/US2 wiring but assume the primitive.
- **Polish (Phase 6)**: after all desired stories.

### Within a story

- The FAILING test is written and confirmed red before its implementation task.
- `_check_rollback` (T010) before both call-site wirings (T011, T013).
- The anchor primitive (T005) and bump (T007) before every read/write check.

### Parallel opportunities

- T002 ∥ (the config file is independent of episodic.py work).
- Test-authoring tasks marked [P] touch distinct test files and can be written together:
  T008/T009 (security), T014 (unit rule), T015 (unit compat), T016/T017 (security),
  T019 (unit cache), T021/T023 (security).
- Docs tasks T025/T026/T027 are [P].

---

## Implementation Strategy

- **MVP = US1**: Setup → Foundational → US1. At the checkpoint the pack read seam already
  refuses a rolled-back store — the core value — and can be validated on its own (T008).
- **Then US2**: closes the write path so a rollback cannot be appended onto (protects 003
  exactly-once).
- **Then US3**: removes false positives, guarantees compat and no-DoS, and makes the signal
  operator-visible — the phase that makes the guard safe to arm by default.
- Commit per task or per green checkpoint (commits only on explicit request).

## Notes

- The spec's FR-005 was reconciled with FR-006 per D006-3 (missing anchor → not-yet-anchored,
  not fail-closed) with operator sign-off before these tasks; the tests encode the reconciled
  rule (T015, T016, T018).
- Downstream (Repo B) wiring — `SR_ANCHOR_ROOT` placement and `MemoryRollbackDetected`
  handling — is out of this repo's tasks; the contract ships with the commit pin.
