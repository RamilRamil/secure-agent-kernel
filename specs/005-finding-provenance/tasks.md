---
description: "Task list — provenance for model-reported findings (kernel 005)"
---

# Tasks: provenance for model-reported findings

**Input**: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md)
(D005-1…D005-8), [data-model.md](data-model.md),
[contracts/finding-provenance.md](contracts/finding-provenance.md),
[quickstart.md](quickstart.md)

**Repository**: secure-agent-kernel (Repo A, `sr_agent`). Cross-repo references are written
`kernel/NNN` / `pack/NNN` — the two spaces already collide.

**Tests**: INCLUDED and **mandatory**, test-first. FR-003, FR-005, FR-012 and FR-015 are all
security-critical. Every test task below is authored to **FAIL first**.

**Baseline**: kernel/002 merged (`a33ee04`). Suite at **450 passed, 4 skipped, 2 xfailed**.
Every checkpoint must leave the whole suite green, including the untouched kernel/003 and
kernel/004 tests — they are this feature's regression gate, and `test_memory_composition.py`
in particular must stay green because D005-4 refuses to touch `supersedes`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelisable (different files, no ordering dependency).
- **[Story]**: `[INFRA]` for the record shape everything sits on; US1 the stamp, US2 the
  pause pair, US3 the snapshot exposure.

> **The one thing this feature must not get wrong.** There are two loop entry points and
> four exits each. The old code got the ordering wrong in one direction at every one of
> them. Two independent devices guard it, and both are tasks: the **required provenance
> argument** (T011) makes an early call impossible to write without inventing an outcome,
> and the **single scenario table run against both entry points** (T014) makes a path that
> stops covering a scenario fail rather than drift. Neither alone is enough — the signature
> would still allow a wrong-but-well-typed value, and the table alone would pass against a
> refactor that reintroduced a second, untabled call site.

> **Two hazards found before implementation.** They are tasks, not surprises:
> 1. adding fields to `MemoryRecord` changes `fields_for_hmac()` for **every** record and
>    would blank every existing store (T003 exists to fail on exactly that);
> 2. giving findings a `payload_kind` would silently drop them out of `SNAPSHOT_KINDS`
>    (T026 exists to fail on exactly that).

---

## Phase 1 — Setup

- [x] T001 Read the current shapes before touching them: `MemoryRecord` /
  `fields_for_hmac` / `for_llm_context` in [`sr_agent/models/memory.py`](../../sr_agent/models/memory.py),
  `SnapshotItem` in [`sr_agent/models/dispatch.py`](../../sr_agent/models/dispatch.py),
  `_persist_finding` and both call sites in
  [`sr_agent/orchestrator/loop.py`](../../sr_agent/orchestrator/loop.py). Record the current
  line numbers in the PR description; every reference in these docs is a snapshot and will
  drift.
- [x] T002 Create a fixture store written with the **pre-005 code** and commit it under
  `tests/fixtures/pre_005_store/` — records signed under today's shape. T003 depends on it,
  and it cannot be regenerated after Phase 2 lands.

---

## Phase 2 — Foundational: the record shape (blocks everything)

**Goal**: the four fields exist, illegal combinations are refused, and no existing store
changes shape.

- [x] T003 [INFRA] Failing test first, in `tests/security/test_finding_provenance_integrity.py`:
  load the `tests/fixtures/pre_005_store/` fixture and assert every record still verifies and
  is returned by `load_for_principal`. This is the regression that catches the whole-store
  blanking described in FR-015 / D005-3. Write it before T005.
- [x] T004 [INFRA] Failing tests for the combination table in
  `tests/unit/test_finding_provenance_fields.py`: each legal row from
  [data-model.md](data-model.md) constructs, and each named illegal combination raises —
  `resolved` without an operation id; `unresolved` carrying an operation id or a status;
  `pending` whose status is not `pending`; `resolves_record_id` on a non-`resolved` record;
  any provenance field on a record with no `finding`.
- [x] T005 [INFRA] Add `action_resolution`, `action_operation_id`, `action_dispatch_status`
  and `resolves_record_id` to `MemoryRecord` in
  [`sr_agent/models/memory.py`](../../sr_agent/models/memory.py), all optional, all defaulting
  to `None`. FR-003, D005-2.
- [x] T006 [INFRA] Add the `model_validator` enforcing the legal combinations. Refuse at
  write time; never store a contradiction. FR-012.
- [x] T007 [INFRA] Make `fields_for_hmac()` omit each of the four when unset, so a record
  carrying none of them signs exactly as it does today. FR-015, D005-3. T003 goes green here.
- [x] T008 [INFRA] Failing test in `tests/unit/test_finding_provenance_fields.py`:
  `for_llm_context()` strips all four provenance fields, alongside `hmac`, `seq`,
  `chain_prev` and `log_sequence`. FR-010.
- [x] T009 [INFRA] Add the four to the `for_llm_context()` strip set in
  [`sr_agent/models/memory.py`](../../sr_agent/models/memory.py). They are not signature
  material, but neither is `log_sequence`, which is stripped because a turn that can see its
  own bookkeeping can argue about it — and these fields tell the model exactly which state
  makes a finding proof-eligible downstream. The pack reads them off `SnapshotItem`
  (FR-016), never out of model context.
- [x] T010 [INFRA] Security test in `tests/security/test_finding_provenance_integrity.py`:
  a stamped record is tamper-evident in both directions — altering a provenance value and
  removing one both break the signature — while a record carrying none of them signs exactly
  as it did before this feature. Assert the property, not just the mechanism: there is no
  path from a hypothesis to `resolved` + `ran` without the key, and the removal is still
  visible against the signed chain head (D005-3).

**Checkpoint**: shape and integrity pinned. Nothing writes the fields yet; suite green.

---

## Phase 3 — US1: every finding write carries its outcome (P1)

**Goal**: scenarios 1–5 and 7. A finding is written after the action proposed in the same
`AgentAction` resolves, stamped with what happened.

**Independent test**: run a turn that ends each of the five ways and assert the stamp.

- [x] T011 [US1] Give `_persist_finding` a **required** provenance argument in
  [`sr_agent/orchestrator/loop.py`](../../sr_agent/orchestrator/loop.py). This is a design
  device, not a refactor: an early call site has no outcome to pass, so the ordering bug
  cannot be reintroduced by moving one line back up. D005-1.
- [x] T012 [P] [US1] Failing test in `tests/unit/test_finding_provenance_paths.py` for
  scenario 1 (dispatch returned `ran`): `resolved`, operation id present, status `ran`.
- [x] T013 [P] [US1] Failing tests for scenarios 2–5 in the same file: non-`ran` terminal
  statuses stay `resolved` with that status; rejected / unknown `next_action` / terminal
  without a tool are `unresolved` with **no** operation id and **no** status.
- [x] T014 [US1] Parametrise the whole scenario table over **both** entry points — `run` and
  `run_turn` — from one table, in `tests/unit/test_finding_provenance_paths.py`. FR-006.
  One table, two runners: a path that stops covering a case fails rather than drifting.
- [x] T015 [US1] Move the call sites in `run` below the point where the outcome is known:
  terminal branch, unknown-action branch, rejected branch, and after `executor.execute`.
  FR-001, FR-002.
- [x] T016 [US1] The same in `run_turn`. Same five outcomes, same helper, same argument.
- [x] T017 [US1] Regression test for FR-007 in `tests/unit/test_finding_provenance_paths.py`:
  a pack whose `persist_finding` returns `None` still produces **no** record — no partial,
  no near-finding. Unchanged behaviour, pinned because the call sites moved.
- [x] T018 [US1] Architecture test in `tests/architecture/test_finding_persist_ordering.py`:
  AST over `loop.py` asserting that in **both** `run` and `run_turn` no `_persist_finding`
  call appears before the `validate_action` call, and that every call passes a provenance
  argument. FR-006, D005-1.

- [x] T019 [US1] Architecture test in the same file: the kernel reads the domain `Finding`
  no deeper than `.location`. Assert the attribute set `loop.py` touches on the pack-built
  finding object, so a future change that reaches for `.severity` or `.finding_id` inside
  the kernel fails here rather than in review. FR-011, Principle III.

**Checkpoint**: US1 shippable on its own — findings are distinguishable for every
non-paused turn.

---

## Phase 4 — US2: the pause pair (P2)

**Goal**: scenario 6. A paused finding is recorded as `pending` and gains a resolution record
on resume, with the paused record surviving.

- [x] T020 [US2] Failing test in `tests/unit/test_finding_provenance_paths.py`: a turn that
  pauses writes `pending` with the operation id it paused with and
  `action_dispatch_status == "pending"`.
- [x] T021 [US2] Failing test for the resume half: after `resume_turn`, a second record
  exists with `resolved`, the same operation id, a terminal status, and `resolves_record_id`
  naming the paused record — **and the paused record is still returned by
  `load_for_principal`**. The survival assertion is the point: under `supersedes` it would
  not be, and a `write_poc` holding that id would be left with a dangling reference (D005-4).
- [x] T022 [US2] Implement the resume-side write in
  [`sr_agent/orchestrator/loop.py`](../../sr_agent/orchestrator/loop.py): after the resumed
  dispatch resolves, find this session's finding records with `action_resolution == "pending"`
  and a matching `action_operation_id` through the normal verified read path, and write one
  resolution record per match. No `supersedes`, no tier change. FR-014, D005-6.
- [x] T023 [US2] Test in `tests/unit/test_memory_write_event.py` that the kernel/002
  `memory_write` event still fires for a finding write, and that the pause pair produces
  **two** events, not one. `payload_kind` on both is empty, which is the value consumers were
  already required to tolerate — the point of the test is that the moved call site did not
  drop an event. FR-009.
- [x] T024 [US2] Security test in `tests/security/test_finding_provenance_integrity.py`: a
  forged `pending` finding record (unsigned, or signed with the wrong key) does **not**
  attract a resolution record on resume — it is not there to match, because the lookup goes
  through the verified read path.
- [x] T025 [US2] Regression: `tests/security/test_memory_composition.py` unchanged and green.
  `_enforce_status_rules` is **not** relaxed and no finding is ever written at `human_input`.
  D005-4.

**Checkpoint**: the pause pair works and the paused `record_id` is stable.

---

## Phase 5 — US3: the pack can see it (P1 for the consumer)

**Goal**: FR-016/FR-017. Without this the feature delivers nothing to Repo B — `PackContext`
has no memory handle and `MemorySnapshot` is the only read seam.

- [x] T026 [US3] Failing test in `tests/unit/test_finding_provenance_snapshot.py`: a finding
  record still has `kind == "finding"` in the snapshot and is still inside `SNAPSHOT_KINDS`.
  This is the guard on the second hazard — `_snapshot_kind` reads `payload_kind` first, so
  giving findings a kind would drop them out of the projection silently. FR-013.
- [x] T027 [US3] Failing test in the same file: the four fields are readable off
  `SnapshotItem`, and `item.operation_id` is `None` on a finding while
  `item.action_operation_id` may be set. Assert both, so the naming hazard in D005-5 is
  pinned by a test rather than by a paragraph.
- [x] T028 [P] [US3] Add the four fields to `SnapshotItem` in
  [`sr_agent/models/dispatch.py`](../../sr_agent/models/dispatch.py). It is `extra="forbid"`
  and `frozen=True`, so this is a real model change. FR-016.
- [x] T029 [P] [US3] Populate them from the record envelope in `EpisodicMemory.snapshot`
  ([`sr_agent/memory/episodic.py`](../../sr_agent/memory/episodic.py)) — off the envelope,
  never out of `body`. FR-016.
- [x] T030 [US3] Test that the provenance fields are absent from `finding` in the snapshot
  body and from `record.finding` on disk. FR-017.
- [x] T031 [US3] Capacity note as a test, not prose: a session with N paused findings
  produces 2N snapshot items, and the fail-closed envelope still raises rather than
  truncating. Patch `MAX_SNAPSHOT_ITEMS` down for the test rather than writing ten thousand
  records — the assertion is that the pair counts double and that the cap refuses, not that
  the machine can reach the real cap. D005-8.

**Checkpoint**: the consumer can filter proof-eligible records. Feature is complete for
Repo B.

---

## Phase 6 — Contract, docs, gates

- [x] T032 Walk [quickstart.md](quickstart.md) end to end against the implementation and fix
  whatever does not run. All seven scenarios, plus the pre-005 store section.
- [x] T033 [P] Finalise [contracts/finding-provenance.md](contracts/finding-provenance.md)
  against the shipped field names. This is the deliverable Repo B is waiting on and it ships
  **with the pin**, not before.
- [x] T034 [P] Document the fields in [`docs/kernel.md`](../../docs/kernel.md) and mirror in
  [`docs/kernel.ru.md`](../../docs/kernel.ru.md): what the kernel claims, what it explicitly
  does not, and why there is no `grounded` field. FR-004 is the part a future reader will be
  tempted to "complete".
- [x] T035 [P] Update [`CLAUDE.md`](../../CLAUDE.md): active feature, and a warning not to
  add findings a `payload_kind` and not to relax `_enforce_status_rules` to let the kernel
  supersede.
- [x] T036 Full suite green. Then `tests/security/ tests/architecture/` specifically.
- [x] T037 MI harness at its existing bar — protected ASR unchanged, no security test deleted
  or weakened. Report the field-harness status honestly: it is skipped without `FIELD_ASR=1`
  and `OPENROUTER_API_KEY`, and a skipped harness is not a passed one.

---

## Dependencies & Execution Order

```
Phase 1 (T001-T002)
   └─> Phase 2 INFRA (T003-T010)          ← blocks everything; T002 must precede T003
         ├─> Phase 3 US1 (T011-T019)
         │      └─> Phase 4 US2 (T020-T025)   ← needs the stamp before it can pair records
         └─> Phase 5 US3 (T026-T031)          ← independent of US1/US2 once the fields exist
                └─> Phase 6 (T032-T037)
```

**Parallel opportunities**: Phase 5 can run concurrently with Phase 3 — disjoint files
(`dispatch.py` + `episodic.py` versus `loop.py`). Within Phase 3, T012 and T013 are
parallel. Within Phase 6, T033/T034/T035 are parallel.

**MVP**: Phase 2 + Phase 3 + Phase 5. That is the smallest set that is useful to Repo B —
the stamp exists and the pack can read it. Phase 4 (the pause pair) is what pack/005's US1
needs and cannot be dropped for the pin, but it is not needed for the feature to be coherent.

## Independent test criteria

| Story | Green means |
|---|---|
| INFRA | pre-005 store verifies; illegal combinations refused; a stamped record is tamper-evident both ways |
| US1 | all five non-paused turn shapes stamped correctly, in both entry points, from one table |
| US2 | paused finding is `pending`, gains a resolution record on resume, keeps its `record_id` |
| US3 | findings still in `SNAPSHOT_KINDS`; four fields readable off `SnapshotItem` |
