# Feature Specification: Provenance for model-reported findings

**Repository**: secure-agent-kernel (Repo A, package `sr_agent`)

**Feature Branch**: `feat/005-finding-provenance`

**Created**: 2026-08-22

**Status**: Implemented (2026-08-22)

**Depends on**: `003` (`DispatchResult`, `operation_id`) and `004` (chain / `supersedes`),
both merged. Touches `sr_agent/orchestrator/loop.py`, `sr_agent/models/memory.py`,
`sr_agent/memory/episodic.py`.

**Requested by**: Repo B (`audit_agent`), as the kernel half of pack spec
`pack/003-agent-tool-surface` FR-008a. Numbering note: kernel and pack number features
independently and the spaces already collide (kernel `004-memory-composition-integrity`
vs pack `004-audit-loop-methodology`). Cross-repo references in this feature are written
`kernel/NNN` or `pack/NNN`.

## Why this exists

`OrchestratorLoop` persists a model-reported finding as soon as the model reports it —
[`loop.py:237`](../../sr_agent/orchestrator/loop.py:237) in `run` and
[`loop.py:374`](../../sr_agent/orchestrator/loop.py:374) in `run_turn`. Both sit
**before** the terminal check, before the unknown-action check, before `validate_action`,
and before `executor.execute`. The record carries no `payload_kind` at all.

So a `Finding` lands in the store whether the turn went on to run a tool, propose an action
the kernel rejected, name an action that does not exist, end on `complete`, or fail. Nothing
in the record distinguishes those. A later reader — the pack, an operator, a report — sees a
signed `Finding` and has no way to tell a hypothesis from a claim that followed real work.

This is not a trust-tier break. The record is `external_llm_output`, it is never promoted,
and nothing here is treated as a verdict (Constitution II holds). It is an **evidence gap**:
the store answers "did the model say this" and is silently taken to answer "was this
grounded in anything". A consumer cannot close the gap on its own, because `PackContext` has
no memory handle and the pack cannot annotate a record after the fact.

### What the kernel can and cannot observe

The kernel can see, for the `AgentAction` that carried the finding, whether the action
proposed in that same object resolved, and with which `DispatchStatus` and `operation_id`.

It cannot see whether the finding was **derived** from that tool's output. The model may
attach a finding to an unrelated action, or to an action it ran for other reasons. Kernel
observation is co-occurrence within one model turn, not derivation.

This distinction decides the feature's shape. `DispatchStatus`'s own docstring already
pins the smaller version of it — `ran` means the dispatch completed, not that an analyzer
produced grounded output, and collapsing the two is named there as an error. A kernel field
called `tool_grounded` would collapse it one level higher and would be **worse than the
present gap**: today a consumer knows a `Finding` is a hypothesis, whereas a manufactured
grounding flag invites it to stop knowing that. The kernel therefore reports facts it
checked and leaves the verdict to whoever defines what grounding means for their task.

## Scenarios

The model reports a finding, and in the same `AgentAction`:

| # | The turn's action | Record today | Record after |
|---|---|---|---|
| 1 | dispatch returned `ran` | `Finding`, unmarked | `Finding` + that dispatch's `operation_id` and status |
| 2 | dispatch returned `did_not_run` / `timeout` / `unavailable` / `error` | `Finding`, unmarked | `Finding` + that status, no successful operation |
| 3 | `validate_action` rejected it | `Finding`, unmarked | `Finding` marked as carrying no resolved action |
| 4 | `next_action` unknown to kernel and pack | `Finding`, unmarked | as 3 |
| 5 | terminal (`complete` / `escalate`), no tool at all | `Finding`, unmarked | as 3 |
| 6 | dispatch returned `pending` (awaiting confirmation or relay) | `Finding`, unmarked | `Finding` marked `pending`, plus a resolution record on resume (D-A) |
| 7 | pack refuses to build the finding | no record | unchanged — no record, no partial |

## Requirements

- **FR-001**: A model-reported finding MUST NOT be persisted before the action proposed in
  the same `AgentAction` has resolved. The binding is to that object, not to "the turn" —
  a chat turn runs several tool calls and "the turn's action" would be ambiguous.
- **FR-002**: A proposed finding MUST remain persistable. This feature closes an
  indistinguishability, not the ability to record a hypothesis. Scenarios 3–5 still write a
  record.
- **FR-003**: Every finding record MUST carry kernel-set provenance describing what happened
  to that action: the `operation_id` of the dispatch that resolved and its `DispatchStatus`,
  or an explicit marker that no action resolved. The fields are set by the kernel from the
  executor's own result — never from model params and never by the pack.
- **FR-004**: The kernel MUST NOT emit a field that asserts a finding is grounded in a tool
  result, under that or any equivalent name. It observes co-occurrence, not derivation, and
  a flag naming the stronger claim would create an evidence tier nothing verified
  (Constitution I). Grounding is a policy the consumer defines over FR-003's facts. This is
  the kernel's answer to pack/003 FR-008a's second branch, and it is deliberately narrower
  than that branch's wording.
- **FR-005**: `source_type` stays `external_llm_output`. No finding is promoted, by this
  path or any other. `PackContext` gains no memory handle, and the pack does not write the
  record (Constitution I, Principle III).
- **FR-006**: The chat path and the batch path MUST agree — same ordering, same stamping.
  Enforced structurally, so that a future change to one path cannot silently diverge from
  the other.
- **FR-007**: A finding whose payload the pack declines to build MUST still produce no
  record. No silent strip into a nearly-finding.
- **FR-008**: Findings MUST remain visible in `MemorySnapshot`. `_snapshot_kind`
  ([`episodic.py:872`](../../sr_agent/memory/episodic.py:872)) reads `payload_kind` first and
  only falls back to `"finding"` when it is empty, and `SNAPSHOT_KINDS` does not contain any
  finding-specific kind — so giving finding records a `payload_kind` would drop them out of
  the pack's projection **silently**. Whatever shape FR-003 takes must be checked against
  this, with a test that fails if findings stop reaching the snapshot.
- **FR-009**: The `memory_write` event (kernel/002) keeps firing on these writes. Consumers
  are already required to tolerate an unrecognised `payload_kind`, so no contract change is
  owed to them for the event itself.
- **FR-010**: New fields entering `fields_for_hmac()` MUST NOT reach model context.
  `for_llm_context()` already strips signature material; the provenance fields are metadata
  about the turn and must be reviewed against that boundary rather than assumed safe.
- **FR-011**: The kernel MUST NOT learn more about the domain `Finding` than it knows today.
  `_persist_finding` currently reads `.location` to derive a target; that is the existing
  extent of its domain knowledge and this feature does not deepen it (Principle III).

## Out of scope

- Any judgement about whether a finding is true, reproducible, or worth acting on.
- Pack `005-proof-loop-closure`, `write_poc`, the PoC queue runner.
- Moving the domain `Finding` model into the kernel.
- A memory handle on `PackContext`.
- Retro-marking findings already in the store. Records written before this feature carry no
  provenance and MUST read as "unknown", never as "no action resolved" — absence of the
  field is not evidence about the turn.

## Decisions taken with Repo B (2026-08-22)

Repo B answered D-A, D-B and D-C, and accepted FR-004 (fact, not flag) as satisfying
pack/003 FR-008a for their consumer. Their policy sits entirely on top of the facts below:
`proof MAY attach` only when the kernel stamped `resolved` + `ran` **and** the pack itself
joins that `operation_id` to a tool-result with `status="ran"`. The kernel's stamp is an
input to that join, never a verdict.

- **D-A — a paused turn.** Write at pause with `pending`, and file a kernel-authored
  resolution record on resume. Withholding until resume was rejected by the consumer: the
  main pending path is an out-of-band confirmation, and a hypothesis must survive a crash
  and stay visible in the snapshot while a human decides. Leaving it frozen at `pending`
  was rejected too — after resume the action has an outcome, and a record that still says
  `pending` is false evidence of a different kind.
- **D-B — one record per resolution.** Two records only in the D-A pause case. No
  "proposed now, resolved later" pair on an action that resolves in-process: that
  reintroduces the window this feature exists to close.
- **D-C — a positive marker, never an absent field.** `unresolved` may not be encoded as a
  null `operation_id`, because that is indistinguishable from a record written before this
  feature.

## Field set (FR-003 made concrete)

Kernel-set on `MemoryRecord`, on every finding write. **Not** a `payload_kind`, and not
inside `finding` — see FR-008 and FR-011.

| Field | Values |
|---|---|
| `action_resolution` | `"resolved"` \| `"unresolved"` \| `"pending"` |
| `action_operation_id` | `str`, or absent |
| `action_dispatch_status` | a `DispatchStatus` value, or absent |

| Case | `action_resolution` | `action_operation_id` | `action_dispatch_status` |
|---|---|---|---|
| dispatch resolved | `resolved` | present | terminal (`ran`, `error`, `did_not_run`, `timeout`, `unavailable`) |
| rejected / unknown id / terminal without a tool | `unresolved` | absent | absent |
| paused | `pending` | present (the id it paused with) | `pending` |
| written before this feature | absent | absent | absent |

A record with all three absent means **unknown**, never "no action resolved", and is never
proof-eligible.

- **FR-012**: All four fields MUST be kernel-set from the executor's result. No pack and no
  model params reach them.
- **FR-013**: FR-008 stated as an implementation constraint, not a second requirement:
  finding records keep an empty `payload_kind`, which is what makes `_snapshot_kind` resolve
  them to `"finding"` and keeps them inside `SNAPSHOT_KINDS`.

## Blocking issues found against the agreed shape

Both were found while checking D-A and D-C against the merged store; neither is a reason to
change the field set, but D-A cannot be built as written.

- **B-1 — `supersedes` is not available to the kernel.**
  `_enforce_status_rules` ([`episodic.py:1131`](../../sr_agent/memory/episodic.py:1131))
  refuses `supersedes` on anything but `source_type=human_input`: *"Corrections to existing
  records require human authority."* A finding stays `external_llm_output` (FR-005), so a
  kernel-authored supersede is refused at write time; and setting `human_input` to get past
  the check would promote a model-reported finding to the human tier, which is exactly what
  Constitution I forbids and what both sides already agreed not to do.
  Relaxing the rule was considered and is **not** recommended: `supersedes` is a
  delete-by-id primitive, `_apply_supersedes` drops the superseded record from every load
  and from the snapshot, and today the only authority that can invoke it is a human. Making
  the kernel a second such authority turns any future defect in this path into a way to make
  records disappear.
  **Resolution taken**: the resume record does not use `supersedes`. It is an ordinary
  finding-provenance record naming the paused one through a kernel-set
  `resolves_record_id`, and both records stay in the store and in the snapshot. This is
  better for the consumer as well: the paused record keeps its `record_id`, so a
  `write_poc` that captured that id before the pause still addresses the same finding
  afterwards — under `supersedes` that id would have vanished.
- **B-2 — adding signed fields would blank every existing store.**
  `fields_for_hmac()` is `model_dump(exclude={"hmac"})`
  ([`memory.py:114`](../../sr_agent/models/memory.py:114)), so three new fields change the
  signed shape of *every* record. Records already on disk would dump with the new keys set
  to `None`, fail verification, and be silently dropped — the whole store reads as empty,
  which is the behaviour kernel/004 documented and accepted for its own shape change. Doing
  it again would also erase the `legacy = unknown` case D-C depends on, since there would be
  no legacy records left to read.
  **Resolution taken**: the new fields are excluded from `fields_for_hmac()` when unset, so
  records written before this feature verify exactly as they do now.
  The security direction of that choice was stated in the first draft as a harmless
  downgrade — strip the fields, the record still verifies and reads as `unknown`. That was
  wrong and the test written for it caught it: the exclusion applies at **signing** time, so
  a record signed with the fields carries them inside the HMAC and stripping breaks the
  signature. What actually holds is stronger — pre-005 records verify unchanged, set fields
  are tamper-evident like every other field, and the removal still shows up against the
  signed chain head.
- **FR-014**: `resolves_record_id` MUST be kernel-set and MUST NOT be `supersedes`. The
  paused record survives resolution and keeps its `record_id`.
- **FR-015**: The provenance fields MUST NOT change the signed shape of a record that does
  not carry them. A store written before this feature MUST load unchanged.

## Snapshot exposure (Repo B condition, accepted 2026-08-22)

`PackContext` has no memory handle, so `MemorySnapshot` is the pack's only read seam. Fields
that live only in the JSONL are invisible to the consumer, and a consumer that cannot see
the stamp cannot filter on it — which would leave the feature delivering nothing.

- **FR-016**: `SnapshotItem` MUST carry `action_resolution`, `action_operation_id`,
  `action_dispatch_status` and `resolves_record_id`, read off the kernel envelope. They MUST
  NOT be reachable only through `body`. This follows `SnapshotItem`'s own rule that identity
  comes off the kernel-authored envelope and never off the body — a pack reading a field it
  wrote itself is making a claim about itself.
- **FR-017**: The provenance fields MUST NOT be written into `finding.model_dump()`, and
  `payload_kind` on findings is unchanged (FR-013).

Two consequences to write into the contract rather than discover later:

- **`operation_id` and `action_operation_id` are different questions.** The existing
  `operation_id` on `SnapshotItem` answers *"which operation is this record the commit
  of"*, and is read out of the payload
  ([`episodic.py:856`](../../sr_agent/memory/episodic.py:856)). `action_operation_id`
  answers *"which operation did the turn that produced this finding dispatch"*. On a finding
  record the first is `None` and the second may be set. The names are close enough to be
  misread, so both must be defined side by side in the contract. Unifying where the existing
  `operation_id` is read from is a kernel/003 behaviour change and is **out of scope here**.
- **The D-A pair costs two snapshot items.** A paused finding produces a `pending` record and
  a resolution record, and both count against the snapshot capacity envelope (10000 items /
  32 MiB, fail closed, never truncated). Sessions that pause on many findings reach the cap
  sooner than before. The remedy is unchanged: complete the session and start a new one.

## Contract owed to Repo B on landing

Delivered with the commit pin, not before: the field names and legal combinations above;
what pack/005 must ignore (`unresolved`, `pending`, non-`ran` statuses, and legacy records);
that `resolved` + `ran` is an input to the pack's own join and not a kernel verdict; the
pause/resume pair, with `resolves_record_id` on the resume record naming the paused
record's id and that paused id remaining the finding's address for `write_poc`; the four
`SnapshotItem` fields and how `action_operation_id` differs from `operation_id`; and
confirmation that `payload_kind` is unchanged on findings, so the kernel/002 event contract
is a no-op for them.
