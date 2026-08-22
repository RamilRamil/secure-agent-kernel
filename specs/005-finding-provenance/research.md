# Phase 0 — Research: provenance for model-reported findings

**Feature**: kernel/005-finding-provenance · **Spec**: [spec.md](./spec.md)

## A note on decision numbering

Decision ids collide across features in this repo: kernel/002 used D8–D13 while
kernel/003 independently used D1–D27, so a bare "D11" is ambiguous in the codebase
already. This feature uses **`D005-n`**. Existing bare `Dn` references in code comments
belong to whichever feature the surrounding comment names.

---

## D005-1 — The persist moves inside the loop, and cannot be called without an outcome

**Decision**: `_persist_finding` gains a required provenance argument and is called only
once the action proposed in the same `AgentAction` has an outcome. It stays in
`OrchestratorLoop`; it does not move into `KernelActionExecutor`.

**Rationale**: the executor must not learn what a finding is. It is the single dispatch
path (`tests/architecture/test_single_dispatch_path.py`) and knows actions, not domain
objects; `agent_action.finding` is a loop concern. Moving the write there would put domain
vocabulary into the one class Principle III most needs clean.

The ordering is enforced by the *signature*, not by discipline. Once
`_persist_finding(agent_action, provenance)` requires a `FindingProvenance`, an early call
site has nothing to pass — there is no outcome yet to name. A reviewer cannot reintroduce
the bug by moving one line back up; they would have to invent a provenance value, which is
a visible act rather than an invisible one.

**Alternatives considered**:

- *Persist in the executor after dispatch.* Rejected: domain knowledge in the wrong class,
  and it would not cover scenarios 3–5 (rejected / unknown / terminal), which never enter
  the executor at all.
- *Keep the call where it is and patch the record afterwards.* Impossible on append-only
  JSONL, and the patch window is the gap this feature exists to close.

## D005-2 — The fields live on the record envelope

**Decision**: `action_resolution`, `action_operation_id`, `action_dispatch_status` and
`resolves_record_id` are optional fields on `MemoryRecord`. Not in `payload`, not in
`finding`, and not a new `payload_kind`.

**Rationale**: `SnapshotItem`'s own docstring states the rule — identity comes off the
kernel-authored envelope, never off the body, because a pack reading a field it wrote
itself is making a claim about itself. The same argument applies to the model: the finding
body is model-authored text, and provenance about that text may not sit inside it.

`payload_kind` specifically is excluded because `_snapshot_kind` reads it first and falls
back to `"finding"` only when it is empty. Giving findings a kind would drop them out of
`SNAPSHOT_KINDS` and out of the pack's projection **silently** (spec FR-008/FR-013).

## D005-3 — Unset provenance is excluded from the signed shape

**Decision**: `fields_for_hmac()` omits the four fields when they are `None`, so a record
written before this feature produces the same signed dict it produces today.

**Rationale**: `fields_for_hmac()` is `model_dump(exclude={"hmac"})`. New fields would
otherwise appear as `None` in the dump of every stored record, every signature would fail,
and the whole store would read as empty — the behaviour kernel/004 documented and accepted
for its own shape change. Repeating it here would also destroy the `legacy = unknown` case
the field design depends on: there would be no legacy records left to read.

**Security direction, corrected during implementation.** The first draft of this decision
claimed an attacker with file-write access and no key could strip the fields from a stored
line, leave a record that still verified, and so downgrade `resolved` to `unknown` —
harmless, because both are non-proof-eligible. That was wrong, and the test written for it
is where it was caught. The exclusion is conditional on the field being **unset at signing
time**: a record signed *with* the fields carries them inside the HMAC, so removing them
breaks the signature and the record is dropped like any other tamper — and the signed chain
head still attests it was there, so the removal surfaces to the operator.

The real property is the stronger one. Unset fields are outside the signed dict, which is
what keeps pre-005 stores verifying; set fields are signed like every other field. There is
no downgrade path, only deletion, and deletion is what kernel/004's chain exists to detect.

**Alternatives considered**: signing the fields unconditionally and accepting the blank
store (rejected — data loss plus loss of the legacy case); a schema version field
(rejected — a second mechanism to get wrong, and it changes the signed shape anyway).

## D005-4 — No `supersedes`; the resume record names the paused one

**Decision**: the record written on resume carries `resolves_record_id`, a kernel-set field
naming the paused record. `_enforce_status_rules` is not relaxed and no finding is written
at `human_input`.

**Rationale**: `supersedes` requires `source_type=human_input`
(`episodic.py:1131`, *"Corrections to existing records require human authority"*). A
finding stays `external_llm_output`, so a kernel-authored supersede is refused at write
time, and setting `human_input` to pass the check would promote model output to the human
tier (Constitution I).

Relaxing the rule was considered and rejected: `supersedes` is a delete-by-id primitive —
`_apply_supersedes` removes the superseded record from every load and from the snapshot —
and today exactly one authority can invoke it. Making the kernel a second one turns any
future defect on this path into a way to make records disappear.

The consumer gains from the rejection: the paused record keeps its `record_id`, so a
`write_poc` that captured that id before an out-of-band confirmation still addresses the
same finding after resume. Under `supersedes` that id would have vanished from the
projection precisely between the confirmation and the resume.

## D005-5 — Two operation ids, deliberately not merged

**Decision**: `SnapshotItem` carries both the existing `operation_id` and the new
`action_operation_id`. They are not unified.

**Rationale**: they answer different questions — *"which operation is this record the
commit of"* versus *"which operation did the turn that produced this finding dispatch"*. On
a finding record the first is `None` and the second may be set. The existing one is read
out of the payload (`episodic.py:856`), which is inconsistent with `SnapshotItem`'s own
envelope rule; fixing that is a kernel/003 behaviour change and is out of scope. Both names
must appear side by side in the contract, because they are close enough to be misread.

## D005-6 — The resume record is written by the loop, keyed by operation id

**Decision**: on resume, after the dispatch resolves, the loop writes one resolution record
for each finding record in this session that has `action_resolution == "pending"` and
`action_operation_id == <the resumed operation id>`.

**Rationale**: the executor's `resume` must stay free of finding knowledge (D005-1), and
the operation id is the only durable link between the paused turn and the resumed one — the
`AgentAction` itself is gone. The lookup is scoped by session and project through the normal
verified read path; a record whose signature does not verify is simply not there to match,
so a forged pending record cannot attract a resolution.

**Open at implementation**: whether more than one pending finding can share an operation id.
The plan assumes it can (a turn may report several findings) and writes one resolution
record each.

## D005-7 — Records written before this feature are never inferred about

**Decision**: absent fields mean **unknown**. The kernel never back-fills, never migrates,
and never treats absence as `unresolved`.

**Rationale**: the same reasoning kernel/004 used to reject a re-signing migration — a
migration stamps a claim onto records whose provenance can no longer be checked. Here it
would additionally be a claim the kernel never observed.

## D005-8 — The pause pair costs two snapshot items, and that is accepted

**Decision**: a paused finding occupies two items in `MemorySnapshot`. No deduplication in
the kernel.

**Rationale**: collapsing the pair is a consumer decision — the pack knows which of the two
its roadmap row should show. Capacity is 10000 items / 32 MiB, fail closed, never
truncated; sessions that pause on many findings reach the cap sooner, and the remedy is
unchanged (complete the session, start a new one). Kernel-side deduplication would mean the
kernel deciding which record represents a finding, which is the interpretation this feature
deliberately leaves to the pack.
