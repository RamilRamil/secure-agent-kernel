# Feature Specification: Kernel-owned `DispatchResult`, atomic operation commit, durable scope, turn resume

**Repository**: secure-agent-kernel (Repo A, package `sr_agent`)

**Feature Branch**: `003-dispatch-result-resume`

**Created**: 2026-08-21 · **Revised**: 2026-08-22 (revision 11)

**Status**: Draft (revision 11)

**Paired feature**: araratsec-agent `004-audit-loop-methodology` (consumes this contract for stage events, roadmap projection, and honest resume). Kernel `plan.md` / `tasks.md` MUST NOT start until this revision is accepted. Pack 004 `plan.md` / `tasks.md` wait on this feature's **implementation**.

**Depends on**: `001-task-agnostic-contract` (open action taxonomy, `PackContext` least privilege) and the **implemented** `004-memory-composition-integrity` (per-target `seq` / `chain_prev` chain, signed `_chain_head.json`, project-wide `supersedes`, fail-closed composition break). This feature MUST NOT weaken any of those; see D35, D37, D38, D39. Orthogonal to `002-memory-write-path` (model-proposed `write_memory` interception + `memory_write` observability). This feature MUST NOT reuse 002's branch, schema, or FR numbers as if they already defined `DispatchResult`.

**Note on numbering**: Kernel-local `003-*`. Distinct from araratsec `003-agent-tool-surface`. Do not conflate the two.

**Input**: Architecture reviews of pack 004 through revision 5. Revision 3 of this spec
locked `pause_checkpoint`, the lease state machine, and `KernelActionExecutor`. Revision
3 still stored `system_prompt_body` in episodic memory (Constitution I), had no
`pending` dispatch status for relay, and scoped the no-write test to known files.
Revision 4 locked the trusted prompt registry, generic async `pending`, the
package-wide architecture invariant, `detached`, and a declared digest include set.
Revision 5 closes the delta that `pending` and the include set opened: a random
`operation_id` cannot make an effect idempotent across a full restart, the checkpoint
did not store the Action to re-dispatch, and the include set bounded hashing but not
reads. Revision 5 locks deterministic operation identity, a durable Action snapshot,
and one `ContentScopePolicy` enforced on every read path.

Revision 5 in turn left three seams that its own new contracts created: ingesting an
external response was durable but not idempotent, a pack with no memory handle had no
defined way to read its own prior events for the roadmap projection, and `scope rebind`
existed as a permitted operation with no place in the session/operation state machine.
Revision 6 locks `put_external_response_if_absent` (durable record wins), a
kernel-supplied immutable **read snapshot** as the only pack read seam, and rebind as an
operator control event bound into operation identity through `scope_generation`.

Revision 6's snapshot omitted standalone `finding` records (so grounding could not be
joined), used the dispatch-only `session_revision` as its watermark (so the same
`as_of_revision` could denote two different histories), and accepted a caller-supplied
`body_digest` without recomputing it. Revision 7 locks a kernel-assigned monotonic
`log_sequence` on every durable append, a snapshot bounded by `as_of_sequence` and
carrying findings, and digest computation inside the kernel.

Revision 7 stated a capacity limit as an obligation without naming numbers, left the
interaction between the sequence prefix and `_apply_supersedes` undefined (a later
correction could retroactively change an older snapshot), and described a "project log"
watermark while storage is actually one JSONL per target. Revision 8 fixes the two
numbers, pins the evaluation order, and defines the project log as the logical union of
the project's target files.

Revision 8 left two narrow holes in that order: a caller could ask for a watermark above
the current maximum (so the same `as_of_sequence` would grow as the log grew), and
`supersedes` ran before the session filter (so a correction written in another session
could delete a record from this session's snapshot while the correction itself was
dropped as out-of-scope). Revision 9 pins the watermark in the kernel, rejects future
values, and makes corrections session-scoped by scoping before superseding.

Revision 9 combined session and kind filtering into one step placed before `supersedes`,
which would drop a correction whose own record kind is not an output kind, and left the
older D32 stating the opposite order. Revision 10 splits scoping from final selection
into an explicit six-step pipeline and marks D32 superseded.

Revision 11 reconciles this feature with the **already-implemented** kernel feature
`004-memory-composition-integrity`, which revisions 1–10 did not account for. That
feature put a per-target `seq` + `chain_prev` chain and a signed `_chain_head.json`
inside the signed shape, resolves `supersedes` **project-wide**, withholds the whole
project on a composition break, and settled the legacy-record question. Revision 11
therefore: defines `log_sequence` as a distinct project-global field alongside `seq`;
drops the session-scoped correction rule in favour of `004`'s project-wide resolution;
adopts `004`'s legacy decision instead of version-aware verification; and makes a
composition break a fail-closed condition for the snapshot (operator decision
2026-08-22).

## Why this exists

Pack 004 needs the kernel to persist structured stage results, bind a session to a filesystem *and* content identity, resume paused turns, and make stage transitions idempotent. None of that is in `002-memory-write-path`:

| Concern | `002-memory-write-path` | This feature |
|---|---|---|
| Trigger | model `next_action=write_memory` | return value of `pack.dispatch` |
| Provenance | `llm_inference` for model notes | kernel-set `tool_output` for allowed payloads |
| `DispatchResult` | absent | defined here |
| Durable `scope_root` / content identity | absent | defined here |
| `resume_turn` / `pause_checkpoint` | absent | defined here |
| Operation replay / uniqueness | absent | `commit_if_absent` here |
| Crash-safe append | absent | log protocol here |
| Read boundary = digest boundary | absent | `ContentScopePolicy` here |

Today `dispatch` is typed to return a string. The loop keeps it as in-memory `last_tool_output` and a short summary. `ChatTurn.agent_action` is saved as `None` from the pack CLI regardless of outcome. There is no tool-result *record* and no durable loop phase. `EpisodicMemory.write` does `f.write()` without `fsync` and without torn-tail recovery; a crash can glue a partial line to the next append so the loader drops both.

This feature closes that seam without giving packs a memory handle (Constitution III, `PackContext` least privilege), without treating HMAC as a uniqueness mechanism, and without putting audit-domain `payload_kind` strings in the kernel.

## Resolved Decisions

- **D8 — `dispatch` returns `DispatchResult`; kernel persists.** After `pack.dispatch` returns, the kernel DATA-wraps the operator-facing body and, when persistable payloads are present, commits them itself via `commit_if_absent`. The pack MUST NOT choose `project_id`, `session_id`, `source_type`, or `tool`. `PackContext` still has no memory write API. Rejected: a memory handle on `PackContext`; rejected: composition-root code in the pack that calls `EpisodicMemory.write`.
- **D9 — uniqueness is an atomic kernel commit, not a payload field.** The only persist path for dispatch payloads is `commit_if_absent(session_id, operation_id, expected_revision, payloads)`. A field named `operation_id` inside a pack payload is not sufficient. HMAC remains an integrity check, not a uniqueness check.
- **D10 — durable session scope includes content identity of what is read.** Binding is canonical `scope_root` plus a **worktree digest** of the files the kernel/pack will actually read (algorithm in FR-013a). Git remote URL + HEAD SHA, when a `.git` directory exists, are **additional** metadata, not a substitute: they do not detect dirty/untracked files. Path-only binding is rejected. Git HEAD-only identity is rejected.
- **D11 — writer lease is a state machine, not flock-plus-timeout.** States:
  `active_process` (OS lock + heartbeat), `paused_reserved` (durable reservation,
  **no** timeout-steal), `completed` / `abandoned` (released), derived
  `crashed_active` (last durable mode was `active_process`, flock gone, heartbeat
  stale → steal allowed). Same `session_id` MAY reacquire `paused_reserved` into
  `active_process`. A different `session_id` is refused while `paused_reserved` or
  `active_process`. Takeover of `paused_reserved` is an explicit human-approved
  kernel operation (`takeover_lease`), not a timer. REPL EOF (Ctrl-D) MUST call
  `detach_session` (release lease, keep history, allow later reopen of the same
  `session_id`). It MUST NOT call `complete_session`. Complete/abandon remain
  explicit. Process death is not complete. Rejected: steal of a paused owner;
  rejected: lease only inside `commit_if_absent`; rejected: paused indistinguishable
  from crash; rejected: Ctrl-D as audit/conversation completion.
- **D12 — compute vs commit.** If a matching `operation_id` / `transition_key` is already committed, `dispatch` is not called. If compute finished but commit did not, resume may re-compute (at-least-once compute) and the second `commit_if_absent` is a no-op if the first commit landed. If the bytes were written and `fsync` then reported failure, the next start MUST scan: a verified complete bundle is committed (do not treat the operation as new).
- **D13 — one `pause_checkpoint` record is the pause SoT.** Pause is a single
  HMAC-signed JSONL record (`payload_kind=pause_checkpoint`) that contains the
  continuation fields **and** the new session status (and lease mode
  `paused_reserved`). `load_session` / `resume_turn` MUST derive status from the
  latest verified checkpoint (or an explicit later `complete`/`abandon` record).
  Rejected: two ordered writes (`TurnContinuation` then `ChatSession` snapshot).
  `TurnContinuation` remains the field set, not a separate persist step.
- **D14 — pack-owned `AnalyzerExecution`; kernel payloads are generic.** Kernel persistable kind is only `dispatch_payload` (plus kernel-owned `dispatch_commit` / `pause_checkpoint` / `writer_lease` envelopes). Discriminator and schema live in the pack body. Grounding MUST NOT require a kernel `record_id` stamped into pack JSON before commit. Pack 004 puts analyzer outcome, truncated result, target digest, and finding ids in **one** `AnalyzerExecution` object inside a `dispatch_payload`. Intra-payload join; no `result_record_id`. Projection MUST use `operation_id` from the kernel envelope, not an echo inside the opaque body. Rejected: kernel allowlist of `stage_event` / `execution_evidence` / `tool_result`.
- **D15 — crash-safe JSONL protocol, not "one line implies atomic".** Before scan or append, under the writer lease, the log file MUST be torn-tail recovered (truncate to the last complete newline), then a complete record line written, then `flush` + `fsync`. A torn write MUST NOT remain at EOF where the next append would concatenate. Logical uniqueness remains lease + scan + one bundle record. Rejected for this feature: SQLite; rejected: claiming `f.write` of one `MemoryRecord` is atomic.
- **D16 — every durable write for the leased project takes the lease.** `save_turn`, finding persist, `commit_if_absent`, and `pause_checkpoint` MUST NOT append while another session holds `active_process` or `paused_reserved`. `session_revision` CAS applies to **dispatch commits only**.
- **D17 — `KernelActionExecutor` is the only durable dispatch path.**
  `execute(pack, session, action) -> DispatchResult` (committed or pending). It
  validates, derives `transition_key` and the deterministic `operation_id` (D22),
  calls `pack.dispatch` only if the
  `transition_key` is not already committed, `commit_if_absent`s payloads only when
  status is not `pending`, and on `pending` writes one `pause_checkpoint`. Chat
  `OrchestratorLoop` and `sr-agent audit` both call this primitive. Production pack
  code MUST NOT call `EpisodicMemory.write` (D21). PackContext still has no memory
  handle.
- **D18 — system prompt is never an episodic instruction (Constitution I).**
  `pause_checkpoint` stores only `system_prompt_id` and `system_prompt_hash` (and
  optional immutable `system_prompt_version`). Prompt **bytes** are loaded from a
  kernel-owned **trusted prompt registry** (code bundle or a configuration store
  that is not episodic memory and is never wrapped into the model as prior-turn
  DATA). Resume: load bytes from the registry, verify hash, then use those bytes as
  the system instruction. If the id/version is absent from the registry, resume
  **fails closed** with an explicit message. HMAC on the checkpoint proves the
  *reference* was not swapped; it does **not** authorize executing checkpoint
  contents as instructions. Rejected: `system_prompt_body` in episodic memory;
  rejected: using any memory artifact as the system prompt.
- **D19 — `DispatchResult` may be `pending`.** Status set is `ran` / `did_not_run` /
  `timeout` / `unavailable` / `error` / **`pending`**. `pending` carries
  `{kind, correlation_id}` where `kind` is a **closed kernel enum**:
  `external_response` / `human_confirmation` / `local_model_retry`. An unknown
  `kind` is rejected **before** any checkpoint (the executor cannot continue a
  wait it does not implement). The executor persists that pending state inside the
  same `pause_checkpoint` (never a completed `dispatch_commit`). On resume it
  injects the stored response as DATA and re-enters the adapter with the same
  identity. Final `dispatch_payload` commits only when status is not `pending`.
  Pack MUST NOT write checkpoints.
- **D20 — one `ContentScopePolicy` binds hashing *and* reading.** The composition
  root supplies `runtime_state_roots`; the pack supplies an **input include set**
  (globs / prefixes). That resolved set is the bound policy on the session: it is
  what the kernel hashes (FR-013a) **and** the only thing `read_file` /
  `search_code` may return, and the only thing the analyzer sandbox may mount.
  Reading a file inside `scope_root` but outside the include set is refused like a
  traversal escape. Widening requires an explicit operator **scope rebind** that
  records a new digest. Kernel MUST NOT hardcode relay/report directory names.
  Rejected: include set as a hashing optimisation while reads stay `scope_root`-wide.
- **D21 — production pack has zero durable-memory API.** Architecture tests scan
  the entire production pack package (not a file allowlist of today's helpers).
  No import of `EpisodicMemory`, no `MemoryRecord` construction for append, no
  `.write(` on memory. The only production caller of `pack.dispatch` is
  `KernelActionExecutor`. A runtime hostile test complements the AST scan: a
  memory-like object reached through an alias, attribute, or untyped helper still
  MUST NOT let pack code append. Test code is out of scope of the production scan.
- **D22 — operation identity is deterministic, not a fresh UUID.** `transition_key`
  is computed first (stable inputs only); `operation_id = UUIDv5(kernel namespace,
  transition_key)`. A full process restart that recomputes the same transition
  derives the **same** `operation_id`, so an effect keyed on it (relay request file,
  confirmation id) is idempotent even when the crash happened after the effect and
  before any durable record. Rejected: random UUID minted per attempt; rejected: an
  extra `operation_started` reservation record (adds a second crash-consistency
  point for no gain).
- **D23 — the checkpoint stores the Action, not just its digest.** `transition_key`
  contains a params digest, which cannot be inverted. `pause_checkpoint` therefore
  carries a durable **Action snapshot**: `action_type`, canonical validated params,
  `pack_id`, `pack_contract_version`. Resume re-dispatches from that snapshot after
  re-deriving and matching the digest. Missing or incompatible pack version →
  fail closed, exactly like a missing prompt registry version. Re-asking the model
  for the action, or reconstructing it from `user_message`, is forbidden. This
  applies to `human_confirmation` too: a correlation id alone cannot execute an
  approved action.
- **D24 — the external response is durable before it is consumed.** Ingestion of a
  relay/confirmation body writes one kernel DATA record (`payload_kind=
  external_response`, tier `external_llm_output` for relay, `human_input` for an
  operator confirmation decision) **before** the re-dispatch that may fail. Resume
  reads that record, not the source file. A crash after ingestion and before the
  final commit MUST NOT lose the only copy. Consumption MUST be non-destructive:
  the executor does not delete or move the source artifact as part of ingest.
- **D25 — ingestion is put-if-absent and the durable record wins.** The API is
  `put_external_response_if_absent(operation_id, correlation_id, body)`
  under the writer lease, with the same torn-tail + `fsync` protocol as
  `commit_if_absent`. `(operation_id, correlation_id)` is the uniqueness key. An
  existing record with the **same** `body_digest` is accepted and returned (no second
  append). An existing record with a **different** digest is **fail closed**: the
  operation stops and reports a conflict; it MUST NOT overwrite, append a rival, or
  prefer the file. After the first successful put, the durable record is the sole
  source of truth and the file on disk is never re-read for that key. This is what
  stops an edited confirmation file from turning a stored `deny` into an `approve`.
  Rejected: re-ingest on every resume; rejected: last-write-wins.
- **D26 — packs read through a kernel-supplied immutable snapshot, never a handle.**
  A pure reducer needs prior events (roadmap projection, regenerable synthesis) while
  production pack code has no memory API (D21). The executor therefore loads
  HMAC-verified, committed, session-scoped records up to a **fixed `as_of_sequence`**
  (D28) and passes a frozen, bounded **read snapshot** as an argument to the pack call.
  It is
  plain data: no `EpisodicMemory`, no callable, no query handle, no lazy cursor. The
  kernel verifies signatures, session scope, and the revision bound; the pack parses
  its own opaque bodies and builds the domain projection. This extends the existing
  `PackContext` precedent that "prior findings ... are passed as arguments, not read
  from here" (`sr_agent/orchestrator/pack.py`). Rejected: a read-only memory handle on
  `PackContext`; rejected: a pack-side second orchestration path that re-opens JSONL
  and would have to defeat the architecture test.
- **D27 — `scope rebind` is a control event bound into operation identity.** Rebinding
  the `ContentScopePolicy` (new include set and/or new `content_identity`) is an
  operator-authorized control event, legal only at a completed-turn boundary. With a
  pending or in-flight operation it is **refused**; the operator must first let that
  operation finish or end the session with the existing `abandon_session` API (there is
  no per-operation abandon in this feature). Because `expected_revision` counts dispatch commits only,
  it cannot express a rebind: the session therefore carries a monotonic
  `scope_generation` that increments on every bind/rebind and enters both
  `transition_key` and the Action snapshot. Resume compares the snapshot's
  `scope_generation` / `content_identity` with the current binding and fails closed on
  mismatch, so one effect identity can never be applied to a different input set.
- **D28 — two counters: a dispatch CAS and an append watermark.** `session_revision`
  stays a **dispatch-commit-only** counter used for optimistic concurrency
  (`expected_revision`) and for operation identity. It is not a history watermark:
  findings, `external_response`, checkpoints, and control events append without moving
  it, so two snapshots at the same `as_of_revision` could denote different histories.
  Every durable append therefore also carries a kernel-assigned monotonic
  `log_sequence` (per project log, gap-free, never reused, assigned under the writer
  lease). A snapshot means "records with `log_sequence <= as_of_sequence`", which is
  stable and reproducible. Rejected: `as_of_revision` as the snapshot bound; rejected:
  timestamps as the ordering key.
- **D29 — the snapshot carries every domain input, not only dispatch bundles.**
  Findings remain standalone `MemoryRecord.finding` records
  (`sr_agent/orchestrator/loop.py`, `sr_agent/models/memory.py`), so a snapshot limited
  to `dispatch_commit` + `external_response` cannot express a finding set, cannot join
  `AnalyzerExecution` grounding, and cannot feed synthesis — while the pack is promised
  a full projection. The snapshot MUST therefore include all HMAC-verified,
  session-scoped domain inputs: standalone `finding` records, `dispatch_commit`
  bundles, and the `external_response` records the transition needs. The kernel selects
  by kind and verifies provenance; it still does not parse pack bodies.
- **D30 — the kernel computes the response digest.** `put_external_response_if_absent`
  takes `(operation_id, correlation_id, body)` and the kernel canonicalizes the body and
  computes `body_digest` itself. A caller can therefore never present bytes that
  disagree with the digest used for the equality check. Rejected: a caller-supplied
  digest trusted for lookup — an edited `approve` carrying the old `deny` digest would
  match and be accepted as the stored decision.
- **D31 — the project log is the logical union of the project's target files.**
  Storage is one JSONL per target (`EpisodicMemory._path`) and a project load merges
  them in filename order (`_all_records`), so `log_sequence` is allocated over the
  **union**, not per file. Under the project writer lease the kernel torn-tail recovers
  and verifies **every** target file of the project, takes
  `next = 1 + max(verified log_sequence)` across all of them, and only then appends to
  the chosen target. A torn tail in `Vault.jsonl` is therefore repaired before the next
  append to `Token.jsonl`. Rejected: a separate counter or reservation file (it would
  add a second crash-consistency point and can diverge from the log it describes);
  rejected: a per-file sequence (ordering across targets would be undefined).
- **D32 — snapshot order: verify, cut, scope, resolve, then select.** *(Superseded in
  part by D35/D36; the authoritative pipeline is the six steps in FR-009a.)*
  `load_for_principal` today applies corrections over the whole history
  (`_apply_supersedes`), so reusing it before cutting would let a correction at `S+1`
  retroactively change the snapshot at `S`. Two orderings are therefore wrong and both
  are rejected: **supersede-then-cut** (a later correction rewrites an older snapshot)
  and **kind-filter-then-supersede** (a correction record that is not itself an output
  kind is dropped before it can resolve anything). A correction is visible only from its
  own sequence onward, only inside its own session, and only after resolution has run.
- **D33 — capacity is two named numbers, not a promise.** The snapshot caps are fixed in
  this spec (FR-009b) so kernel and pack enforce the same value and CI can test the
  boundary. Compaction is out of scope; the limit is a stated product boundary with a
  fail-closed error.
- **D34 — the kernel pins the watermark; future values are refused.** `as_of_sequence`
  is not a free caller parameter. Under the writer lease the kernel establishes
  `current_max` (highest verified `log_sequence` across the project log) and serves the
  snapshot at that value, or at a requested value in `[0, current_max]`. A request above
  `current_max` is **fail closed**, not clamped and not served as "everything so far":
  clamping would make one watermark denote a growing history and break the immutability
  FR-009a promises. Rejected: caller-chosen future watermarks; rejected: silent clamp.
- **D35 — corrections stay project-wide (deferring to implemented `004`).** An earlier
  revision of this spec made `supersedes` session-scoped and refused cross-session
  corrections at the write path. That contradicts the shipped
  `004-memory-composition-integrity`, whose FR-007 resolves corrections across the
  principal's **whole project** and whose FR-003 explains why: a correction may live
  under a different `target` than the record it cancels, so narrowing resolution lets an
  attacker resurrect a record by damaging or isolating the file that holds its
  correction. That threat model outranks session tidiness, so this feature **adopts**
  project-wide resolution unchanged and introduces no write-path session check
  (operator decision 2026-08-22).
  Accepted consequence, which MUST be explicit and tested rather than incidental: a
  human correction filed in one session can remove a record from another session's
  projection within the same project. This is a deliberate operator capability, not a
  leak of pack state — sessions remain isolated for everything the pack writes, and only
  a `human_input` correction (`004` FR-006, verified records only) can reach across.
  Snapshot session scoping (step 6 of FR-009a) therefore selects **what the pack
  receives**; it is not a resolution boundary.
- **D36 — correction records survive the kind filter until resolution has run.**
  `supersedes` carriers are a **resolution kind**: they are retained through scoping and
  resolution regardless of the caller's requested output kinds, and only the final
  selection step narrows the snapshot to the kinds the pack receives. Filtering by kind
  before resolving would silently disable a correction whose own record kind is not in
  the output allowlist — the snapshot would look intact while an intended retraction had
  no effect. Rejected: one combined session+kind filter placed before `supersedes`.
- **D37 — `log_sequence` is a second, project-global field, not a rename of `seq`.**
  `004` already stores `seq` (0-based position **within one target file**) and
  `chain_prev` (previous record's signature) inside `fields_for_hmac`, plus a signed
  `_chain_head.json` attesting each target's chain length. Those establish *composition*
  per file; they cannot order records across targets, which is exactly what a snapshot
  watermark needs. A record therefore carries **both**: `seq` / `chain_prev` for the
  per-target chain, and `log_sequence` for the project-wide append order. `log_sequence`
  is likewise kernel-set and inside the signed shape. Rejected: reusing `seq` as the
  watermark (undefined across targets); rejected: dropping the chain in favour of a
  global counter (it would give up `004`'s removal detection).
- **D38 — a composition break withholds the snapshot too.** `004` FR-003 fails closed
  for the whole project when the chain or head does not reconstruct. A `MemorySnapshot`
  MUST honour that: no snapshot is served, no projection is built, and the operator sees
  the break on the operator channel only (`004` FR-005). Serving "the verified part"
  would hand the pack exactly the resurrected-record view `004` exists to prevent.
- **D39 — legacy records follow `004`'s decision, not a version-aware reader.** Adding
  `log_sequence` to the signed shape means pre-existing records no longer verify. `004`
  already settled this case: such records read as an empty store, a target whose records
  all fail verification and which has no head entry starts a fresh chain on the next
  write, and a re-signing migration is **rejected** because it would stamp a valid
  signature onto records whose provenance can no longer be checked. This feature adopts
  that verbatim and drops its own version-aware verification requirement (operator
  decision 2026-08-22). Consequence for this feature: a session that predates the change
  is not resumable and not projectable; it is completed and started anew.

## User Scenarios & Testing *(mandatory)*

The users are (a) the **kernel maintainer**, who must expose a pack-agnostic persist/resume contract; (b) the **pack author** (audit 004), who must not invent a parallel memory write; (c) the **security reviewer**, who must confirm provenance, isolation, and uniqueness cannot be forged by the pack or the model.

### User Story 1 - Dispatch results become kernel-authored tool_output records (Priority: P1)

A pack `dispatch` returns a `DispatchResult` with an operator-facing body and optional persistable payloads. The kernel wraps the body as DATA, stamps identity fields, and commits allowed payloads at `SourceType.tool_output`. A hostile pack that sets `source_type` or `project_id` on a payload has those fields overwritten or the commit rejected.

**Why this priority**: Without this, pack 004 cannot put stage results in signed memory without violating PackContext least privilege.

**Independent Test**: Fixture pack returns `DispatchResult` with one `dispatch_payload`; after the loop step, a durable `dispatch_commit` bundle exists with kernel-set `project_id`, `session_id`, `source_type=tool_output`, `tool=<action id>`, and the pack was not given a memory handle. A payload that forges `source_type=human_input` is not stored at that tier.

**Acceptance Scenarios**:

1. **Given** a validated domain action and a `DispatchResult` with an allowed persistable payload, **When** the loop completes the tool step, **Then** one committed bundle exists at `tool_output` with kernel-assigned identity fields, and the DATA-wrapped body is what re-enters the model.
2. **Given** a payload that includes `source_type`, `hmac`, `project_id`, or `session_id`, **When** commit runs, **Then** those fields are not honored from the pack (default **reject** the persist, still DATA-wrap a `did_not_run` / error body so the model is not fed a silent success).
3. **Given** a payload whose kernel `payload_kind` is not `dispatch_payload`, or a payload over the size cap, **When** commit runs, **Then** nothing is persisted for that transition and the operator-facing body reports the refusal.
4. **Given** `PackContext` after this feature, **When** inspected, **Then** it still has no memory write API. Read-only kernel-assigned identity (`operation_id` on the context or equivalent) MAY be present.
5. **Given** kernel source after this feature, **When** grepped for audit-domain persist kinds `stage_event` / `execution_evidence` as kernel allowlist members, **Then** there are zero matches in `sr_agent/` (they live only in the pack body schema).

---

### User Story 2 - `commit_if_absent` is unique and crash-safe (Priority: P1)

The kernel derives `operation_id` deterministically from `transition_key` before `dispatch`, so the same id is recomputed after a restart. Commit of the transition is unique relative to that id and `expected_revision`. A torn JSONL line is recovered before the next scan/append so a retry cannot be glued to a partial write and dropped as one corrupt line. A second live session cannot interleave any durable write.

**Independent Test**: Commit once; retry the same key; `dispatch` not called. Simulate a torn last line (no trailing newline); the next leased write truncates the tail, then appends a complete bundle that loads. Two processes: the second is refused at lease acquire, including `save_turn`.

**Acceptance Scenarios**:

1. **Given** a committed `operation_id` for a session, **When** the same transition is requested again, **Then** the kernel returns the stored `DispatchResult` and does not call `pack.dispatch`.
2. **Given** `expected_revision` that does not match the session's last **dispatch** revision, **When** commit is attempted, **Then** it is rejected; no payload is appended.
3. **Given** a crash after a complete line was `fsync`ed, **When** resume runs, **Then** the operation is committed and is not duplicated.
4. **Given** two processes targeting the same `project_id`, **When** the second attempts any durable write (`commit_if_absent`, `save_turn`, finding persist, `pause_checkpoint`), **Then** it is refused. They MUST NOT both append.
5. **Given** a torn last line (`{"record_id":"partial...` with no newline), **When** the lease holder next loads or appends, **Then** the tail is truncated to the last complete newline **before** scan/append; the retry writes a complete line; load returns the retry bundle and does not drop it because it was concatenated with the torn prefix.
6. **Given** a `paused_reserved` owner, **When** another `session_id` starts on the same project after heartbeat expiry and without flock, **Then** it is still refused. Steal MUST NOT apply to `paused_reserved`.
7. **Given** write of a complete bundle succeeded and `fsync` then returned an error, **When** the next process starts, **Then** if a verified complete line exists it is treated as committed (not a new operation). If no verified line exists, the operation is uncommitted and may re-compute.

---

### User Story 3 - Session restore binds worktree identity; paused turns resume from one checkpoint (Priority: P1)

A `pause_checkpoint` records continuation fields, new session status, lease mode
`paused_reserved`, and the system-prompt body. Resume from another working directory
restores scope. `resume_turn` loads that checkpoint and continues the same loop phase.
Old snapshots that lack `scope_root` MUST NOT be resumed as if `Path(".")` were the target.

**Why this priority**: Pack 004 US1 / US4 are kernel obligations. Two JSONL writes cannot
be an atomic pause. In-memory `last_tool_output` and CLI `agent_action=None` cannot resume
a turn that already spent tool calls.

**Independent Test**: After two successful tool calls, simulate `blocked_local_unavailable`;
assert **one** `pause_checkpoint` record contains `tool_calls_used>=2`, status
`blocked_local_unavailable`, lease `paused_reserved`, and prompt body; `resume_turn`
continues without a second session snapshot write being required for consistency. Crash
the process after that single record: `load_session` reports paused, not `active`. Dirty a
tracked file; worktree digest mismatches; resume fails.

**Acceptance Scenarios**:

1. **Given** a checkpoint with `scope_root` and content identity, **When** resume runs from another cwd, **Then** containment is measured against the recorded root, not cwd.
2. **Given** a missing root, or a root whose worktree digest no longer matches, **When** resume runs, **Then** it fails with an explicit reason and does not substitute another directory. A dirty or untracked file that is inside the digest set MUST change the digest even if HEAD SHA is unchanged.
3. **Given** `paused_relay` with a filed relay response and a persisted `pause_checkpoint`, **When** `resume_turn` runs, **Then** the response is ingested at `external_llm_output` from the continuation phase, committed dispatch operations are not duplicated, and the original `user_message` is not started as a new `run_turn`.
4. **Given** `blocked_local_unavailable` after N tool calls, **When** resume runs with the model available, **Then** the session continues from the checkpoint (`budget used = N`, last DATA body restored, system prompt loaded from the trusted registry after hash check), without falling back to relay, without restarting committed operations, and without re-asking the model as if no tools had run. If the registry lacks that prompt id/hash, resume fails closed.
5. **Given** a pre-this-feature snapshot with no `scope_root` / no checkpoint, **When** methodology-capable or in-turn resume is requested, **Then** it is refused explicitly. It MUST NOT bind `Path(".")` and MUST NOT heuristically rebuild the loop.
6. **Given** any pause point, **When** the loop returns paused status to the caller, **Then** a single `pause_checkpoint` was already fsync'd. Returning paused without that record is a spec violation. A subsequent `ChatSession` snapshot, if any, MUST NOT be required to interpret the pause.
7. **Given** a crash after `pause_checkpoint` and before any later snapshot, **When** `load_session` runs, **Then** status and continuation come from that checkpoint (paused, not `active`).

---

### User Story 4 - ChatSession schema is versioned and compatible (Priority: P2)

Existing signed `payload_kind=chat_session` records remain verifiable. New fields are additive. Migration rules are explicit: no silent default of cwd; no HMAC bypass.

**Independent Test**: Load a fixture snapshot shaped like today's `ChatSession`; HMAC verification still works. A new snapshot round-trips `scope_root`, worktree digest, `session_revision`, writer lease owner, and continuation reference. Resume policy for old snapshots matches US3 scenario 5.

**Acceptance Scenarios**:

1. **Given** an old `chat_session` payload, **When** loaded, **Then** HMAC verification uses the signed field set of that record (no rewrite-in-place that would invalidate signatures).
2. **Given** a new snapshot or checkpoint, **When** saved and loaded, **Then** `scope_root`, content identity, `session_revision`, lease mode/owner, and continuation fields round-trip.
3. **Given** documentation of the migration, **When** a pack asks whether it may invent `scope_root` at resume, **Then** the answer is no — only kernel restore from the checkpoint/snapshot or explicit fail.

---

### User Story 5 - Chat and batch share one kernel execution path (Priority: P1)

A fixture pack action run from a chat loop and from a batch helper both go through
`KernelActionExecutor.execute`. Neither path calls `EpisodicMemory.write` from pack or
pipeline code. Forged identity and a second writer are rejected identically.

**Independent Test**: Grep/architecture test: `audit_agent/pipeline.py` and
`planner/stage2.py` (or kernel fixture equivalents) have zero `memory.write` after this
feature. A hostile payload forging `source_type` is rejected on both the loop driver and
the batch driver.

**Acceptance Scenarios**:

1. **Given** the same validated action, **When** executed via loop and via batch adapter, **Then** both produce a kernel-stamped `dispatch_commit` and neither imported a memory write API into pack orchestration.
2. **Given** a second session holding `paused_reserved` or `active_process`, **When** batch `execute` runs for that project, **Then** it is refused the same way chat is.
3. **Given** oversize / unknown kernel payload kind, **When** either surface executes, **Then** persist is refused and the caller does not see `status=ran` for that persist.

### Edge Cases

- **`002` observability**: if `002-memory-write-path` has landed, successful kernel appends emit `memory_write`. This feature MUST NOT be blocked on 002.
- **Finding persist path**: model-reported `AgentAction.finding` remains kernel `persist_finding` (today, before dispatch). This feature does not fix kernel FR-008a. Pack 004 MUST NOT treat those findings as tool-grounded. Finding persist still requires the writer lease (D16) and MUST go through a kernel function, not pack `memory.write`.
- **Opaque pack body**: the kernel does not parse `AnalyzerExecution`. Pack 004 defines that schema. The kernel only enforces `dispatch_payload` + size caps + identity stamps on the envelope. Envelope `operation_id` is the join key for projections.
- **Lease**: `paused_reserved` never times out into steal. Forgotten paused sessions need `abandon_session` or human `takeover_lease`. `crashed_active` steal is only when last durable lease mode was `active_process`.
- **Out of scope**: distributed locks across hosts; reconstructing in-turn model **token streams**; giving packs `EpisodicMemory`; making `read_only` mean "no durable record"; SQLite.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `CapabilityPack.dispatch` MUST return `DispatchResult` (structured), not an opaque `str` as the persistable contract. A temporary adapter MAY wrap legacy string-only fixture packs in tests; production packs used with this feature MUST return `DispatchResult`.
- **FR-002**: `DispatchResult` MUST contain: `status` (`ran` / `did_not_run` /
  `timeout` / `unavailable` / `error` / `pending`), operator-facing `body` (DATA-wrapped
  by the kernel), `payloads` (empty when `pending`), and when `status=pending` a
  `pending` object `{kind, correlation_id}` where `kind` is the closed kernel enum
  `external_response` / `human_confirmation` / `local_model_retry` (D19). An unknown
  `kind` MUST be rejected as `error` **before** any checkpoint is written. `ran` is
  dispatch completion, not analyzer grounding. `pending` MUST NOT be persisted as a
  completed `dispatch_commit`.
- **FR-002a**: Executor pending protocol (D19). If `dispatch` returns `pending`:
  1. Do not `commit_if_absent` stage payloads.
  2. Persist one `pause_checkpoint` including `pending`, `operation_id`,
     `transition_key`, the **Action snapshot** (FR-010c), and continuation fields
     (FR-010a).
  3. Return paused to the caller (`paused_relay` when `kind=external_response`,
     `paused_confirmation` when `kind=human_confirmation`,
     `blocked_local_unavailable` when `kind=local_model_retry`).
  On resume: load the checkpoint; ingest the external/human/model result as a durable
  DATA record (FR-002b); rebuild the Action from the snapshot (FR-010c); call
  `pack.dispatch` again with the **same** `operation_id` / `transition_key` and a
  read-only response reference on `PackContext`. Commit payloads only if the second
  return is not `pending`. A repeated `pending` for the same `operation_id` is allowed
  only when `correlation_id` is unchanged (idempotent wait).
- **FR-002b**: External-response durability (D24). Before re-dispatch, the kernel MUST
  persist the ingested body as one record (`payload_kind=external_response`,
  `correlation_id`, `operation_id`; tier `external_llm_output` for relay,
  `human_input` for an operator confirmation decision). Resume reads that record, not
  the source file. Ingestion MUST NOT delete or move the source artifact. A crash
  after ingest and before commit MUST leave the response readable on the next resume.
- **FR-002c**: Ingestion idempotency (D25 / D30). The kernel MUST expose
  `put_external_response_if_absent(operation_id, correlation_id, body)`,
  callable only from `KernelActionExecutor`, under `active_process`, using the FR-007a
  protocol. The signature MUST NOT accept a caller-supplied digest. Semantics:
  1. Canonicalize `body` (FR-005a encoding) and compute `body_digest` **inside the
     kernel**. The stored record's digest is always the digest of its own stored bytes.
  2. Torn-tail recover, then scan for a verified record with this
     `(operation_id, correlation_id)`.
  3. Found, and the **recomputed** digest of the stored record equals the computed
     digest of the incoming body → return the stored record; do not append.
  4. Found with a different digest → **fail closed** with an explicit conflict
     (no append, no overwrite, no rival record, no preference for the file).
  5. Absent → append one record (carrying its digest and `log_sequence`), `flush`,
     `fsync`.
  Equality MUST be decided on digests the kernel computed from bytes it holds, never on
  a value the caller asserted. After a successful put, the durable record is the sole
  source of truth for that key and the source file MUST NOT be re-read for it. A
  `human_confirmation` decision is therefore immutable once ingested. Tests MUST
  include: (a) ingest → kill → mutate the source file → resume (conflict reported, the
  stored decision stands); (b) a **hostile caller** that passes an edited `approve`
  body while asserting the previous `deny` digest through any available path — it MUST
  NOT match the stored record, and no record may exist whose digest field disagrees
  with its own body.
- **FR-003**: Persistable pack payloads MUST use kernel `payload_kind=dispatch_payload` only. The pack MAY put a discriminator inside `body` (opaque JSON to the kernel). Each payload `body` MUST be JSON-serializable and MUST NOT exceed **8192** bytes encoded; the list MUST NOT exceed **32** items. Oversize → refuse persist of the whole transition (FR-008). Kernel MUST NOT allowlist audit-domain kinds (`stage_event`, `execution_evidence`, `tool_result`).
- **FR-004**: The kernel MUST assign `project_id`, `session_id`, `source_type=tool_output`, `tool` (the action id), `operation_id`, and envelope `record_id` itself. Pack-supplied values for those fields MUST be rejected. Nested pack objects do **not** receive per-item `record_id`s; packs MUST NOT require `result_record_id` (D14).
- **FR-005**: Operation identity MUST be deterministic (D22). The kernel first derives
  `transition_key` from `session_id` + action id + **canonical digest of validated
  action params** (the `ValidationResult`-approved params, including analyzer/config
  identity) + caller chunk identity + `expected_revision` + **`scope_generation`**
  (D27), then sets `operation_id = UUIDv5(kernel_namespace, transition_key)`. It MUST
  NOT mint a random UUID per attempt. Recomputing the same transition in a **new
  process** MUST yield the same `operation_id`. Lookup is by `transition_key` and/or
  `operation_id`.
- **FR-005a**: Canonical encoding (closes the "sorted JSON" ambiguity). The spec MUST
  fix, and tests MUST pin: a constant protocol **UUID namespace** (a literal in kernel
  source, versioned with the protocol, never derived at runtime); UTF-8 output with
  **NFC** Unicode normalization of keys and string values; keys sorted by Unicode code
  point; separators without spaces (`,` / `:`); no non-finite floats; integers not
  rendered as floats; **absent vs `null` distinguished** (an absent optional key is
  omitted, an explicit `null` is emitted); a version tag inside the digest input so a
  future encoding change cannot silently collide. A **golden vector** (params → canonical
  bytes → `transition_key` → `operation_id`) MUST be reproduced by a freshly started
  process in CI. Changing the encoding is a protocol version bump, not a refactor.
- **FR-006**: The kernel MUST expose `commit_if_absent` only as a callee of `KernelActionExecutor` (FR-018), not as a pack-facing API. Semantics:
  1. Require the caller already holds `active_process` (FR-014). Failure → refuse, no append.
  2. Torn-tail recover the target JSONL (FR-007a).
  3. If a verified bundle for this `operation_id` or `transition_key` exists → return it; do not append; do not call `dispatch` when this check runs *before* dispatch.
  4. If `expected_revision` != session's last **dispatch** revision → refuse, no append.
  5. Else append **one** HMAC-signed `MemoryRecord` (`payload_kind=dispatch_commit`) whose payload is the whole bundle, then `flush` + `fsync`.
  6. Advance `session_revision` (dispatch counter only) **and** assign the next
     `log_sequence` (FR-006a).
- **FR-006a**: Append watermark (D28 / D31 / D37). Every durable append for a project —
  including `dispatch_commit`, standalone `finding`, `external_response`,
  `pause_checkpoint`, `save_turn`, and operator control events — MUST carry a
  kernel-assigned monotonic `log_sequence`, covered by the record HMAC. This is a
  **new field alongside** `004`'s per-target `seq` / `chain_prev`, not a replacement:
  `seq` remains the position inside one target file and keeps the composition chain and
  `_chain_head.json` semantics intact, while `log_sequence` orders the project globally.
  Both are kernel-set and inside `fields_for_hmac`; both are stripped by
  `for_llm_context` (`004` FR-008) so no ordering or signature material reaches the
  model.
  `session_revision` MUST remain the dispatch-commit-only CAS counter
  (`expected_revision`, operation identity) and MUST NOT be used as a history watermark.
  Allocation is defined over the **project log = logical union of every
  `memory/<project_id>/*.jsonl`**, not per target file:
  1. Hold the project writer lease (`active_process`).
  2. Torn-tail recover (FR-007a) and HMAC-verify **all** target files of the project,
     including targets named by the head with no file on disk.
  3. Validate the observed sequence set: it MUST be a contiguous run with no duplicates
     and no gaps among verified records. A duplicate or a gap is **fail closed** (the
     log is already inconsistent; continuing with `1 + max` would extend the damage) and
     is reported with the offending values.
  4. `next = 1 + max(log_sequence of all verified complete records)`; an empty project
     starts at 1.
  5. Append the record to its own target file, `flush`, `fsync`.
  A crashed partial append in one target MUST be repaired before the next append to any
  other target, so a torn tail in `Vault.jsonl` cannot corrupt or duplicate the sequence
  used by `Token.jsonl`. Sequences are gap-free per project, never reused, and two
  records MUST NOT share one. The kernel MUST NOT introduce a separate counter or
  reservation file (a second crash-consistency point that can diverge from the log).
  Tests MUST include a restart with **two alternating target files** where the first has
  a torn append before the write to the second.
- **FR-006b**: Legacy records follow `004-memory-composition-integrity` (D39). Adding
  `log_sequence` to `fields_for_hmac` changes the signed shape, so pre-existing records
  stop verifying and read as an **empty store** — the behaviour `models/memory.py`
  already documents for any change to the signed shape. A target whose records all fail
  verification and which has no head entry MUST start a fresh chain on the next write
  (`004` `test_MI014`), so the store is not bricked. A re-signing migration is
  **rejected** (it would authenticate records whose provenance can no longer be
  checked). This feature MUST NOT add version-aware verification, MUST NOT invent
  sequence numbers for old records, and MUST NOT re-sign anything. A session created
  before this change is therefore not resumable and not projectable; the operator
  completes it and starts a new one, and resume of such a session fails closed with an
  explicit message (FR-015).
- **FR-007**: `EpisodicMemory.write` remains append-only of complete records and MUST NOT be called from pack or batch pipeline code (FR-018). Uniqueness MUST NOT be in-place rewrite of verified lines. HMAC drops corrupt **complete** lines; it is not a substitute for FR-006 or FR-007a.
- **FR-007b**: `supersedes` scoping is unchanged from `004` (D35). Resolution runs over
  the principal's whole project (`004` FR-007) and MUST NOT be narrowed to a session or
  a target. This feature MUST NOT add a write-path `session_id` equality check on
  corrections and MUST NOT filter corrections out by session before resolving. The
  cross-session effect (a `human_input` correction in one session removing a record from
  another session's projection in the same project) is an accepted operator capability
  and MUST be covered by an explicit test rather than left implicit (SC-014g).
- **FR-007c**: Composition break withholds the snapshot (D38). If `004`'s chain or
  `_chain_head.json` does not reconstruct for the project, the kernel MUST NOT serve a
  `MemorySnapshot`, build a projection, or dispatch a transition that depends on one.
  The failure is reported on the operator channel only, never as model context
  (`004` FR-005). Serving the still-verifying subset is prohibited.
- **FR-007a**: Crash-safe log protocol (D15). Under `active_process`, before load-scan or append of a project JSONL file: if the file is non-empty and does not end with `\n`, truncate to the last `\n` (or to empty). Then any new record is written as `json + "\n"`, `flush`, `fsync` (file; directory fsync when the platform allows). Tests MUST include: (1) glued-line fixture; (2) write-completed / `fsync`-failed kill point — next start treats a verified complete line as committed.
- **FR-008**: If append/`fsync` fails **and** no verified complete bundle for that `operation_id` exists, the operation is uncommitted. If a verified complete line exists despite `fsync` error, it is committed (D12). The operator-facing `DispatchResult.body` MUST NOT show `status=ran` with no committed bundle when payloads were requested.
- **FR-009**: `PackContext` MUST remain without a memory write handle. The kernel MAY add read-only `operation_id` / `transition_key` on the context. Pack bodies MUST NOT be trusted for `operation_id`; projections use the envelope.
- **FR-009a**: Read snapshot — the only pack read seam (D26). `KernelActionExecutor`
  MUST be able to pass the pack an immutable `MemorySnapshot` **argument** (not a field
  on `PackContext`, not a handle, not a callable):
  1. Contents (D29): **all** HMAC-verified, session- and project-scoped domain inputs
     needed for a full projection — standalone `finding` records, `dispatch_commit`
     bundles, and the `external_response` records the transition needs. A snapshot of
     dispatch bundles alone is insufficient, because findings are separate records
     (`sr_agent/models/memory.py`) and grounding is a finding ↔ `AnalyzerExecution`
     join.
  2. Bound (D28 / D34): records with `log_sequence <= as_of_sequence`, where
     `as_of_sequence` is the append watermark (FR-006a), **not** `session_revision`.
     The watermark is **pinned by the kernel under the writer lease**, never chosen
     freely by the caller: the kernel reads the current verified maximum `current_max`
     and either issues the snapshot at exactly that value or accepts a requested
     `0 <= as_of_sequence <= current_max`. A **future** watermark
     (`as_of_sequence > current_max`) MUST be rejected fail-closed; it MUST NOT be
     served as "everything so far", because later appends would silently change the
     contents of that same watermark. Within these rules the same `as_of_sequence`
     always yields the same snapshot contents.
  2a. Evaluation order (D32 / D35 / D36). This six-step pipeline is authoritative and
     mandatory in this order:
     1. HMAC-verify raw records across the project's target files and check `004`'s
        composition chain / head. A break withholds the whole project (FR-007c).
     2. Cut the prefix `log_sequence <= as_of_sequence`.
     3. Retain **all** correction-bearing (`supersedes`) records and all candidate
        domain inputs of the project, regardless of the requested output kinds (D36).
     4. Apply `supersedes` across that prefix **project-wide**, as `004` FR-007 requires
        (D35). Resolution is not narrowed by target and not narrowed by session.
     5. Filter by `session_id`.
     6. Apply the **final kind selection** for what the pack receives.
     The snapshot MUST NOT reuse a loader that applies corrections over the full history
     before cutting (`_apply_supersedes` on `_all_records`): a correction at `S+1` would
     retroactively alter the snapshot at `S`. Kind selection follows resolution so a
     correction is never disabled merely because its own record kind is not an output
     kind (D36). Session selection also follows resolution: it decides what the pack
     receives, not what a correction may cancel (D35). A correction is therefore visible
     from its own sequence onward, across the project.
  3. Shape: frozen, fully materialized, ordered by `log_sequence`; each item exposes
     envelope identity (`record_id`, `log_sequence`, `operation_id`, `source_type`,
     timestamp) plus the opaque body. The kernel MUST NOT parse pack bodies; the pack
     MUST NOT receive unverified or cross-session records.
  4. Size: item count and total bytes are capped; exceeding the cap fails closed rather
     than silently truncating (see FR-009b).
  5. Provenance: snapshot bodies re-enter the model only as DATA (FR-017); the snapshot
     is an input to a pure reducer, never an instruction source.
  A pack MUST be able to build its full domain projection from this snapshot alone, so
  that FR-018a stays satisfiable without a second orchestration path.
- **FR-009b**: Snapshot capacity (D33). The limits are **fixed here** so kernel and pack
  enforce identical values:
  - **`MAX_SNAPSHOT_ITEMS = 10000`** records after filtering (step 2a.iv).
  - **`MAX_SNAPSHOT_BYTES = 33554432`** (32 MiB) of canonical encoded bytes (FR-005a),
    counted over the bodies plus envelope identity fields actually handed to the pack.
  Whichever is reached first is the binding cap. Exceeding either is a fail-closed error
  that names the limit, the measured value, and the remedy (`complete_session`, then
  continue in a new session); it MUST NOT silently truncate, sample, or drop the oldest
  records. Both constants live in kernel code as the single source of truth; the pack
  MUST NOT define its own. CI MUST cover four boundary cases: item count at `MAX` and at
  `MAX + 1`, and canonical byte count at `MAX` and at `MAX + 1`, independently.
  This feature does **not** implement compaction; a durable projection-checkpoint /
  compaction design is deferred to a follow-up in the pack `005` timeframe.
- **FR-010**: Durable session identity lives on `pause_checkpoint` and on
  `complete` / `abandon` / `detach` records, not on an independent pause-time
  `ChatSession` write. Projected `ChatSession` MUST include: canonical `scope_root`,
  `content_identity` (FR-013a), `session_revision`, `writer_session_id`, `lease_mode`,
  continuation fields. `SessionStatus` MUST include `completed`, `abandoned`, and
  `detached` in addition to today's pause/active set.
- **FR-010a**: Continuation field set (embedded in `pause_checkpoint`): `turn_id`;
  `phase`; `user_message`; `system_prompt_id`; `system_prompt_hash`;
  `system_prompt_version` (optional); MUST NOT include `system_prompt_body` (D18);
  `last_dispatch_operation_id`; `pending` (nullable); `last_tool_body_ref`;
  `tool_calls_used`; `pending_relay_request_id` / confirmation id as copies of
  `pending.correlation_id` when applicable; `expected_session_revision`;
  `session_status`; the **Action snapshot** (FR-010c) whenever a dispatch is in
  flight or pending. `resume_turn` restores the loop from this checkpoint and loads
  prompt bytes from the trusted registry (hash check). Registry miss → fail closed.
- **FR-010c**: Action snapshot (D23). When the checkpoint covers an in-flight or
  pending dispatch it MUST store `action_type`, **canonical validated params** (the same
  canonical form the digest is taken over, FR-005a), `pack_id`,
  `pack_contract_version`, and the binding fields `scope_generation` +
  `content_identity` (D27). On resume the kernel MUST: compare the snapshot's
  `pack_id` / `pack_contract_version` against the **pack supplied by the composition
  root** and the snapshot's scope binding against the current one; rebuild the Action
  from the snapshot; re-derive the params digest and match it against `transition_key`;
  only then re-dispatch. Any mismatch → **fail closed** with an explicit message. The
  kernel MUST NOT search for, download, or otherwise resolve an alternative pack
  version at runtime: there is no dynamic pack registry (Constitution III); the active
  pack is wired at the composition root and a version mismatch is an operator-fixed
  condition. The kernel MUST NOT ask the model to restate the action and MUST NOT infer
  it from `user_message`. A `human_confirmation` pending resolves through this same
  snapshot: `correlation_id` alone MUST NOT authorize execution.
- **FR-010b**: Pause atomicity. For loop pauses (`paused_confirmation`, `paused_relay`,
  `blocked_local_unavailable`) **and** for `DispatchResult.status=pending`: persist
  **one** `pause_checkpoint` then drop flock then return. MUST NOT write a second
  JSONL record to make the pause visible. `load_session` uses the latest verified
  checkpoint / complete / abandon / detach.
- **FR-011**: `OrchestratorLoop` MUST provide `resume_turn` that loads the latest `pause_checkpoint` and continues that phase after `reacquire_lease` for the **same** `session_id`. It MUST NOT call `run_turn(user_message)` as a substitute. Committed `operation_id`s MUST NOT be duplicated.
- **FR-012**: Resume MUST restore `scope_root` from the checkpoint. The kernel MUST NOT default missing `scope_root` to `"."`.
- **FR-013**: Resume MUST re-verify content identity (FR-013a). Mismatch or missing path → fail closed.
- **FR-013a**: Content identity algorithm over the bound `ContentScopePolicy` (D20):
  1. Composition root supplies `runtime_state_roots` (absolute paths). Pack supplies
     include globs/prefixes relative to `scope_root`. Kernel MUST NOT hardcode
     relay/report directory names.
  2. Walk included paths under `scope_root` (no symlink follow; hash symlink as
     path + target). Skip any path under a `runtime_state_root`, `.git`, or
     `__pycache__`.
  3. **Budget**: fail closed if file count > **10000** or total hashed bytes >
     **100 MiB** or any single file > **8 MiB**.
  4. Sort by relative POSIX path. Digest = SHA-256 over `path + NUL + size + NUL +
     raw bytes` (no mtime, no mode).
  5. Optional git `remote_url` + `head_sha` as metadata. Resume compares digest first.
- **FR-013b**: Read enforcement (D20). The bound include set is a **security
  boundary**, not a hashing optimisation. `read_file` and `search_code` MUST refuse
  any path that is contained in `scope_root` but outside the bound include set (same
  fail-closed shape as the traversal guard), and MUST refuse anything under
  `runtime_state_roots`. `search_code` MUST enumerate only include-set files rather
  than walking the passed root. The analyzer sandbox MUST receive a read-only mount /
  manifest limited to that same set, and the target digest a pack records for an
  execution MUST be computed over the inputs actually handed to the analyzer.
  Broadening the set (for example newly discovered imports) requires an explicit
  operator **scope rebind** (FR-013c); it MUST NOT happen implicitly inside a turn.
- **FR-013c**: Scope rebind in the state machine (D27). The session MUST carry a
  monotonic `scope_generation`, set at first bind and incremented by every rebind.
  `rebind_scope` is an operator control API (never model-facing, never pack-callable,
  FR-019) that:
  1. Is legal **only at a completed-turn boundary**. With a pending or in-flight
     operation it MUST be refused, naming the operation; the operator either lets it
     finish or ends the session with the existing `abandon_session` API. This feature
     MUST NOT introduce a per-operation `abandon_operation`. Rebind MUST NOT be
     auto-converted into a pause.
  2. Records one control event (`source_type=human_input`) with the previous and new
     `scope_generation`, include set, and `content_identity`.
  3. Takes effect only from the next transition: `scope_generation` is part of
     `transition_key` (FR-005), so a post-rebind transition has a different
     `operation_id` and can never reuse a pre-rebind effect identity.
  Resume MUST compare the checkpoint's `scope_generation` and `content_identity` with
  the current binding before re-dispatch and fail closed on mismatch. `expected_revision`
  (dispatch-only counter) MUST NOT be relied on to detect a rebind.
- **FR-014**: Writer lease state machine (D11):
  - **Owner**: `session_id`.
  - **`active_process`**: flock + heartbeat. Process death without
    complete/abandon/detach/pause_checkpoint → derived `crashed_active` after
    heartbeat window; steal allowed (sequential).
  - **`paused_reserved`**: set only by `pause_checkpoint`. No timeout-steal. Other
    `session_id` refused. Same `session_id` → `reacquire_lease` then `resume_turn`.
  - **`detached`**: `detach_session` releases the lease, keeps history. Same
    `session_id` MAY later `reacquire_lease` to continue or inspect. Other
    `session_id` MAY acquire a **new** session on the project (sequential writer).
    REPL EOF MUST `detach_session`, not `complete_session`. `detach_session` is
    legal **only at a completed-turn boundary**: with an in-progress or pending turn
    it MUST either be refused or first persist a `pause_checkpoint` and land in
    `paused_reserved` (never silently drop pending state).
  - **`completed` / `abandoned`**: explicit APIs; lease released; same `session_id`
    MUST NOT reacquire as writer (history still readable).
  - **`takeover_lease`**: human-approved; moves `paused_reserved` to `abandoned`.
  - **Coverage**: durable appends for the project while `active_process` or
    `paused_reserved` belong to the owner or are refused.
- **FR-018**: `KernelActionExecutor.execute(pack, session, action) -> DispatchResult`
  is the **only** production path that calls `pack.dispatch` for domain actions and
  the only path that `commit_if_absent`s their payloads or writes a pending
  `pause_checkpoint` for dispatch. `OrchestratorLoop` and the batch adapter MUST
  both call it.
- **FR-018a**: Architecture invariant (D21). A production-package AST/import-graph
  test MUST fail if the active pack's production package (for this pairing:
  `audit_agent/`, excluding `tests/` and `**/test_*.py`)
  imports `EpisodicMemory`, constructs `MemoryRecord` for append, or calls
  `.write(` on a memory object. Allowlist of production exceptions: **empty**. The
  same test family MUST fail if any production caller of `CapabilityPack.dispatch`
  is not `KernelActionExecutor`. A new helper module MUST fail this test without
  editing the test's file list.
- **FR-018b**: Runtime companion to FR-018a (D21). A hostile-pack runtime test MUST
  show that a memory-like object handed to pack code through an alias, attribute,
  duck-typed helper, or `PackContext` extension still cannot append a durable record:
  the write API is not reachable from the pack process/objects, not merely
  un-imported in source.
- **FR-019**: Kernel session APIs MUST include `complete_session`, `abandon_session`,
  `detach_session`, `reacquire_lease(session_id)`, `takeover_lease`, and
  `rebind_scope`. Packs MUST
  NOT implement a parallel lease file. `takeover_lease`, `abandon_session`,
  `complete_session`, and `rebind_scope` MUST NOT appear in the model-facing action vocabulary and MUST
  NOT be invocable by a pack; each is an operator-authorized control operation and is
  recorded as an explicit control event (actor = operator, `source_type=human_input`).
- **FR-020**: Trusted prompt registry (D18). Resume and `run_turn` load system prompt
  bytes only from that registry. Checkpoint prompt fields are a reference. The
  registry is **kernel-owned as a mechanism and pack-owned as content**: the pack ships
  and versions its own prompt text through registry registration; kernel source MUST
  NOT contain audit-domain prompt bodies (Constitution III). An MI test MUST show that
  a checkpoint whose `system_prompt_hash` is attacker-controlled DATA is **not** used
  as the system instruction (hash mismatch or non-registry bytes → fail closed).
- **FR-021**: Effect ports invoked from the executor that allocate external
  correlation ids (relay request files, confirmation ids) MUST derive those ids
  deterministically from `transition_key` / the derived `operation_id` (FR-005), so
  the id exists identically before and after a restart and a crash after the effect
  and before the checkpoint does not create a second request. Persist-then-reuse of a
  random id is **not** an acceptable substitute, because the id is not durable at the
  moment the effect happens. Ports MUST be able to recognise their own prior effect
  (existing request file with the same correlation id → adopt it, do not re-create).
  Pure reducer compute and analyzer subprocesses MAY be at-least-once. Paid/hosted
  model calls SHOULD pass the same `operation_id` as an idempotency key when the
  provider supports it; otherwise at-least-once cost is recorded as accepted.
- **FR-015**: Compatibility: old snapshots remain HMAC-verifiable under version-aware verification (FR-006b). New required fields — including `log_sequence` — MUST NOT be silently invented at load, and old records MUST NOT be re-signed in place. Resume of an old snapshot without `scope_root` / checkpoint MUST fail explicitly.
- **FR-016**: This feature MUST NOT intercept `write_memory` (that is 002). It MUST NOT add OOB confirmation to `read_only` dispatch persist. `takeover_lease` is operator CLI, not Constitution II confirmation for `write_execute`.
- **FR-017**: Existing MI-resistance and hostile-pack guarantees MUST be preserved. Pack-controlled payload bodies AND checkpoint fields that re-enter the model (`user_message`, last tool body), plus every `MemorySnapshot` body and `external_response` body, re-enter as DATA. System prompt bytes MUST NOT come from those artifacts (D18 / FR-020).

### Key Entities

- **`DispatchResult`**: kernel-owned return type: `status` (including `pending`), `body`, `payloads`, optional `pending`.
- **`dispatch_payload`**: sole pack persistable kind; `body` opaque to the kernel.
- **`dispatch_commit`**: completed-transition envelope (not used for `pending`).
- **`KernelActionExecutor`**: sole production path `execute` (FR-018).
- **Trusted prompt registry**: non-episodic store of system prompt bytes; kernel mechanism, pack content (D18 / FR-020).
- **Writer lease**: `active_process` / `paused_reserved` / `detached` / `completed` / `abandoned` / derived `crashed_active`.
- **`pause_checkpoint`**: single-record pause SoT (continuation + status + `paused_reserved` + prompt **id/hash** + **Action snapshot**, never prompt body).
- **Action snapshot**: `action_type` + canonical validated params + `pack_id` + `pack_contract_version` + `scope_generation` + `content_identity`; the only source for re-dispatch after restart (D23 / D27).
- **`ContentScopePolicy`**: resolved include set + `runtime_state_roots`; binds digest, kernel read tools, and analyzer sandbox mount (D20). Versioned by `scope_generation`.
- **`external_response` record**: durable DATA copy of a relay/confirmation body, put-if-absent on `(operation_id, correlation_id)` with a kernel-computed digest; the record wins over the file (D24 / D25 / D30).
- **`MemorySnapshot`**: frozen, verified, session-scoped, `as_of_sequence`-bounded list of findings + dispatch bundles + external responses passed to the pack as an argument; watermark pinned by the kernel, corrections applied within the session only and before final kind selection; the only pack read seam (D26 / D29 / D34 / D35 / D36).
- **`log_sequence`**: kernel-assigned monotonic append watermark over the **project log** (logical union of all target JSONL files), on every durable record; the snapshot bound (D28 / D31). Distinct from `session_revision`, which stays the dispatch-only CAS counter.

## Success Criteria *(mandatory)*

- **SC-001**: `KernelActionExecutor.execute` yields a kernel-authored `dispatch_commit` when status is not `pending`; `PackContext` has no memory write API.
- **SC-002**: Retrying a committed `transition_key` does not call `dispatch` again. Write-then-failed-fsync still treats a verified line as committed.
- **SC-003**: Two concurrent processes cannot both write. `paused_reserved` blocks another `session_id` after flock/heartbeat death. `detached` releases the lease. Same `session_id` can reacquire from `paused_reserved` or `detached`.
- **SC-004**: `expected_revision` mismatch refuses dispatch commit.
- **SC-005**: Forged pack identity fields never land at `human_input`, on loop and batch.
- **SC-006**: Resume restores `scope_root`; digest uses the bound include set; over-budget fails closed; no `"."` default. `read_file` / `search_code` refuse an in-`scope_root` file that is outside the include set and anything under `runtime_state_roots`; `search_code` enumerates only include-set files (FR-013b).
- **SC-006a**: Scope-integrity scenario test: with include set `contracts/**`, mutating `script/Deploy.sol` after detach MUST NOT leave a readable-but-undigested file — either the read is refused, or the file is in the digest and resume fails closed. Widening requires an explicit scope rebind that records a new digest.
- **SC-007**: Pause paths persist one checkpoint; crash loads paused; `resume_turn` loads prompt from the **registry** (not checkpoint body); missing registry version fails closed; tool-call budget is preserved.
- **SC-008**: Old snapshots verify; resume without checkpoint fails explicitly. `complete` / `abandon` / `detach` / `takeover` match FR-014, are absent from the model action vocabulary, and are recorded as operator control events. `detach_session` during a pending turn refuses or checkpoints. REPL EOF at a turn boundary detaches.
- **SC-009**: Oversize / wrong kind refuses persist; `pending` is not reported as `ran`; an unknown `pending.kind` is rejected **before** any checkpoint is written.
- **SC-010**: Glued torn-line fixture requires FR-007a.
- **SC-011**: Package-wide architecture test (FR-018a) plus runtime hostile test (FR-018b): empty production allowlist; only executor calls `dispatch`; an aliased/untyped memory object still cannot append. Adding a new `checkpoint.py`-style helper fails without editing the test inventory.
- **SC-012**: Full-restart idempotency. `status=pending` + `kind=external_response` writes a checkpoint without a completed bundle. Kill the process **after** the relay request file is created and **before** the checkpoint is written; a fresh executor that re-derives the transition from scratch MUST mint the same `operation_id` / correlation id (FR-005), adopt the existing request, and MUST NOT create a second one. Resume then ingests the response as a durable record and commits exactly once. A test that pre-seeds `operation_id` is not sufficient: the minting path itself must be restarted.
- **SC-012a**: Action-snapshot resume. After restart, re-dispatch is rebuilt from the checkpoint snapshot (digest re-matched), with no model call to restate the action; an absent/incompatible `pack_contract_version` fails closed. A `human_confirmation` approval carrying only a correlation id does not execute anything.
- **SC-012b**: Response durability. Crash after ingesting the external response and before the final commit leaves the response readable on the next resume; the source artifact is not deleted or moved by ingest.
- **SC-012c**: Ingestion idempotency (FR-002c). Ingest → kill → **mutate the source file** → resume: the second ingest hits the existing `(operation_id, correlation_id)` record, an equal body is reused without a second append, and a different body fails closed. A `human_confirmation` `deny` edited to `approve` on disk after ingestion does not execute. Hostile case: a caller presenting an edited body while asserting the prior digest cannot match the stored record, because the kernel computes both digests from bytes it holds (FR-002c / D30); no stored record's digest disagrees with its own body.
- **SC-014**: Read seam (FR-009a). A pack builds its full projection from the passed `MemorySnapshot` alone: no memory import, no second JSONL reader, architecture test still green. The fixture MUST contain a **standalone persisted `finding` record** plus one `AnalyzerExecution` that lists it and one that does not, and the reducer MUST reach grounded / ungrounded correctly from the snapshot only. The snapshot excludes other sessions and anything above `as_of_sequence`, rejects unverified records, and fails closed over its size cap with the limit named (FR-009b).
- **SC-014a**: Watermark stability (FR-006a / D28). Take a snapshot at `as_of_sequence=S`; append a `finding` and an `external_response` (neither advances `session_revision`); re-take at `S` → byte-identical contents, while a snapshot at the new watermark includes them. `log_sequence` is monotonic and gap-free across a torn-tail crash and restart, and no two records share a value.
- **SC-014b**: Corrections do not travel backwards, and are not disabled by kind selection (FR-009a step 2a / D32 / D36). Finding A at `log_sequence=S` is in snapshot `S`; a human correction superseding A is appended at `S+1`; re-taking snapshot `S` is byte-identical and still contains A, while snapshot `S+1` reflects the correction. The correction fixture MUST use a record kind that is **not** in the snapshot's output allowlist, so the test proves the pipeline order rather than passing accidentally on a correction-shaped Finding. A test MUST fail if the implementation supersedes before cutting the prefix, or filters kinds before resolving.
- **SC-014f**: No future watermark (FR-009a step 2 / D34). With `current_max = S`, a request for `S+1` fails closed naming both values; it is not clamped and not served as "everything so far". Appending records afterwards does not change that refusal, and a snapshot taken at `S` before and after those appends is byte-identical. A kernel-issued snapshot always reports the `as_of_sequence` it was pinned at.
- **SC-014g**: Project-wide corrections, made explicit (FR-007b / D35). A `human_input` correction filed in session B against finding X of session A **does** remove X from session A's projection from its own sequence onward, matching `004` FR-007, and a snapshot at an earlier watermark still contains X. The test asserts this deliberately, so the cross-session reach is a pinned, reviewed behaviour rather than an accident. Corrections filed under a different `target` still apply (`004` scenario 3). No write-path `session_id` check exists on corrections.
- **SC-014h**: Composition break withholds the snapshot (FR-007c / D38). With `004`'s chain or head damaged anywhere in the project, no snapshot is served, no projection is built, no dependent transition dispatches, and the break is visible only on the operator channel — never in model context, and never as a partially served "verified subset".
- **SC-014i**: Both orderings coexist (FR-006a / D37). A record carries `seq` + `chain_prev` (per-target chain, `004` semantics preserved: removal detection and head attestation still pass) **and** `log_sequence` (project-global). Neither is exposed by `for_llm_context`. `004`'s existing composition tests still pass unchanged.
- **SC-014c**: Union allocator (FR-006a / D31). With two target files in one project, appends alternate across files and sequences stay globally monotonic and gap-free. Torn append in the first target followed by a restart and an append to the second: the tail is repaired first, no sequence is duplicated or burned, and no counter/reservation file exists anywhere in the store. A fixture with a duplicated or missing sequence fails closed instead of continuing from `1 + max`.
- **SC-014d**: Capacity boundary (FR-009b). Item count at `MAX_SNAPSHOT_ITEMS` succeeds and at `MAX + 1` fails closed naming the limit; canonical byte count at `MAX_SNAPSHOT_BYTES` succeeds and at `MAX + 1` fails closed. No truncation, sampling, or oldest-record dropping occurs on any path. The at-limit case doubles as a **performance characterization**: it records wall-clock for snapshot build and for one append (whose union scan is linear in the total project-log records and bytes, not logarithmic), so the quadratic total cost of scan-per-append stays a measured, accepted property of the 10000 / 32 MiB envelope rather than an unexamined one.
- **SC-014e**: Legacy records (FR-006b / D39). Pre-change records stop verifying and read as an empty store; a target whose records all fail verification and which has no head entry starts a fresh chain on the next write (`004` `test_MI014` still passes); nothing is re-signed and no sequence is invented. Resuming a session created before the change fails closed with an explicit message.
- **SC-015**: Scope rebind (FR-013c). `rebind_scope` during a pending/in-flight operation is refused; at a turn boundary it records a control event and increments `scope_generation`; the next transition gets a different `operation_id` for otherwise identical params; resume of a checkpoint from the previous generation fails closed instead of running the old Action against the new inputs. `rebind_scope` is absent from the model action vocabulary.
- **SC-016**: Canonical encoding golden vector (FR-005a) reproduces byte-identically in a freshly started process; NFC-equivalent-but-different key spellings, absent vs explicit `null`, and int vs float renderings are covered.
- **SC-013**: MI/hostile test: checkpoint prompt reference cannot become the system instruction (D18 / FR-020); audit prompt bodies live in pack-registered registry content, not kernel source.

## Assumptions

- Memory remains HMAC-signed append-only JSONL plus lease file + fsync; no SQLite.
- Manual/external relay remains a first-class `pending.kind=external_response` path (pack 004 Stage 2).
- Pack 004 declares include globs and uses the executor pending protocol; the reducer stays pure.
- Pin bump of araratsec is a pack implementation concern.

## Governance Impact (constitution)

- **Principle I**: Restored — episodic artifacts (including `pause_checkpoint`) re-enter as DATA only. System instructions come from the trusted registry, never from memory. HMAC is integrity, not a trust upgrade.
- **Principle II**: Unchanged.
- **Principle III**: Strengthened — pack cannot write memory; kernel persist kinds stay generic; `runtime_state_roots` are composition-root, not audit path names; the pack read seam is passed-in data, not a handle; the active pack stays composition-root wired (no dynamic pack registry at resume).
- **Principle IV**: Unchanged.
- No constitution version bump required (wording already forbids executing prior-turn artifacts as instructions).

## Cross-Repository Coordination

- Pack 004 MUST: use `AnalyzerExecution` without body `operation_id`; route chat **and** batch through `KernelActionExecutor`; express Stage 2 relay as `pending` with a correlation id derived from `transition_key`; declare an include set that covers every file it intends to read (analyzer inputs included); build the roadmap/synthesis projection from the passed `MemorySnapshot` only (FR-009a), including its standalone `finding` records, and record `as_of_sequence`; keep production `audit_agent/` free of durable-memory APIs; surface a needed scope widening as operator `rebind_scope` rather than reading outside the set; Ctrl-D → `detach_session` at a turn boundary; register its system prompt in the trusted registry rather than shipping it into kernel source.
- Kernel `plan.md` is authored against this revision (D18–D39, FR-002a/b/c, FR-005/005a, FR-006a/b, FR-007b/c, FR-009a/b, FR-010c, FR-013b/c, FR-018a/b), accepted 2026-08-22.
- Pack 004 `plan.md` / `tasks.md` still wait on kernel **implementation**.