# Phase 0 Research: kernel-owned `write_memory` + memory-write observability

**Feature**: `002-memory-write-path` · **Date**: 2026-08-22 · **Spec**: [spec.md](./spec.md)

The spec resolved D7 in August, when `pack.dispatch` was called directly from
`loop.py` and `EpisodicMemory.write` had four callers. Feature `003` shipped in
between and moved both. Nothing in D7 is contradicted, but four of its sentences
now name places that no longer exist, and three questions D7 never had to answer
are now live. This document resolves them as **D8–D13** (the numbering gap `003`
left between `002`'s D7 and its own D14).

Nothing here reopens D7. Where a decision below narrows D7, it says so.

---

## D8 — the interception point is `KernelActionExecutor.execute`, not the loop

**Decision**: `write_memory` is intercepted inside
`KernelActionExecutor.execute`, immediately after `validate_action` and before
`derive_ids`. `loop.py` is not touched for this path.

**Rationale**: FR-001 says "the orchestration loop (batch and chat) MUST execute
the write in-kernel and MUST NOT call `pack.dispatch`". When that was written the
loop *was* the dispatch caller. Feature `003` made
`KernelActionExecutor.execute` the sole production caller of `pack.dispatch`, and
made that an enforced invariant: `tests/architecture/test_single_dispatch_path.py`
AST-scans `sr_agent/` and fails on any caller outside `executor.py`.

Intercepting in `loop.py` would satisfy FR-001's letter and break its intent. It
would create a second place where an action is executed without going through the
executor — the exact drift `003` FR-018 exists to prevent, and the reason chat and
batch diverged before. Intercepting in `execute` satisfies FR-001 *and* FR-004's
parity requirement for free: both `OrchestratorLoop.run` (batch,
[loop.py:280](../../sr_agent/orchestrator/loop.py:280)) and `OrchestratorLoop.run_turn`
(chat, [loop.py:410](../../sr_agent/orchestrator/loop.py:410)) already call
`self._executor.execute`. There is no second wiring job and no way for one surface
to be forgotten.

**Alternatives considered**:

- *Intercept in `loop.py` before `_executor.execute`* — rejected: two execution
  paths, and the architecture test's guarantee ("one place to get this wrong")
  degrades to "one place to get *dispatch* wrong, plus a memory path beside it".
- *Give `write_memory` an `ActionSpec` flag that the executor keys off* —
  rejected as indirection: the executor would still need the branch, and the flag
  would be a second, weaker statement of which id is kernel-executed.

**Consequence for the spec text**: FR-001's "the orchestration loop" reads as
"the kernel execution path", which is now the executor. No FR is amended; the
plan records the location.

---

## D9 — every `EpisodicMemory.write` emits, and the event is metadata-only

**Decision**: three parts.

1. **Scope**: FR-007 is honoured literally — *every* successful
   `EpisodicMemory.write` emits `memory_write`, including the writes `003`
   introduced (`commit_if_absent`, `put_external_response_if_absent`,
   `write_pause_checkpoint`) and the ones that predate it (findings, chat turns,
   session snapshots, PoC status, scope rebinds).
2. **Binding**: an optional `event_sink: Callable[[dict], None] | None` is bound
   into `EpisodicMemory.__init__`, alongside `privileged_statuses` (D5) and
   `lease` (`003`). Composition root's job; default `None`.
3. **Payload**: the event carries *metadata only* — `record_id`, `project_id`,
   `target`, `session_id`, `source_type`, `payload_kind`, `log_sequence`. It
   carries **no record body** and **no signature material** (`hmac`, `seq`,
   `chain_prev`).

**Rationale, part 1**: US2 enumerates "model note, finding persist, chat turn,
session snapshot, etc." — an open list. `003`'s records are new members of that
list, not exceptions to it. The stated purpose is that an operator can tell
episodic memory was appended without scraping INFO logs; a dispatch commit is the
single most consequential append the kernel makes, and exempting it would invert
the requirement. Narrowing FR-007 to "writes the model caused" would also be a
scope reduction decided by the implementer rather than the operator.

**Rationale, part 2**: the spec's own Assumptions offered "directly or via a thin
kernel wrapper used by all writers". The wrapper option was viable in August with
four call sites; there are now seven, spread across `executor.py`, `loop.py`,
`chat_session.py`, and `scope.py`. A wrapper is now a convention that a new writer
can silently skip, and nothing would fail. `EpisodicMemory.write` is the one place
every durable append provably passes through — the same argument that put the HMAC,
the status gate, the chain link, and the lease check there.

**Rationale, part 3**: the sink is supplied by the composition root and its output
is rendered to operators. A record body can contain attacker-influenced text
(tool output, relayed model output), so putting bodies into a trace event creates
a rendering surface for content the kernel spent the rest of its design keeping
wrapped. Signature material is excluded for the reason already written into
`MemoryRecord.for_llm_context`: `chain_prev` is the previous record's HMAC, and a
channel that carries it is a channel that leaks it. Metadata answers the question
FR-007 asks — *was memory appended, by whom, of what kind* — without carrying
either.

**Alternatives considered**:

- *Emit only for model-proposed `write_memory`* — rejected: makes A-hard
  observability cosmetic, and the writes an operator most needs to see are the
  kernel's own.
- *Emit from each call site* — rejected: seven places to forget, zero enforcement.
- *Carry the body* — rejected as above. If a consumer needs the body it can read
  the record by `record_id` through the normal verified path, where the DATA
  wrapping rules still apply.

**Open sub-risk, handled in the plan**: a sink that itself writes memory would
recurse. The contract states the sink MUST NOT write; the implementation adds a
re-entrancy guard so that a violating sink is a broken observer rather than a
broken store.

---

## D10 — `model_note` is deliberately not a snapshot kind

**Decision**: `payload_kind="model_note"` is **not** added to
`SNAPSHOT_KINDS` (`{finding, dispatch_commit, external_response}`,
[dispatch.py](../../sr_agent/models/dispatch.py)). Model notes are durable,
readable, and observable, but they never reach a pack's `MemorySnapshot`.

**Rationale**: this is currently true by accident — `003` fixed the allowlist
before `model_note` existed — and an accident that a later "completeness" edit
would undo. It should be a rule with a reason.

The reason: a model note is `llm_inference`, the lowest tier in the Principle I
hierarchy. `MemorySnapshot` is the input to a pack's domain projection, from which
`003` FR-009a requires the pack build its *full* projection. Admitting
`llm_inference` content there would let the model's own prose become an input to
the projection that decides what the agent does next — a self-reinforcing loop
across turns, which is exactly the retrospective-poisoning channel Principle IV
closes for steering knowledge. The kernel already refuses to promote model output
across the source-type boundary; refusing to feed it into the pack's reducer is
the same rule one layer out.

Notes remain available: they are in the log, they verify, they load through
`load()`/`load_for_principal()`, and they emit `memory_write`. What they do not do
is silently become premises.

**Alternatives considered**:

- *Add `model_note` to `SNAPSHOT_KINDS`* — rejected above.
- *Leave it unstated* — rejected: an unstated invariant is one refactor from gone.

---

## D11 — the target comes from params, exactly as it already does for a dispatch commit

**Decision**: `write_memory` honours a model-supplied `params["target"]`, using
the same expression the kernel already uses for a dispatch bundle:

```python
target = str(action.params.get("target") or action.action_type)
```

The validator bounds it (non-empty after strip, no NUL or control characters, at
most 200 bytes UTF-8). It does not constrain its *meaning*.

**Rationale**: the first draft of this decision fixed the target at
`_model_notes` on the grounds that the target is a partitioning key rather than
content, and that the model has no business choosing it. Two things overturn that.

*Operationally*: one agent is not spun up per project and per goal. A session
moves across targets during its life, and a note about `Vault.withdraw` filed
under a global `_model_notes` bucket loses the one association that makes it
worth reading later. The fixed target optimizes a property nobody asked for at
the cost of the note's usefulness.

*For consistency*: the kernel already accepts a model-supplied target on the more
consequential path. [`executor.py:170`](../../sr_agent/orchestrator/executor.py:170)
and [`:332`](../../sr_agent/orchestrator/executor.py:332) build the
`dispatch_commit` target as `action.params.get("target") or action.action_type`.
Holding an `llm_inference` note to a stricter rule than a `tool_output` commit
would be a rule with no principle behind it — and a second convention for the
same field is how two writers drift.

Containment does not depend on this choice. `project_id` comes from
`session.principal`, never from params, so the target cannot reach another
project. `EpisodicMemory._target_stem` replaces `/` and `:`, so a target cannot
escape the project directory — `"../../etc/x"` becomes the ordinary filename
`.._.._etc_x.jsonl`. Findings already derive their target from model-reported
content (`loop.py:484`, `location.split(":")[0]`).

**What the validator does add**: a target becomes a filename, and three inputs
turn into an `OSError` rather than a clean refusal — an empty string, a NUL or
control character, and a name past the filesystem's 255-byte limit. D12.3 would
catch those and report "rejected", so nothing breaks either way; rejecting them
by name is simply a better message and a testable boundary.

**Alternatives considered**:

- *Fixed `_model_notes` target* — rejected above. It was the original decision;
  it did not survive the operational reading or the consistency check.
- *Per-session target (`chat:<session_id>`)* — this is the convention for chat
  turns and checkpoints (`chat_session.py`, `executor.py:232`), and it is right
  for records that are *about the session*. A note is about a target, not about
  the session, so it follows the finding/commit convention instead.
- *Constrain the target to a path inside the content scope* — rejected as
  category confusion: `target` is a memory partition label, not a filesystem
  path. `read_file` is scope-bound because it opens files; nothing opens a target.

**Carried forward as a separate finding, not fixed here**: the `dispatch_commit`
target at `executor.py:170` / `:332` takes the same model-influenced string with
no validation at all, so an empty or oversized target there fails as an `OSError`
mid-commit rather than as a refusal. Tightening it would change the behaviour of
a merged feature and belongs to its own change, not to this one. The plan lists
it under Risks; the task list carries it as an optional task the operator can
drop.

---

## D12 — forged provenance is refused structurally *and* explicitly, and never raises

**Decision**: two layers, plus a fail mode.

1. **Structural**: the executor builds the `MemoryRecord` from a fixed shape and
   never reads `source_type`, `hmac`, `supersedes`, `status_change`, `seq`,
   `chain_prev`, or `log_sequence` out of `action.params`. There is no code path
   that could honour them.
2. **Explicit**: `write_memory` gets a real validator in
   `orchestrator/action.py` (replacing `_noop_validate`) that **rejects** — not
   strips, not ignores — any params carrying those keys, rejects an empty or
   missing note, and bounds `target` (D11). `target` is the one params key on
   this path that IS honoured; every other identity-shaped key is refused.
3. **Fail mode**: every failure on this path, including
   `MemoryWriteError` from the lease or the status gate, `MemoryChainError`, and
   `PrincipalMismatch`, is returned as `DispatchResult(status=error, ...)` and
   re-enters the model's context DATA-wrapped. Nothing on this path raises into
   the turn.

**Rationale (1 + 2)**: the spec's D7 says "reject or strip fail-closed … default:
reject", leaving the fail mode to the plan. Reject, for the reason `003` already
wrote into `DispatchPayload`'s `extra="forbid"`: an ignored field is a claim the
model made about itself that the kernel silently accepted and then could not see.
Silent stripping also makes the two-layer defence untestable — an attempt that is
stripped looks identical to an attempt never made.

The structural layer is the one that actually holds. Validation can be bypassed by
a future caller that forgets to validate; a record built from a fixed shape cannot
be made to carry a forged tier by any params at all. `003`'s H5 tests establish
the precedent (`test_H5_payload_has_no_identity_field_to_set` alongside
`test_H5_extra_identity_fields_are_refused_not_ignored`), and this feature follows
it rather than inventing a second style.

**Rationale (3)**: new since D7. `EpisodicMemory.write` now calls
`_require_lease` and can raise `MemoryWriteError`, and `_next_log_sequence` can
raise `MemoryChainError` on a composition break. A model-proposed action must not
be able to kill a turn by proposing a write the kernel then refuses — that turns a
denied action into a denial of service the model can trigger at will. The loop's
existing convention for a refused action is a DATA-wrapped `ACTION REJECTED`
string fed back as tool output; this path uses it.

Note the honest boundary: a composition break refuses the write and tells the
model "rejected", while the operator learns the real reason through the
out-of-band break signal `004` added. The model is not told the store is broken.
That asymmetry is intended and is the same posture as the silent-drop rule.

---

## D13 — `write_memory` is not a dispatch transition

**Decision**: the `write_memory` path does **not** derive a
`transition_key` / `operation_id`, does **not** go through `commit_if_absent`, and
does **not** produce a `dispatch_commit` record. Two identical notes produce two
records.

**Rationale**: `session_revision` counts `dispatch_commit` records and is the
optimistic-concurrency token `003` uses to refuse a stale commit. If a model note
advanced it, an unrelated note would invalidate an in-flight dispatch expectation —
precisely the failure `003` documents in `session_revision`'s docstring
("other durable records … must not move this"). A note is not a transition against
the outside world: there is no effect to make idempotent, so the machinery that
exists to prevent a second external effect has nothing to protect here.

Deduplication is therefore not offered, and that is correct for an append-only
log: two identical notes in one session are two things the model said, not one
thing said twice.

**Alternatives considered**:

- *Route notes through `commit_if_absent` for uniformity* — rejected: couples an
  observability-grade write to the exactly-once protocol and moves
  `session_revision` for a non-transition.

---

## Clarification — why `llm_inference` and not `external_llm_output`

FR-002 puts a model note at `llm_inference`. The sibling kernel-authored artifact
of model origin — a reported finding — is persisted at `external_llm_output`
([loop.py:485](../../sr_agent/orchestrator/loop.py:485)). Two contents the model
produced, two tiers. Left unexplained this reads as an oversight, so:

The constitution names both, in different principles. Principle I: "Model and
relay output is `external_llm_output`". Principle IV: "A model's draft lesson is
`llm_inference`". A `write_memory` note is the second thing, almost verbatim — an
unprompted remark the model decided to keep for itself. A finding is the first: a
structured claim produced in response to a specific analytical task, and the tier
the kernel already commits to for it.

The choice is also the conservative one. `llm_inference` is trust level 1,
`external_llm_output` is 2, so a note sits *below* a finding and gains nothing a
finding does not already have. Nothing here can be a weakening; the only risk of
getting it wrong in this direction is a note being treated more sceptically than
it deserves, which is the correct direction to err in.

---

## Resolved: no NEEDS CLARIFICATION remains

D7 (spec) plus D8–D13 (here) cover every FR. The one item the spec left explicitly
to the plan — the fail mode for disallowed params, FR-004 — is resolved in D12 as
*reject*, matching the spec's own stated default.
