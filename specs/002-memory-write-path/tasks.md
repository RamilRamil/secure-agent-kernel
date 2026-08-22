---
description: "Task list — kernel-owned `write_memory` path + memory-write observability (kernel 002)"
---

# Tasks: kernel-owned `write_memory` path + memory-write observability

**Input**: [spec.md](spec.md) (D7), [plan.md](plan.md), [research.md](research.md)
(D8–D13), [data-model.md](data-model.md),
[contracts/memory-write-event.md](contracts/memory-write-event.md),
[quickstart.md](quickstart.md)

**Repository**: secure-agent-kernel (Repo A, `sr_agent`).

**Tests**: INCLUDED and **mandatory**. The constitution requires test-first for
security-critical behaviour; FR-002, FR-004, FR-005, FR-006 and FR-009 all are.
Every test task is authored to **FAIL first**, before the implementation it
describes.

**Baseline**: `003` is merged (`3675bac`). Suite at **300 passed, 4 skipped,
2 xfailed**. Every checkpoint below must leave the whole suite green — including
the untouched `003` and `004` tests, which are this feature's regression gate.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no ordering dependency).
- **[Story]**: US1–US3 for traceability to spec user stories; `[INFRA]` for the
  shared validator every story sits on.
- Exact file paths and FR/SC/D ids are in each task.

> **US1 and US2 are genuinely independent**, unlike `003`'s facets of one
> contract. Phase 3 (the interception) and Phase 4 (the event) touch disjoint
> files and can be worked concurrently once Phase 2 is green. Either alone is a
> shippable increment: US1 without US2 gives kernel-owned writes that are quiet;
> US2 without US1 gives observability over the writers that exist today. **MVP =
> Phase 2 + Phase 3** (US1), because FR-001 is the contract fix that makes the
> constitution's Principle III exception honest.

> **Two known collisions, both discovered before implementation.** They are
> tasks, not surprises:
>
> 1. **T004 breaks an existing test.**
>    [`test_open_taxonomy_sc005.py:48`](../../tests/unit/test_open_taxonomy_sc005.py:48)
>    asserts `Action(action_type="write_memory", params={})` **resolves**. Once
>    the validator requires a note, empty params are rejected. That test is
>    asserting *taxonomy resolution*, not param policy, so it is updated to keep
>    proving what it was written to prove (T007) — not deleted, not weakened.
> 2. **The MI oracle classifies `write_memory` attempts.**
>    [`field_mi_asr/oracle.py:167`](../../tests/security/field_mi_asr/oracle.py:167)
>    counts privileged hints in `write_memory` params as a `dangerous_attempt`.
>    The validator now rejects those before dispatch. That should *lower* ASR,
>    which is the right direction — but "should" is not a measurement, and an
>    oracle that stops seeing attempts it used to count can report a better
>    number for the wrong reason. T027 re-measures and compares against the
>    recorded baseline rather than assuming.

---

## Phase 1: Setup & before-baseline

- [x] T001 [INFRA] Dev-install editable (`.venv/bin/pip install -e ".[dev]"`),
  export `SR_SECRET_KEY` (64 hex chars), run `.venv/bin/python -m pytest tests/ -q`
  and record the baseline verbatim in the PR description — including the current
  protected MI ASR from `tests/security/field_mi_asr/`. T027 compares against
  this number, so it must be captured *before* any change.

---

## Phase 2: Foundational — the `write_memory` validator (blocking)

**⚠️ Blocks US1.** The interception in Phase 3 assumes params have already been
policed. Landing the interception first would put a fail-open window in the
middle of the feature.

Independently valuable on its own: even with no interception, a forged-provenance
`write_memory` stops reaching `pack.dispatch`.

### Tests (write FIRST, must FAIL)

- [x] T002 [P] [INFRA] `tests/unit/test_write_memory_validation.py`: each
  forbidden params key is **rejected, not stripped** — `source_type`, `hmac`,
  `supersedes`, `status_change`, `status`, `seq`, `chain_prev`, `log_sequence`,
  `record_id`, `project_id`, `session_id`, `tool`. Assert one key at a time
  (a loop over the set), so a partial implementation cannot pass by catching only
  the first. Follows `003`'s H5 precedent —
  `test_H5_extra_identity_fields_are_refused_not_ignored`. (FR-004, D12.2)
- [x] T003 [P] [INFRA] Note validation in the same file: missing `note`, `note=""`,
  whitespace-only, a non-string `note`, and a note over `MAX_PAYLOAD_BODY_BYTES`
  (8192, `sr_agent/models/dispatch.py`) each reject. An empty note MUST NOT be a
  silent no-op that reads as success. (FR-004; spec Edge Cases)
- [x] T004 [P] [INFRA] Target bounds in the same file (D11): `target` absent →
  accepted, falls back to the action id; empty or whitespace-only → reject; NUL or
  control characters → reject; over 200 bytes UTF-8 → reject. And the containment
  assertion: `target="../../etc/passwd"` is **accepted** and
  `EpisodicMemory._target_stem` renders it as the contained filename
  `.._.._etc_passwd.jsonl` inside the project directory — the target is not a
  path and is not a containment boundary. (D11)

### Implementation

- [x] T005 [INFRA] Add `_validate_write_memory(action, scope_root)` to
  `sr_agent/orchestrator/action.py` implementing T002–T004, and point
  `KERNEL_GENERIC_ACTIONS["write_memory"]` at it in place of `_noop_validate`
  ([action.py:50](../../sr_agent/orchestrator/action.py:50)). `ActionSpec`'s
  class and confirmation flag are **unchanged** — `ActionClass.memory`, no OOB
  (FR-005). Reuse `MAX_PAYLOAD_BODY_BYTES`; do not mint a second limit.
- [x] T006 [INFRA] Extract the note and the resolved target into a small helper the
  executor will consume in T012, so validation and construction cannot drift into
  two different readings of the same params.
- [x] T007 [INFRA] Update
  [`tests/unit/test_open_taxonomy_sc005.py:48`](../../tests/unit/test_open_taxonomy_sc005.py:48)
  to resolve `write_memory` with a **valid** note payload instead of `params={}`.
  The test's subject is taxonomy resolution from `KERNEL_GENERIC_ACTIONS`; it must
  still prove exactly that. Add a sibling assertion that `params={}` now rejects,
  so the change is visible as a policy addition rather than a quiet edit.

**Checkpoint**: `pytest tests/ -q` green. Phases 3 and 4 may now proceed in
parallel.

---

## Phase 3: US1 — model `write_memory` is durable and kernel-owned (P1) 🎯 MVP

**Goal**: the kernel executes `write_memory`; the pack is not invoked for it; the
record's provenance is kernel-authored.

**Independent test**: with a fixture pack whose `dispatch` raises if called, a
proposed `write_memory` produces a durable record at `llm_inference` and
`dispatch` was never entered.

### Tests (write FIRST, must FAIL)

- [x] T008 [P] [US1] `tests/unit/test_write_memory_path.py`: a validated
  `write_memory` produces exactly one durable record with
  `source_type=llm_inference`, `payload_kind="model_note"`,
  `payload == {"note": <the note>}`, `tool is None`, and `finding` /
  `checkpoint` / `status_change` all `None`. `pack.dispatch` is **not** called —
  use a fixture pack whose `dispatch` raises, so the assertion cannot pass by a
  recorded-call check that was never wired. (FR-001, FR-002, FR-003, SC-001)
- [x] T009 [P] [US1] Target routing in the same file (D11): with
  `params={"target": "Vault.sol", ...}` the record lands under `Vault.sol` and is
  returned by `memory.load(project_id, "Vault.sol")`; with no `target` it falls
  back to the action id. `project_id` comes from `session.principal` in both
  cases and is never readable from params.
- [x] T010 [P] [US1] Batch/chat parity in the same file: identical assertions
  driven through `OrchestratorLoop.run` and through `OrchestratorLoop.run_turn`, with the same
  fixture pack. Neither surface may reach `dispatch`. (FR-001, SC-004)
- [x] T011 [P] [US1] `tests/unit/test_write_memory_not_a_transition.py` (D13):
  `session_revision` is identical before and after a note;
  `find_committed_bundle` finds nothing for the note; no record with
  `payload_kind="dispatch_commit"` is created; and two identical notes produce
  **two** records, not one deduplicated record.
- [x] T012 [P] [US1] Failure modes never raise (D12.3), in
  `tests/unit/test_write_memory_path.py`: a `write_memory` issued without the
  writer lease, and one issued against a project whose composition chain is
  broken, each return `DispatchResult(status=error, ...)`. The turn continues:
  drive it through `run_turn` and assert the turn reaches a normal terminal
  status rather than propagating an exception, and that the refusal re-enters
  context DATA-wrapped (`[DATA START` present in the next tool body). A model must
  not be able to kill a turn by proposing a write the kernel then refuses.
- [x] T013 [P] [US1] Privileged `status_change` through this path is refused
  exactly as any other non-`human_input` privileged write is today — rejected at
  validation (T002) and, if construction were ever reached, by
  `EpisodicMemory._enforce_status_rules`. Assert no durable record exists at the
  attempted status. (FR-004, US1 scenario 3)

### Implementation

- [x] T014 [US1] Intercept in `KernelActionExecutor.execute`
  ([executor.py:115](../../sr_agent/orchestrator/executor.py:115)): after
  `validate_action` returns non-rejected and **before** `derive_ids`, branch on
  `action.action_type == "write_memory"`, build the record from the fixed shape in
  [data-model.md](data-model.md), write it, and return
  `DispatchResult(status=ran, body=...)`. Never reaches `derive_ids`,
  `find_committed_bundle`, `pack.dispatch`, or `commit_if_absent` (D8, D13).
  `loop.py` is **not** modified.
- [x] T015 [US1] Wrap the write in the failure contract from T012: catch
  `MemoryWriteError`, `MemoryChainError`, and `PrincipalMismatch` and return
  `DispatchResult(status=error, body=<reason>)`. Nothing on this path raises. Do
  **not** catch broadly — an unexpected exception type is a bug and must surface.

**Checkpoint**: `pytest tests/ -q` green, including
`tests/architecture/test_single_dispatch_path.py`, which fails if the
interception ever migrates into the loop. **US1 is independently shippable here.**

---

## Phase 4: US2 — every durable memory write is first-class observable (P1)

**Goal**: a `memory_write` event on every successful `EpisodicMemory.write`.

**Independent test**: with a sink attached, one model `write_memory` and one
kernel-authored finding persist each produce a `memory_write` event; a read-only
tool call produces none.

Independent of Phase 3 — different files, and the event is observable over the
writers that already exist.

### Tests (write FIRST, must FAIL)

- [x] T016 [P] [US2] `tests/unit/test_memory_write_event.py`: one successful
  `write` produces exactly one event whose keys are **exactly** the set in
  [contracts/memory-write-event.md](contracts/memory-write-event.md) —
  `type`, `record_id`, `project_id`, `target`, `session_id`, `source_type`,
  `payload_kind`, `log_sequence`. Assert set equality, not membership: an extra
  key is a contract break a membership check would miss. (FR-007, SC-003)
- [x] T017 [P] [US2] Exclusion assertions in the same file: `hmac`, `seq`,
  `chain_prev`, `payload`, `finding`, and `checkpoint` are absent. `chain_prev` is
  the previous record's signature; a channel that carries it leaks it. (D9.3)
- [x] T018 [P] [US2] Emission scope (D9.1) in the same file: a finding persist, a
  chat turn, a session snapshot, a `commit_if_absent` bundle, a
  `put_external_response_if_absent` ingest, and a `write_pause_checkpoint` each
  produce exactly one event, with the `payload_kind` the contract's table names.
  There is no exempt writer.
- [x] T019 [P] [US2] Negative case in the same file: a read-only tool call
  (`read_file`) produces **no** `memory_write` event. (SC-003)
- [x] T020 [P] [US2] FR-008 in the same file: with `event_sink=None` the write
  succeeds; with a sink that raises, the write succeeds, the returned record is
  byte-identical to the no-sink case, the store is identical, and no exception
  escapes `write()`. Observability is not a transaction.
- [x] T021 [P] [US2] Durability ordering in the same file: a sink that reads the
  target file from disk during the callback sees its own record already present.
  The event means *this is on disk*, so it cannot fire before the fsync. (D9.2)
- [x] T022 [P] [US2] Re-entrancy in the same file: a sink that itself calls
  `memory.write` does not recurse — the nested write emits nothing and terminates.
  The guard contains the bug; it does not license it.

### Implementation

- [x] T023 [US2] Add `event_sink: Callable[[dict], None] | None = None` to
  `EpisodicMemory.__init__` ([episodic.py:117](../../sr_agent/memory/episodic.py:117)),
  bound by the composition root exactly as `privileged_statuses` (D5) and `lease`
  (`003`) are. Default `None`; a memory built without one is silent, not broken.
- [x] T024 [US2] Emit in `write()` after `_append_durably`, `_write_head`, and
  `_extend_cache` — never before. Swallow every exception from the sink and log at
  DEBUG, mirroring `OrchestratorLoop._emit`
  ([loop.py:192](../../sr_agent/orchestrator/loop.py:192)). Add the re-entrancy
  guard from T022.

**Checkpoint**: `pytest tests/ -q` green. **US2 is independently shippable here.**

---

## Phase 5: US3 — pack boundary and Constitution II unchanged (P2)

**Goal**: prove this feature smuggles in neither a II expansion nor a pack memory
backdoor. Non-regression, so it is written as new assertions beside the existing
ones — nothing here relaxes a test to make the feature pass.

- [x] T025 [P] [US3] Extend `tests/security/test_hostile_pack.py`: a pack cannot
  author a `model_note`. It has no memory handle (`PackContext` field set
  unchanged — the existing `test_H2_packcontext_has_no_memory_handle` assertion
  must still hold verbatim) and it is not invoked for the id at all. (FR-006,
  SC-006)
- [x] T026 [P] [US3] `tests/security/test_write_memory_forgery.py`: no combination
  of params yields a record at `human_input`, or at any tier above
  `llm_inference`. Drive it as a matrix over the forbidden key set × plausible
  values, then assert the tier of every record in the project. (FR-002, SC-002)
- [x] T027 [US3] Re-measure protected MI ASR with the field harness and compare
  against the T001 baseline. Expect equal or lower. If it moved, explain **why**
  before accepting it — the oracle at
  [`oracle.py:167`](../../tests/security/field_mi_asr/oracle.py:167) counts
  privileged hints in `write_memory` params as a `dangerous_attempt`, and those
  attempts are now rejected earlier, so a lower number may reflect a real
  improvement *or* an oracle that stopped seeing what it used to count. Record the
  reading either way. (FR-009, SC-005)
- [x] T028 [P] [US3] `ActionClass.memory` still carries no OOB gate and
  `write_execute` still gates unchanged — assert against `KERNEL_GENERIC_ACTIONS`
  and one live `write_execute` pause. (FR-005)
- [x] T029 [P] [US3] Pin D10: `"model_note" not in SNAPSHOT_KINDS`, and a written
  note is absent from `memory.snapshot(...)` while a finding written in the same
  session is present. The second half matters — the first alone would pass on a
  snapshot builder that returns nothing.
- [x] T030 [US3] Add the D10 rationale as a comment beside `SNAPSHOT_KINDS` in
  `sr_agent/models/dispatch.py`: a model note is `llm_inference`, and admitting it
  to the pack's projection input would let the model's own prose become a premise.
  Written so nobody "completes" the allowlist in six months.

---

## Phase 6: Descriptor and docs (P2)

- [x] T031 [P] Rewrite `_D_WRITE_MEMORY` in
  [`sr_agent/tools/registry.py:44`](../../sr_agent/tools/registry.py:44). The
  current text — *"Write a structured finding or status update to episodic
  memory… HMAC is added by the orchestrator"* — is wrong on both halves after this
  feature: findings stay on the finding path (FR-003), and the model supplies a
  note, not a record. The description hash is recomputed at import
  (`_hash(_D_WRITE_MEMORY)`), so no literal needs updating; confirm
  `verify_all_hashes()` and `tests/security/test_tool_registry_integrity.py` stay
  green. (FR-010)
- [x] T032 [P] Reconcile `docs/kernel.md` and `docs/kernel.ru.md`: `write_memory`
  is kernel-executed, not a pack stub; add the `memory_write` event to whatever
  those docs say about memory observability. (FR-011)
- [x] T033 [P] Reconcile `docs/capability-pack-interface.md` and
  `docs/capability-pack-interface.ru.md`: a pack is not invoked for `write_memory`
  and must not stub it. Keep the two language versions in step — they are
  translations, not independent documents. (FR-011)

---

## Phase 7: Polish & gates

- [x] T034 Full-suite gate: `.venv/bin/python -m pytest tests/ -q` green,
  including the untouched `003` and `004` suites and
  `tests/architecture/test_single_dispatch_path.py`. No security test deleted or
  weakened to make this feature pass. (FR-009)
- [x] T035 [P] Walk [quickstart.md](quickstart.md) end to end against the built
  package and correct anything that drifted during implementation. It is the
  document a reader will actually run.
- [x] T036 [P] Update the `CLAUDE.md` SPECKIT block: status `planned` →
  `implemented`. The D10 warning already there stays.
- [x] T037 The carried finding from the plan, resolved as **option C** (validate
  before dispatch) rather than as originally written. Correction to this task's
  own text: the two sites are
  [`executor.py`](../../sr_agent/orchestrator/executor.py) `execute` and
  `resume`, both `commit_if_absent`; `write_pause_checkpoint` files under a
  kernel-derived `chat:{session_id}` and was never affected.

  The bound is applied **before `pack.dispatch`**, not at the `commit_if_absent`
  call site the task pointed at. Applying it there would have fixed the crash and
  left the real defect: the exception lands *after* the pack acted on the world,
  so the effect is done and the commit record never lands, `find_committed_bundle`
  finds nothing on retry, and the same action re-dispatches — one out-of-band
  confirmation covering two executions (Constitution II).

  Shared rule with T005: `check_target` / `commit_target` /
  `validate_commit_target` in `orchestrator/action.py`, one `MAX_TARGET_BYTES`.
  Behaviour delta against merged `003`, measured: a target of 201..255 bytes used
  to write and now rejects (over 255 it already raised `OSError`; a NUL already
  raised `ValueError`); an empty target still falls back to the action id as
  before, and a traversal-shaped target is still accepted and still contained.
  Covered by `tests/security/test_commit_target_bounds.py` (17 tests), which
  assert the refusal AND that the pack was never entered — a status-only
  assertion would pass against the fix applied at the wrong end of the path.

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1** → no dependencies.
- **Phase 2** blocks Phase 3 only. The interception assumes params are policed;
  landing it first would open a fail-open window mid-feature.
- **Phase 3 (US1)** and **Phase 4 (US2)** are independent of each other — disjoint
  files, disjoint requirements. Either can ship alone.
- **Phase 5 (US3)** depends on both: it is the non-regression proof over the
  finished surface, and T029 needs a note to exist before it can assert the note
  is absent from a snapshot.
- **Phase 6** depends on Phase 3 (the docs describe the shipped behaviour).
- **Phase 7** depends on everything.

### Parallel opportunities

- T002–T004 (Phase 2 tests) are one file but independent test functions; T008–T013
  and T016–T022 are two disjoint files and run fully in parallel.
- **Phases 3 and 4 can be worked concurrently by two people** once Phase 2 is
  green. They meet only at Phase 5.
- T031–T033 are four documentation files with no ordering between them.

### Within each phase

- Tests are written and **must fail** before their implementation.
- Never leave a phase checkpoint red — the `003` and `004` suites are part of
  every checkpoint, not just the last one.

---

## Notes

- [P] = different files, no ordering dependency.
- Use `.venv/bin/python -m pytest`; there is no `python` on PATH in the usual dev
  setup here.
- No signed-shape change in this feature, so unlike `003` and `004` there is no
  compatibility break and no re-signing question. Records written before it verify
  after it.
- Commits happen only on explicit request (constitution, Development Workflow).
