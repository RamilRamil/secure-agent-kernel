# Implementation Plan: kernel-owned `write_memory` path + memory-write observability

**Branch**: `002-memory-write-path` | **Date**: 2026-08-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/002-memory-write-path/spec.md` (Status: Draft, D7 resolved 2026-08-10)

**Phase 0**: [research.md](./research.md) — D8–D13, the decisions `003` made necessary.

**Sequencing note**: this feature was specified before `003-dispatch-result-resume`
and is implemented after it. `003` is merged (`3675bac`, 69/70 tasks; suite at
300 passed / 4 skipped / 2 xfailed). Nothing in D7 is contradicted by `003`, but
the two places FR-001 and FR-007 name have both moved. See D8 and D9.

## Summary

`write_memory` is a kernel-generic action id that the kernel does not execute. It
validates as `ActionClass.memory`, then falls through to `pack.dispatch`, where
packs stub it — a blessed, non-gated generic id with a pack-owned body. This
feature makes the kernel execute it: intercepted in `KernelActionExecutor.execute`
before `pack.dispatch`, persisted as a `model_note` payload at `llm_inference`
with kernel-set provenance, filed under the target the action names, with forged
provenance refused rather than ignored.

Alongside it, every durable episodic append becomes first-class observable. A
`memory_write` event fires from `EpisodicMemory.write` — the one point all seven
production writers pass through — carrying metadata only, never a record body and
never signature material.

The approach is additive and small: one branch in the executor, one real validator
replacing `_noop_validate`, one optional sink bound at `EpisodicMemory`
construction, one emit line after the append is durable. No new module, no new
store, no change to the record format, and therefore no signed-shape break and no
version bump beyond a patch.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: pydantic v2 (models, `extra="forbid"`), stdlib `hmac`
/ `hashlib` / `os` (signing, fsync). No new dependency.

**Storage**: append-only JSONL under `memory/<project_id>/`, HMAC-signed per
record, chained per target (`seq` / `chain_prev`) with a signed
`_chain_head.json`, ordered project-wide by `log_sequence`. Unchanged by this
feature: `model_note` is a `payload_kind`, not a schema field.

**Testing**: pytest. `tests/unit/`, `tests/security/`, `tests/architecture/`.
Test-first is mandatory for the security-critical parts (FR-002, FR-004, FR-005,
FR-006, FR-009) per the constitution's Development Workflow section.

**Target Platform**: local CLI / library (`sr_agent`), consumed as a pin by
araratsec-agent (`audit_agent`).

**Project Type**: single Python package, library + CLI.

**Performance Goals**: no new read of the log on the write path. The interception
adds one `EpisodicMemory.write` (already fsync-bound); the emit adds one
dictionary construction and one callable invocation per append. Both are
negligible against the existing fsync. No change to snapshot cost.

**Constraints**: observability MUST NOT become a control-plane dependency
(FR-008) — a missing or raising sink cannot block, reverse, or fail a write.
Protected MI ASR must stay at the existing bar (FR-009).

**Scale/Scope**: 11 functional requirements, 7 success criteria, 3 user stories.
Touches 5 production files; adds no module.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after the Phase 1 design below.*

| Principle | Verdict | Basis |
|---|---|---|
| **I — trust invariants** | **Strengthened** | Kernel sets `source_type=llm_inference` itself (FR-002); model params can never author provenance (D12, structural). The write outcome re-enters context DATA-wrapped. The `memory_write` event carries no signature material — the `for_llm_context` rule extended to the trace channel (D9). Silent-drop and `compare_digest` untouched. |
| **II — human authority** | **No expansion, no erosion** | FR-005: `ActionClass.memory` does not gain OOB. `write_execute` and pack-declared privileged statuses remain the only gates. A privileged `status_change` through `write_memory` is refused twice — at validation (D12) and at `_enforce_status_rules` — and `write_memory`'s `ActionSpec` is untouched. |
| **III — kernel / pack separation** | **Fulfilled, not diluted** | This is the principle's own stated exception: `write_memory` is named in the constitution as kernel machinery whose presence is a "structural necessity". Executing it kernel-side is what makes that claim true rather than aspirational. `PackContext` gains nothing (FR-006); the pack is not invoked for this id. No domain vocabulary enters the kernel: `model_note` is provenance-shaped, not audit-shaped. |
| **IV — knowledge promotion** | **Unchanged, and reinforced** | A model note is episodic DATA at `llm_inference` with no promotion path. D10 additionally keeps it out of `MemorySnapshot`, so it cannot become an input to the pack's projection — the same rule one layer out. |
| **V — provider agnostic** | **Not engaged** | No provider-conditional behaviour; no model-capability assumption. |
| **Security requirements** | **Gated** | FR-009 keeps the MI harness at its existing bar; no security test is deleted or weakened. New MI coverage is added for the new action path rather than assumed. |

**Result**: PASS, no violations. Complexity Tracking below is empty by design.

## Project Structure

### Documentation (this feature)

```text
specs/002-memory-write-path/
├── spec.md              # D7 (2026-08-10)
├── research.md          # Phase 0 — D8..D13
├── plan.md              # This file
├── data-model.md        # Phase 1 — the model note record and the event
├── contracts/
│   └── memory-write-event.md   # Phase 1 — the live-trace event contract
├── quickstart.md        # Phase 1 — wiring the sink, reading a note
├── checklists/
│   └── requirements.md  # spec quality gate (passed 2026-08-10)
└── tasks.md             # Phase 2 — /speckit-tasks, NOT created here
```

### Source code (repository root)

```text
sr_agent/
├── memory/
│   └── episodic.py           # + event_sink binding, + emit after durable append,
│                             #   + re-entrancy guard  (FR-007, FR-008, D9)
├── models/
│   ├── memory.py             # (read-only for this feature; no schema change)
│   └── dispatch.py           # + comment pinning model_note ∉ SNAPSHOT_KINDS (D10)
├── orchestrator/
│   ├── action.py             # _noop_validate → _validate_write_memory  (FR-004, D12)
│   └── executor.py           # + write_memory interception before dispatch (FR-001, D8)
└── tools/
    └── registry.py           # _D_WRITE_MEMORY rewritten                  (FR-010)

tests/
├── unit/
│   ├── test_write_memory_validation.py # param policy: reject, never strip
│   ├── test_write_memory_path.py       # US1: durable, kernel-provenanced, no dispatch
│   ├── test_write_memory_not_a_transition.py  # US1: D13, session_revision unmoved
│   └── test_memory_write_event.py      # US2: emission scope, sink absence/failure
├── security/
│   ├── test_write_memory_forgery.py    # US1/US3: forged provenance + privileged status
│   └── test_hostile_pack.py            # extended: pack still cannot author a note
└── architecture/
    └── test_single_dispatch_path.py    # unchanged, must stay green (D8)

docs/
├── kernel.md / kernel.ru.md                         # FR-011
└── capability-pack-interface.md / .ru.md            # FR-011
```

**Structure Decision**: no new module. Every change lands in a file that already
owns the concern — the executor owns action execution, `episodic.py` owns the
durable append, `action.py` owns param validation, `registry.py` owns descriptor
text. Introducing a `memory_write` module would put the emit somewhere other than
the chokepoint, which is the one property D9 depends on.

## Phase sequencing

Test-first throughout for the security-critical items. Each phase is independently
green — the suite passes at every boundary, so any phase can be a commit.

### Phase A — the validator (FR-004, D12)

Replace `_noop_validate` for `write_memory` with `_validate_write_memory`:

- rejects a missing or empty note, and a note over `MAX_PAYLOAD_BODY_BYTES`;
- rejects any params key in the forbidden set — `source_type`, `hmac`,
  `supersedes`, `status_change`, `status`, `seq`, `chain_prev`, `log_sequence`,
  `record_id`, `project_id`, `session_id`, `tool`;
- bounds `target`, which **is** honoured (D11): non-empty after strip, no NUL or
  control characters, at most 200 bytes UTF-8.

Rejection, not stripping. Written as failing tests first.

Independently valuable: even before interception exists, a forged-provenance
`write_memory` stops reaching `pack.dispatch`.

### Phase B — the interception (FR-001, FR-002, FR-003, D8, D11, D12, D13)

In `KernelActionExecutor.execute`, after `validate_action` and before
`derive_ids`: if `action.action_type == "write_memory"`, build the record from a
fixed shape and write it, then return. Never reaches `derive_ids`,
`find_committed_bundle`, `pack.dispatch`, or `commit_if_absent` (D13).

Record shape is fixed and kernel-authored:

```
project_id  = session.principal.project_id      # never from params
session_id  = session.session_id                # never from params
source_type = SourceType.llm_inference          # never from params (FR-002)
target      = params.get("target") or action.action_type   # D11, as 003 does
payload_kind= "model_note"
payload     = {"note": <the validated note>}
```

Every failure returns `DispatchResult(status=error, body=...)`; nothing raises
(D12.3). The loop already DATA-wraps a non-`ran` body back into context.

Parity (FR-001, SC-004) is structural: both loop entry points already route
through `execute`.

### Phase C — the event (FR-007, FR-008, D9)

1. `EpisodicMemory.__init__` gains `event_sink: Callable[[dict], None] | None =
   None`, bound by the composition root exactly as `privileged_statuses` and
   `lease` are.
2. `write()` emits after `_append_durably` + `_write_head` + `_extend_cache` — the
   event means *this is on disk*, so it cannot fire before the fsync.
3. The emit swallows every exception and logs at DEBUG, matching
   `OrchestratorLoop._emit` (FR-008).
4. A re-entrancy guard: while the sink is running, a nested `write` from the same
   instance does not emit. A sink that writes memory is a broken observer; it must
   not become an unbounded recursion in the store.

Payload is metadata-only per D9.3.

### Phase D — descriptor and docs (FR-010, FR-011)

`_D_WRITE_MEMORY` currently reads *"Write a structured finding or status update to
episodic memory… HMAC is added by the orchestrator"* — wrong on both halves after
this feature: findings stay on the finding path (FR-003), and the model never
supplies a record at all. Rewrite to describe a note with kernel-set provenance.
The description hash is computed from the text at import
(`_hash(_D_WRITE_MEMORY)`), so it follows automatically; no test pins the literal
string, and `verify_all_hashes` stays green.

Reconcile the four docs that describe `write_memory` execution or memory
observability (`docs/kernel.md`, `docs/kernel.ru.md`,
`docs/capability-pack-interface.md`, `docs/capability-pack-interface.ru.md`) so
none implies pack-stub execution as the steady state.

### Phase E — the gates (FR-009, SC-005, SC-006)

Run the protected MI harness and the hostile-pack suite; extend rather than
adjust. Two new hostile assertions: a pack cannot author a `model_note` (it has no
memory handle and is not invoked for the id), and a `write_memory` cannot reach
`human_input` by any params. Full suite must be green including the untouched
`003` and `004` tests.

## Risks and how the plan handles them

**A model can now cause an unbounded number of durable writes.** The per-turn
tool-call budget bounds it within a turn (Principle I), and the note is one small
payload, but a long session accumulates them on disk and in `log_sequence`.

They do **not** count toward the `003` snapshot capacity envelope: capacity is
enforced on the post-filter `items`
([episodic.py:795-814](../../sr_agent/memory/episodic.py:795)), and D10 keeps
`model_note` out of `SNAPSHOT_KINDS`, so a note never enters that count. An
earlier draft of this risk claimed the opposite; it was wrong, and the correction
is recorded here rather than quietly dropped.

Phase A caps the note body at a fixed byte limit, reusing
`MAX_PAYLOAD_BODY_BYTES` rather than inventing a second number, and refuses over
it — same posture as `003`'s payload limits. The cap is justified by disk growth
and by consistency with `003`, not by the snapshot envelope.

**The event becomes a control-plane dependency by accident.** The guard is
structural (the emit is after the durable append and inside a bare `except`) and
tested directly: SC-003's negative case asserts a raising sink does not affect the
returned record or the store. Named because it is the failure mode that would make
this feature a liability rather than an improvement.

**The trace channel becomes an injection surface.** Handled by D9.3 — metadata
only. Worth stating that this is not hypothetical: record bodies routinely contain
attacker-influenced analyzer output, and an operator terminal is a place where
text is read by a human deciding what to approve.

**FR-007's widened scope surprises a consumer.** Repo B renders the live trace. It
will now see `memory_write` for dispatch commits and pause checkpoints, not only
findings. That is the intent (D9.1), but it is a volume change on a channel
someone else consumes. The contract in `contracts/` states it explicitly, and the
`payload_kind` field lets a consumer filter without guessing.

**A composition break makes `write_memory` look like an ordinary rejection.**
Intended (D12.3), and the honest limit is written down rather than smoothed over:
the model is told "rejected", the operator learns the real cause out-of-band.

**The `dispatch_commit` target is unvalidated, and this feature does not fix it.**
`executor.py:170` / `:332` build a commit's target from
`action.params.get("target")` with no bounds at all, so an empty or oversized
target there surfaces as an `OSError` mid-commit rather than as a refusal. D11
holds the new path to a higher bar; retrofitting the same helper onto `003`'s
path would change the behaviour of a merged feature and belongs to its own
change. It is carried as an optional task, not folded in silently.

## Downstream

- **`/speckit-tasks`** may run on this plan immediately.
- **araratsec-agent (Repo B)**: `memory_write` rendering is explicitly out of
  scope here (spec Assumptions). This feature ships the kernel event contract and
  its tests; the CLI surface is a separate change in Repo B against the contract
  in `contracts/memory-write-event.md`.
- **Version**: no signed-shape change, so unlike `004` and `003` this is not a
  compatibility break. Records written before it verify after it. A patch bump is
  sufficient; the minor bump belongs to whatever release carries `003`.

## Complexity Tracking

No Constitution Check violations. Table intentionally empty.
