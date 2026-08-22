---
description: "Task list — DispatchResult, durable scope, and honest turn resume (kernel 003)"
---

# Tasks: DispatchResult, durable scope, and honest turn resume

**Input**: [spec.md](spec.md) (revision 11, accepted 2026-08-22), [plan.md](plan.md)

**Repository**: secure-agent-kernel (Repo A, `sr_agent`). Paired with araratsec
`004-audit-loop-methodology` (revision 12), which stays blocked on this feature's
**implementation**.

**Tests**: INCLUDED and **mandatory**. The constitution requires test-first for
security-critical behaviour, and this feature is almost entirely security-critical
behaviour. Every test task is authored to **FAIL first**, before the implementation it
guards.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no ordering dependency).
- **[Story]**: US1–US5 for traceability to spec user stories; `[INFRA]` for the
  foundations every story sits on.
- Exact file paths and FR/SC/D ids are in each task.

> **Scope note — not the template's "independently shippable MVP slices" shape.** The
> user stories here are facets of one contract, not separable products: US1 cannot ship
> without the commit path, US3 cannot ship without the lease, and the architecture
> invariant (FR-018a) is meaningless until the executor exists. They are grouped by story
> for traceability. Delivery is by **phase checkpoint**, and every checkpoint must leave
> `pytest tests/` green — including the untouched `004-memory-composition-integrity`
> suite, which is the regression gate for Phase 2.
>
> **Ordering constraint that is easy to get wrong**: T012 changes the HMAC-signed record
> shape. Everything written before it stops verifying (FR-006b / D39, inherited from
> `004`). So T012 lands early and once, and no task after it may re-sign or migrate old
> records.

---

## Phase 1: Setup & before-baseline

- [x] T001 [INFRA] Confirm branch `003-dispatch-result-resume`; dev-install editable
  (`pip install -e ".[dev]"`); export `SR_SECRET_KEY`; run `pytest tests/` green and
  record the baseline, specifically `tests/security/test_memory_composition.py` and
  `tests/unit/test_memory_integrity.py` — these are the `004` guarantees this feature
  must not regress (D37, D39).
- [ ] T002 [P] [INFRA] Inventory every production call site that writes memory or calls
  `CapabilityPack.dispatch`, across `sr_agent/**` and the araratsec pack pinned in the
  dev environment. This inventory is the worklist consumed by T060/T061 (the
  architecture invariant) and by T045 (batch/chat unification). Record it in the PR
  description, not in code.

---

## Phase 2: Foundational — identity, encoding, and the durable log (blocking)

**⚠️ CRITICAL**: every later phase keys off `transition_key` / `operation_id` and off
`log_sequence`. Nothing else may start until this checkpoint is green.

### Tests (write FIRST, must FAIL)

- [x] T003 [P] [INFRA] Golden-vector test in `tests/unit/test_canonical_encoding.py`:
  a fixed params object → canonical bytes → `transition_key` → `operation_id`, asserted
  against literals. Must cover NFC-equivalent-but-differently-spelled keys, absent vs
  explicit `null`, int vs float rendering, and non-finite float rejection. The vector
  MUST be reproduced by a **freshly started interpreter** (subprocess), not only in-process.
  (FR-005a, SC-016)
- [x] T004 [P] [INFRA] Determinism test in `tests/unit/test_operation_identity.py`:
  the same transition computed in two separate processes yields the same
  `operation_id`; changing any one input (session, action id, one param, chunk id,
  `expected_revision`, `scope_generation`) changes it. (FR-005, D22)
- [x] T005 [P] [INFRA] Sequence tests in `tests/unit/test_log_sequence.py`:
  `log_sequence` is monotonic and gap-free across appends to **two different target
  files** of one project; a fixture with a duplicated or missing sequence fails closed
  naming the offending values; an empty project starts at 1. (FR-006a, SC-014c)
- [x] T006 [P] [INFRA] Coexistence test in `tests/unit/test_log_sequence.py`: a record
  carries both `seq` / `chain_prev` (per-target chain) and `log_sequence`
  (project-global); `for_llm_context()` strips all three; `004`'s composition tests still
  pass. (FR-006a, D37, SC-014i)
- [x] T007 [P] [INFRA] Crash tests in `tests/unit/test_episodic_crash_safety.py`:
  (a) glued-line fixture — a torn tail is truncated before the next scan/append, and the
  retry is not swallowed as one corrupt line; (b) write-completed / `fsync`-failed kill
  point — the next start treats a verified complete line as committed;
  (c) torn append in `Vault.jsonl`, restart, then append to `Token.jsonl` — the tail is
  repaired first and no sequence is duplicated or burned. (FR-007a, FR-006a, SC-010, SC-014c)
- [x] T008 [P] [INFRA] Legacy test in `tests/unit/test_memory_integrity.py`: pre-change
  records stop verifying and read as an empty store; a target whose records all fail
  verification and which has no head entry starts a fresh chain on the next write
  (`004` `test_MI014` still passes); nothing is re-signed; no sequence is invented.
  (FR-006b, D39, SC-014e)

### Implementation

- [x] T009 [INFRA] Create `sr_agent/memory/canonical.py`: canonical encoder (UTF-8, NFC
  normalization of keys and string values, keys sorted by code point, separators without
  spaces, no non-finite floats, ints not rendered as floats, absent vs `null`
  distinguished, encoding version tag inside the digest input) and the constant protocol
  **UUID namespace literal**. The namespace is a source literal, versioned with the
  protocol, never derived at runtime. (FR-005a, D22)
- [x] T010 [INFRA] Add `derive_transition_key(...)` and
  `derive_operation_id(transition_key)` (UUIDv5) in `sr_agent/memory/canonical.py`.
  Inputs: `session_id` + action id + canonical digest of validated params + chunk
  identity + `expected_revision` + `scope_generation`. No random UUID path may remain.
  (FR-005, D22)
- [x] T011 [INFRA] `sr_agent/models/memory.py`: add `log_sequence: int | None`, kernel-set,
  **inside** `fields_for_hmac()` and **excluded** by `for_llm_context()` alongside
  `hmac` / `seq` / `chain_prev`. Document that it is a project-global order distinct from
  `004`'s per-target `seq`. (FR-006a, D37)
- [x] T012 [INFRA] `sr_agent/memory/episodic.py`: allocate `log_sequence` over the
  **project log = logical union of every `memory/<project_id>/*.jsonl`**. Under the
  project writer lease: torn-tail recover and verify all target files (including targets
  named by the head with no file on disk), validate the sequence set is contiguous
  without duplicates (fail closed otherwise), take `1 + max`, then append to the chosen
  target. No counter or reservation file anywhere. (FR-006a, D31)
- [x] T013 [INFRA] `sr_agent/memory/episodic.py`: torn-tail recovery + `flush` + `fsync`
  (file, and directory where the platform allows) on every durable append. (FR-007a, D15)

**Checkpoint**: T003–T008 pass; the full `004` composition suite is still green; no
production code re-signs or migrates a legacy record.

---

## Phase 3: US1 + US2 — kernel-authored records and exactly-once commit (P1)

**Goal**: a pack returns structure, the kernel persists it, and a committed transition
cannot be duplicated or forged.

**Independent test**: a fixture pack returns a `DispatchResult` with payloads; the kernel
commits one signed bundle at `tool_output`; retrying the same `transition_key` does not
call `dispatch` again; a hostile pack cannot set identity fields.

### Tests (write FIRST, must FAIL)

- [x] T014 [P] [US1] `tests/unit/test_dispatch_result.py`: `DispatchResult` carries
  `status` ∈ {ran, did_not_run, timeout, unavailable, error, pending}, `body`, `payloads`
  (empty when `pending`), and `pending {kind, correlation_id}` with `kind` restricted to
  the closed enum. An unknown `kind` is rejected as `error` **before** any checkpoint is
  written. (FR-002, SC-009)
- [x] T015 [P] [US1] `tests/security/test_hostile_pack.py`: a pack that sets
  `project_id`, `session_id`, `source_type`, `tool`, `operation_id`, or `record_id` on a
  payload has those rejected or overwritten; nothing pack-authored lands at `human_input`.
  (FR-004, SC-005)
- [x] T016 [P] [US1] `tests/unit/test_dispatch_payload_limits.py`: `payload_kind` must be
  `dispatch_payload`; body > 8192 encoded bytes or list > 32 items refuses persist of the
  **whole** transition; no audit-domain kind is allowlisted anywhere in the kernel.
  (FR-003, SC-009)
- [x] T017 [P] [US2] `tests/unit/test_commit_if_absent.py`: committing once then retrying
  the same `transition_key` returns the stored result and does **not** call `dispatch`;
  an `expected_revision` mismatch refuses with no append; a crash after a complete
  `fsync`ed line leaves the operation committed and not duplicated. (FR-006, FR-008,
  SC-002, SC-004)
- [x] T018 [P] [US2] `tests/unit/test_external_response_ingest.py`: put-if-absent on
  `(operation_id, correlation_id)` — equal body reuses the stored record with no second
  append; different body fails closed with a conflict; **hostile case**: a caller
  presenting an edited `approve` body while asserting the prior `deny` digest through any
  available path cannot match, and no stored record's digest disagrees with its own body.
  Ingest → kill → mutate the source file → resume reports the conflict and the stored
  decision stands. (FR-002c, D30, SC-012c)

### Implementation

- [x] T019 [P] [US1] Create `sr_agent/models/dispatch.py` with `DispatchResult`,
  `PendingWait` (closed `kind` enum), and `DispatchPayload`. (FR-002)
- [x] T020 [US1] `sr_agent/orchestrator/pack.py`: `CapabilityPack.dispatch` is typed to
  return `DispatchResult`. Add a **test-only** adapter for legacy string-returning fixture
  packs; production packs must return the structure. `PackContext` gains no memory handle;
  it may gain read-only `operation_id` / `transition_key`. (FR-001, FR-009)
- [x] T021 [US1] Kernel stamps `project_id`, `session_id`, `source_type=tool_output`,
  `tool`, `operation_id`, `record_id` itself and rejects pack-supplied values. No nested
  per-item `record_id`s. (FR-004, D14)
- [x] T021a [P] [US1] `tests/unit/test_dispatch_commit_authorship.py`: an executed
  transition whose status is not `pending` yields exactly one **kernel-authored**
  `dispatch_commit` at `tool_output`, and `PackContext` still exposes no memory write API
  (asserted by attribute inspection, not by convention). Written FIRST, must FAIL.
  (FR-006, FR-009, SC-001)
- [x] T022 [US2] `sr_agent/memory/episodic.py`: `commit_if_absent(session_id,
  operation_id, expected_revision, payloads)` with the six-step semantics — require
  `active_process`, torn-tail recover, return an existing verified bundle without
  appending, refuse on revision mismatch, append one signed `dispatch_commit` then
  `flush`+`fsync`, advance `session_revision` **and** assign `log_sequence`. Not exposed
  pack-facing. (FR-006)
- [x] T023 [US2] `put_external_response_if_absent(operation_id, correlation_id, body)` —
  signature takes **no** caller digest; the kernel canonicalizes the body and computes the
  digest itself; equality is decided only on digests computed from bytes the kernel holds.
  (FR-002c, FR-002b, D25, D30)

**Checkpoint**: US1 + US2 tests pass; a pack still has no way to write memory.

---

## Phase 4: The read seam (P1, blocks the pack)

**Goal**: a pack with no memory API can still build a full projection.

**Independent test**: a reducer receives a `MemorySnapshot` fixture containing a
standalone `finding` plus a matching and a non-matching `AnalyzerExecution`, and reaches
grounded/ungrounded correctly with no memory object in scope.

### Tests (write FIRST, must FAIL)

- [x] T024 [P] [INFRA] `tests/unit/test_memory_snapshot.py`: the snapshot contains
  standalone `finding` records, `dispatch_commit` bundles, and needed `external_response`
  records; it excludes other sessions and anything above the watermark; it rejects
  unverified records; it is frozen and ordered by `log_sequence`. (FR-009a, D29, SC-014)
- [x] T025 [P] [INFRA] Watermark stability: snapshot at `S`; append a `finding` and an
  `external_response` (neither advances `session_revision`); re-take at `S` → byte-identical;
  a snapshot at the new watermark includes them. (FR-006a, D28, SC-014a)
- [x] T026 [P] [INFRA] Future watermark: with `current_max = S`, a request for `S+1`
  fails closed naming both values — not clamped, not served as "everything so far";
  later appends do not change that refusal. (FR-009a step 2, D34, SC-014f)
- [x] T027 [P] [INFRA] Pipeline order: a correction at `S+1` leaves the snapshot at `S`
  byte-identical while `S+1` reflects it. The correction fixture MUST use a record kind
  that is **not** in the snapshot's output allowlist, so the test proves the order rather
  than passing accidentally on a correction-shaped Finding. Fails if the implementation
  supersedes before cutting, or filters kinds before resolving. (FR-009a step 2a, D32,
  D36, SC-014b)
- [x] T028 [P] [INFRA] Project-wide corrections, pinned deliberately: a `human_input`
  correction filed in session B against session A's finding **does** remove it from
  session A's projection from its own sequence onward (matching `004` FR-007), while an
  earlier watermark still contains it; a correction filed under a different `target` still
  applies; no write-path `session_id` check exists. (FR-007b, D35, SC-014g)
- [x] T029 [P] [INFRA] Composition break: with `004`'s chain or head damaged anywhere in
  the project, no snapshot is served, no projection is built, no dependent transition
  dispatches, and the break appears only on the operator channel — never in model context,
  never as a partial "verified subset". (FR-007c, D38, SC-014h)
- [x] T030 [P] [INFRA] Capacity boundary, four independent cases: items at
  `MAX_SNAPSHOT_ITEMS` and `MAX + 1`; canonical bytes at `MAX_SNAPSHOT_BYTES` and
  `MAX + 1`. Over-limit fails closed naming the limit, the measured value, and the remedy.
  No truncation, sampling, or oldest-record dropping on any path. (FR-009b, SC-014d)

### Implementation

- [x] T031 [INFRA] `sr_agent/models/dispatch.py`: `MemorySnapshot` — frozen, fully
  materialized, ordered by `log_sequence`, each item exposing envelope identity plus the
  opaque body, with `as_of_sequence` recorded. (FR-009a)
- [x] T032 [INFRA] `sr_agent/memory/episodic.py`: snapshot builder implementing the
  authoritative six-step pipeline — verify + composition check, cut the prefix, retain all
  correction carriers and candidate inputs, apply `supersedes` **project-wide**, filter by
  session, then final kind selection. Must not reuse `_apply_supersedes(_all_records(...))`
  before cutting. (FR-009a, D32, D35, D36)
- [x] T033 [INFRA] Kernel pins the watermark under the lease: establish `current_max`,
  serve at that value or at a requested value in `[0, current_max]`, reject anything
  higher. (FR-009a step 2, D34)
- [x] T034 [INFRA] Define `MAX_SNAPSHOT_ITEMS = 10000` and
  `MAX_SNAPSHOT_BYTES = 33554432` as kernel constants — the single source of truth; the
  fail-closed error names the limit, the measured value, and the remedy. (FR-009b, D33)

**Checkpoint**: T024–T030 pass; the `004` suite is still green; a pack could build a
roadmap from a snapshot fixture alone.

---

## Phase 5: US3 — durable session, scope binding, and the lease (P1)

**Goal**: a session knows what it is bound to, and only one writer touches a project.

**Independent test**: resume from an unrelated cwd analyses the originally bound target;
a mutated tree at the same path fails closed; a second `session_id` is refused against a
paused reservation.

> **Carried debt from Phases 2-4, closed here.** Three things were deferred to the
> lease and must land with it, not after:
>
> 1. `log_sequence` allocation (T012) and `commit_if_absent` (T022) currently run
>    without the `active_process` requirement, so nothing yet stops two writers
>    from choosing the same number.
> 2. Measured 2026-08-22: append cost is linear in project-log size, so filling a
>    session is quadratic — 13.8 ms/append at 200 records, 25.3 at 400, 47.7 at
>    800. Extrapolated to `MAX_SNAPSHOT_ITEMS = 10000` that is ~600 ms/append and
>    ~50 minutes to reach the declared cap, which makes the cap unreachable rather
>    than a fail-closed product boundary (D33, SC-014d). Cause: every `write`
>    walks the whole log three times (tail recovery, sequence allocation, chain
>    tip). Remedy chosen by the operator: cache the verified log **per lease
>    holder** — sound precisely because a lease owner is the only writer, so the
>    verified prefix cannot change underneath it. Merging the three passes alone
>    was rejected: a ~3x constant does not make a quadratic curve reachable.
> 3. T030's capacity boundaries are currently exercised with the constants
>    monkeypatched, because materializing 10001 records is itself blocked by (2).
>    Once (2) is fixed, at least the item-count boundary should run against the
>    real constant, and that run doubles as the SC-014d characterization.
>
> **Resolution of (1) and (2), 2026-08-22.** The lease landed and every durable
> append now goes through `_require_lease`. The verified-log cache is keyed to the
> lease holder, and `_ProjectView` additionally carries the two summaries the
> append path needs — per-target chain tip and highest `log_sequence` — so
> appending is O(1) rather than a re-derivation over the whole log. Re-measured
> after the change: 3.51 ms/append at 200 records, 3.57 at 800, 3.98 at 2000,
> 4.34 at 10000 — flat, dominated by `fsync` and the head rewrite rather than by
> log size. Reaching `MAX_SNAPSHOT_ITEMS` takes ~43 s and building the snapshot at
> that size takes 0.37 s, so the declared cap is now a real boundary. This is the
> SC-014d characterization.
>
> Two notes that follow from the measurement rather than from the design:
> - for a 10000-record finding log the canonical size is ~2.3 MB, so the ITEM cap
>   binds long before the 32 MiB byte cap for that shape; the byte cap binds first
>   only for large payload bodies.
> - a real-constant boundary test costs ~43 s, so T030 stays on monkeypatched
>   constants for the fast suite; the run above is the characterization instead.

### Tests (write FIRST, must FAIL)

- [x] T035 [P] [US3] `tests/unit/test_scope_binding.py`: resume restores canonical
  `scope_root`; the `Path(".")` default is gone; a missing path or a digest mismatch fails
  closed; dirty/untracked files in the include set fail resume even when git HEAD is
  unchanged; over-budget (>10000 files, >100 MiB, any file >8 MiB) fails closed.
  (FR-012, FR-013, FR-013a, SC-006)
- [x] T036 [P] [US3] `tests/unit/test_content_scope_policy.py`: `read_file` and
  `search_code` refuse a path inside `scope_root` but outside the bound include set, and
  anything under `runtime_state_roots`; `search_code` enumerates only include-set files
  rather than walking the passed root; the sandbox mount is limited to the same set.
  (FR-013b, D20, SC-006)
- [x] T037 [P] [US3] Scope-integrity scenario: with include set `contracts/**`, mutating
  `script/Deploy.sol` after detach must not leave a file that is both undigested and
  readable — either the read is refused, or the file is in the digest and resume fails
  closed. (FR-013b, SC-006a)
- [x] T038 [P] [US3] `tests/unit/test_scope_rebind.py`: `rebind_scope` during a pending or
  in-flight operation is **refused**, naming the operation, and is not auto-converted into
  a pause; at a turn boundary it records a control event and increments `scope_generation`;
  the next otherwise-identical transition gets a different `operation_id`; resuming a
  checkpoint from the previous generation fails closed. (FR-013c, D27, SC-015)
- [x] T039 [P] [US3] `tests/unit/test_writer_lease.py`: two processes cannot both write;
  `paused_reserved` refuses another `session_id` even after flock/heartbeat death and never
  times out; `detached` releases the lease; the same `session_id` reacquires from
  `paused_reserved` or `detached`; `completed` / `abandoned` cannot reacquire as writer.
  Every durable append (`commit_if_absent`, `save_turn`, finding persist,
  `pause_checkpoint`) is refused for a non-owner. (FR-014, FR-016, SC-003)

### Implementation

- [x] T040 [US3] `sr_agent/models/session.py`: durable `scope_root`, `content_identity`,
  `scope_generation`, `session_revision`, `writer_session_id`, `lease_mode`, continuation
  fields; `SessionStatus` gains `completed`, `abandoned`, `detached`. (FR-010)
- [x] T041 [US3] Create `sr_agent/orchestrator/scope.py`: `ContentScopePolicy` (resolved
  include set + composition-root `runtime_state_roots`), the worktree digest algorithm
  (no symlink follow, skip `.git` / `__pycache__` / runtime roots, budget, sort by relative
  POSIX path, SHA-256 over `path + NUL + size + NUL + bytes`, no mtime/mode), and
  `rebind_scope`. (FR-013a, FR-013c)
- [x] T042 [US3] `sr_agent/tools/readonly.py`: bind `read_file` / `search_code` to the
  resolved include set; `search_code` enumerates the set instead of walking the root.
  (FR-013b)
- [x] T043 [US3] Create `sr_agent/orchestrator/lease.py`: the state machine
  (`active_process` / `paused_reserved` / `detached` / `completed` / `abandoned` /
  derived `crashed_active`) with flock + heartbeat, and coverage of every durable append.
  (FR-014, D16)
- [x] T044 [US3] `sr_agent/orchestrator/chat_session.py`: `complete_session`,
  `abandon_session`, `detach_session` (legal only at a completed-turn boundary — otherwise
  refuse or checkpoint), `reacquire_lease`, `takeover_lease`, `rebind_scope`. All are
  operator-authorized control operations, absent from model vocabulary, not pack-callable,
  each recorded as an explicit control event at `human_input`. (FR-019, FR-014)

**Checkpoint**: T035–T039 pass; the `Path(".")` defect is gone from the codebase.

---

## Phase 6: US3 + US5 — the executor, pause, and resume (P1) 🎯

**Goal**: this is where the user-visible defect disappears — all three pause states and
`pending` round-trip through one durable checkpoint, on both surfaces.

**Independent test**: pause on relay, kill the process, restart: the session loads as
paused, resume continues the same turn with the tool budget intact, and no relay request
is created twice.

### Tests (write FIRST, must FAIL)

- [x] T045 [P] [US5] `tests/architecture/test_single_dispatch_path.py`: the only
  production caller of `CapabilityPack.dispatch` is `KernelActionExecutor`, on chat **and**
  batch. (FR-018, SC-011)
- [x] T046 [P] [US3] `tests/unit/test_pause_checkpoint.py`: each pause path
  (`paused_confirmation`, `paused_relay`, `blocked_local_unavailable`) and
  `status=pending` persist **exactly one** record and then drop flock; no second JSONL
  write makes the pause visible; a crash right after the checkpoint loads as paused, not
  `active`; the tool-call budget survives. (FR-010a, FR-010b, SC-007)
- [x] T047 [P] [US3] `tests/unit/test_resume_turn.py`: `resume_turn` continues the stored
  phase after `reacquire_lease` for the same `session_id` and never falls back to
  `run_turn(user_message)`; committed `operation_id`s are not duplicated; a resume that
  cannot continue reports why. (FR-011, SC-007)
- [x] T048 [P] [US3] Action snapshot: resume rebuilds the action from
  `action_type` + canonical validated params + `pack_id` + `pack_contract_version` +
  `scope_generation` + `content_identity`, re-derives the params digest and matches it
  against `transition_key`, and makes **no** model call to restate the action. An absent or
  incompatible `pack_contract_version` fails closed, and the kernel does not resolve an
  alternative pack version at runtime. A `human_confirmation` carrying only a correlation
  id executes nothing. (FR-010c, D23, SC-012a)
- [x] T049 [P] [US3] **Full-restart idempotency** — the flagship crash test: kill the
  process after the relay request file is created and **before** the checkpoint is
  written; a fresh executor re-derives the transition from scratch, mints the same
  `operation_id` / correlation id, adopts the existing request, and creates no second one.
  Seeding `operation_id` into the test is explicitly insufficient. (FR-021, FR-005,
  SC-012)
- [x] T050 [P] [US3] Response durability: a crash after ingesting the external response
  and before the final commit leaves it readable on the next resume; the source artifact
  is neither deleted nor moved by ingest. (FR-002b, SC-012b)
- [x] T051 [P] [US5] Parity: forged identity and concurrent-writer rejection behave
  identically on chat and batch. (FR-018, SC-005)

### Implementation

- [x] T052 [US3] `sr_agent/models/dispatch.py`: `ActionSnapshot` and the
  `pause_checkpoint` payload — `turn_id`, `phase`, `user_message`, `system_prompt_id` /
  `_hash` / optional `_version` (**never** the body), `last_dispatch_operation_id`,
  `pending`, `last_tool_body_ref`, `tool_calls_used`, correlation copies,
  `expected_session_revision`, `session_status`, and the Action snapshot. (FR-010a, FR-010c)
- [x] T053 [US5] Create `sr_agent/orchestrator/executor.py`: `KernelActionExecutor.execute`
  — validate, derive `transition_key` and `operation_id`, skip `dispatch` when already
  committed, `commit_if_absent` payloads only when the status is not `pending`, and on
  `pending` write one `pause_checkpoint`. The sole production dispatch path for chat and
  batch. (FR-018, D17)
- [x] T054 [US3] Executor pending protocol: no payload commit; one checkpoint including
  `pending` + Action snapshot; return the matching paused state per `kind`; on resume
  ingest the response as a durable record, rebuild the action, re-enter `dispatch` with the
  same identity, and commit only if the second return is not `pending`. A repeated
  `pending` is allowed only with an unchanged `correlation_id`. (FR-002a)
- [x] T055 [US3] `sr_agent/orchestrator/loop.py`: pause writes exactly one checkpoint then
  drops flock; add `resume_turn`; remove the "resume is not wired yet — continuing as a
  fresh turn" path. (FR-010b, FR-011)
- [x] T056 [US3] Effect-port idempotency: correlation ids for relay requests and
  confirmations are derived from `transition_key` / `operation_id`, and a port recognises
  and adopts its own prior effect instead of re-creating it. Persist-then-reuse of a random
  id is not acceptable. (FR-021)

**Checkpoint**: T045–T051 pass. The two originally-broken pause states resume for real.

---

## Phase 7: Trusted prompt registry (P1)

### Tests (write FIRST, must FAIL)

- [x] T057 [P] [US3] `tests/security/test_prompt_registry.py`: resume loads prompt bytes
  from the registry and verifies the hash; a missing id/version fails closed; a checkpoint
  whose `system_prompt_hash` is attacker-controlled DATA is **not** used as the system
  instruction. (FR-020, D18, SC-013)

### Implementation

- [x] T058 [US3] Create `sr_agent/orchestrator/prompts.py`: a trusted registry that is not
  episodic memory and is never wrapped into the model as prior-turn DATA. The mechanism is
  kernel-owned; the **content** is registered by the pack, so no audit-domain prompt body
  enters kernel source (Constitution III). (FR-020)

---

## Phase 8: US4 + invariants — compatibility and the guardrails that keep this true (P2/P1)

### Tests (write FIRST, must FAIL)

- [x] T059 [P] [US4] `tests/unit/test_session_compat.py`: old snapshots still verify under
  `004`'s rules; new required fields are never invented at load; resume of an old snapshot
  without `scope_root` / checkpoint fails explicitly. (FR-015, SC-008)
- [x] T060 [P] [US5] `tests/architecture/test_no_pack_memory_writes.py`: a **whole-package**
  AST/import-graph scan of the active pack's production package (excluding `tests/` and
  `**/test_*.py`) fails on any import of `EpisodicMemory`, any `MemoryRecord` construction
  for append, or any `.write(` on a memory object. Production allowlist: **empty**. Adding
  a new helper module must fail this test **without** editing a file list. (FR-018a, SC-011)
- [x] T061 [P] [US5] `tests/security/test_hostile_pack.py`: runtime companion — a
  memory-like object reached through an alias, attribute, duck-typed helper, or a
  `PackContext` extension still cannot append. Not merely un-imported in source: not
  reachable. (FR-018b, SC-011)
- [x] T062 [P] [US3] `tests/security/test_mi_resistance.py`: snapshot bodies,
  `external_response` bodies, and checkpoint fields that re-enter the model
  (`user_message`, last tool body) all re-enter as DATA; none becomes an instruction.
  Protected-run ASR stays 0. (FR-017)
- [x] T063 [P] [US3] Operator-only controls: `takeover_lease`, `abandon_session`,
  `complete_session`, and `rebind_scope` are absent from the offered action vocabulary in
  every model-facing surface test, and each appears in memory as an operator-authorized
  control event. (FR-019)

### Implementation

- [x] T064 [US4] Version the `ChatSession` projection and its load path per T059; no
  silent field invention. (FR-015)
- [x] T065 [US5] Route `sr-agent audit`-style batch execution through
  `KernelActionExecutor` (kernel side of the seam), using the T002 inventory. (FR-018)

---

## Phase 9: Polish & characterization

- [x] T066 [P] Performance characterization at the capacity envelope: record wall-clock for
  snapshot build and for one append (whose union scan is linear in total project-log
  records and bytes, not logarithmic), so the quadratic total cost of scan-per-append is a
  measured, accepted property of the 10000 / 32 MiB envelope. Reuse the T030 at-limit
  fixture. (SC-014d)
- [x] T067 [P] Update `docs/` and the kernel `CLAUDE.md` SPECKIT block to point at this
  feature; document the snapshot capacity limit and the "complete and start a new session"
  remedy as operator-facing behaviour, not an implementation detail. (FR-009b)
- [x] T068 Full-suite gate: `pytest tests/` green, including the untouched `004`
  composition suite and the MI harness at ASR 0. (Constitution security requirements)
- [x] T069 Notify the pack: araratsec `004-audit-loop-methodology` may generate its
  `plan.md` / `tasks.md` only after this feature is **implemented and merged** — not after
  this task list exists.

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1** → no dependencies.
- **Phase 2** blocks everything: identity, encoding, and `log_sequence` are inputs to every
  later contract. T012 in particular changes the signed shape and must land once, early.
- **Phase 3** depends on Phase 2 (commit needs sequence + canonical identity).
- **Phase 4** depends on Phase 2 (watermark) and Phase 3 (there must be bundles to read).
- **Phase 5** depends on Phase 2 (control events are durable appends).
- **Phase 6** depends on Phases 3, 4, and 5 — the executor needs commit, snapshot, and
  lease. This is the integration point.
- **Phase 7** depends on Phase 6 (resume is where the registry is consulted).
- **Phase 8** depends on Phase 6 (an architecture invariant needs the executor to exist to
  be meaningful; landing it earlier would pin the wrong shape).
- **Phase 9** depends on everything.

### Parallel opportunities

- T003–T008 (Phase 2 tests) are independent files and run in parallel.
- Within Phases 3–8, all tasks marked [P] touch different files.
- Phases 4 and 5 can be worked concurrently by two people once Phase 3 is green: the
  snapshot and the lease/scope work do not overlap in files, and only meet in Phase 6.

### Within each phase

- Tests are written and **must fail** before their implementation.
- Models before the services that use them; services before the loop wiring.
- Never leave a phase checkpoint red — the `004` suite is part of every checkpoint.

---

## Notes

- [P] tasks = different files, no ordering dependency.
- Commit after each task or logical group; commits happen only on explicit request.
- The three crash contracts most likely to be faked by an over-eager test are T007(c),
  T049, and T050. Each names its kill point deliberately; a test that pre-seeds state
  instead of restarting the derivation path does not satisfy it.
- Nothing in this list re-signs, migrates, or invents identity for a legacy record.
