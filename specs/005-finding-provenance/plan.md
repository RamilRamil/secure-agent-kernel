# Implementation Plan: provenance for model-reported findings

**Branch**: `feat/005-finding-provenance` | **Date**: 2026-08-22 | **Spec**: [spec.md](./spec.md)

**Input**: `specs/005-finding-provenance/spec.md` — Status Draft, every decision closed
with Repo B (D-A/D-B/D-C, B-1, B-2, FR-016/FR-017). 17 functional requirements.

**Phase 0**: [research.md](./research.md) — D005-1 … D005-8.

**Cross-repo**: this is the kernel half of pack/003 FR-008a, and the hard start blocker for
pack/005-proof-loop-closure. Kernel and pack number features independently; references are
written `kernel/NNN` / `pack/NNN`.

## Summary

`OrchestratorLoop` persists a model-reported finding the moment the model reports it, in
both entry points, before the terminal check, before the unknown-action check, before
`validate_action` and before `executor.execute`. A signed `Finding` therefore exists whether
the turn ran a tool, proposed a rejected action, named a nonexistent one, or ended on
`complete`. Nothing in the record distinguishes those.

This feature moves the write to after the action resolves and stamps what happened onto the
record: `action_resolution`, `action_operation_id`, `action_dispatch_status`, plus
`resolves_record_id` on the record written when a paused turn resumes. The four fields are
exposed on `SnapshotItem`, because `MemorySnapshot` is the pack's only read seam.

The kernel does **not** say a finding is grounded. It reports the outcome of the action
proposed in the same `AgentAction`, which is co-occurrence within one model turn, not
derivation. The consumer's grounding policy sits on top of those facts.

The change is additive: four optional fields, one moved call site per loop path, one lookup
on resume, four passthrough fields on the snapshot item. No new module, no new store, and —
by D005-3 — no change to the signed shape of any record that does not carry the fields.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: pydantic v2 (`extra="forbid"`, `frozen=True` on the snapshot
models), stdlib `hmac` / `hashlib`. No new dependency.

**Storage**: append-only JSONL under `memory/<project_id>/`, HMAC-signed per record, chained
per target with a signed `_chain_head.json`, ordered project-wide by `log_sequence`.
Unchanged: no new file, no new kind, no migration.

**Testing**: pytest — `tests/unit/`, `tests/security/`, `tests/architecture/`. Test-first is
mandatory for the security-critical items (FR-003, FR-005, FR-012, FR-015) per the
constitution's Development Workflow section.

**Target Platform**: `sr_agent` as a library/CLI, consumed as a pin by `audit_agent`.

**Project Type**: single Python package.

**Performance Goals**: no new read on the finding write path. Resume adds one project-scoped
verified read to find pending finding records — the same read `latest_checkpoint` already
performs on that path, so no new order of cost.

**Constraints**: the signed shape of a pre-005 record MUST NOT change (FR-015). Findings MUST
stay inside `SNAPSHOT_KINDS` (FR-013). `_enforce_status_rules` MUST NOT be relaxed (D005-4).

**Scale/Scope**: 17 functional requirements, 7 scenarios. Touches 5 production files; adds
no module.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after the Phase 1 design below.*

| Principle | Verdict | Basis |
|---|---|---|
| **I — trust invariants** | **Strengthened** | The tier is unchanged (`external_llm_output`, FR-005) and no promotion path is added. What changes is that a record can no longer be read as more than it is: today a hypothesis and a post-tool claim are indistinguishable. FR-004 is the load-bearing half — the kernel refuses to emit a `grounded` flag it never verified, because a manufactured evidence tier is worse than the present gap. D005-3 shows the only reachable manipulation of the new fields points away from false evidence. |
| **II — human authority** | **No expansion, no erosion** | No gate is added or removed. D005-4 specifically declines to make the kernel a second authority able to cancel a record; `supersedes` stays human-only. The pause path stamps `pending` and does not shortcut the out-of-band channel. |
| **III — kernel / pack separation** | **Held at its current depth** | FR-011: the kernel's knowledge of the domain `Finding` stays exactly `.location`, and the provenance fields never enter `finding.model_dump()` (FR-017). D005-1 keeps the write in the loop rather than moving domain vocabulary into the executor. `PackContext` gains nothing. The interpretation — what counts as grounded — stays with the pack by construction, not by convention. |
| **IV — knowledge promotion** | **Unchanged** | Nothing here promotes a finding to knowledge. A `resolved` + `ran` stamp is an input to a consumer's policy, explicitly not a verdict (FR-004). |
| **V — provider agnostic** | **Not engaged** | No provider-conditional behaviour. |
| **Security requirements** | **Gated** | No existing security test is deleted or weakened; `compare_digest`, silent drop, project isolation and the chain are untouched. New coverage is added for the new fields rather than assumed. |

**Result**: PASS, no violations. Complexity Tracking is empty by design.

## Project Structure

### Documentation (this feature)

```text
specs/005-finding-provenance/
├── spec.md                            # decisions closed with Repo B
├── research.md                        # Phase 0 — D005-1..D005-8
├── plan.md                            # this file
├── data-model.md                      # Phase 1 — the four fields and their legal combinations
├── contracts/
│   └── finding-provenance.md          # Phase 1 — the contract owed to Repo B
├── quickstart.md                      # Phase 1 — runnable walkthrough of all seven scenarios
├── checklists/
│   └── requirements.md                # spec quality gate
└── tasks.md                           # Phase 2 — /speckit-tasks, NOT created here
```

### Source code (repository root)

```text
sr_agent/
├── models/
│   ├── memory.py        # + 4 optional fields, + combination validator,
│   │                    #   + conditional exclusion in fields_for_hmac  (FR-003, FR-012, FR-015)
│   └── dispatch.py      # + 4 fields on SnapshotItem                    (FR-016)
├── memory/
│   └── episodic.py      # + passthrough into SnapshotItem               (FR-016)
└── orchestrator/
    └── loop.py          # persist moved after resolution in BOTH paths,
                         #   + resume-side resolution record             (FR-001, FR-006, D005-6)

tests/
├── unit/
│   ├── test_finding_provenance_fields.py   # the combination table, legal + illegal
│   ├── test_finding_provenance_paths.py    # scenarios 1-7 against BOTH loop entry points
│   └── test_finding_provenance_snapshot.py # FR-013 + FR-016: still a "finding", fields visible
├── security/
│   ├── test_finding_provenance_integrity.py # FR-015 + D005-3: legacy verifies; strip only downgrades
│   └── test_memory_composition.py           # unchanged, must stay green (supersedes untouched)
└── architecture/
    └── test_finding_persist_ordering.py     # FR-006: no persist before resolution, in either path
```

**Structure Decision**: no new module. The record shape belongs to `models/memory.py`, the
projection to `models/dispatch.py` + `episodic.py`, and the ordering to `loop.py`. A separate
"provenance" module would put the stamp somewhere other than the two places that already own
the write and the read.

## Phase sequencing

Test-first for every security-critical item. Each phase leaves the suite green, so each phase
can be its own commit.

### Phase A — the record fields (FR-003, FR-012, FR-015; D005-2, D005-3)

Four optional fields on `MemoryRecord`, plus a `model_validator` enforcing the legal
combinations from `data-model.md` — an illegal combination (for example `resolved` with no
`action_operation_id`, or `unresolved` carrying a status) is a write-time error, not a
silently-stored contradiction.

`fields_for_hmac()` omits each of the four when unset. The first test written in this feature
is the one that loads a store created before the change and asserts every record still
verifies; it fails against a naive implementation and is the reason the phase exists on its
own.

`for_llm_context()` strips all four (FR-010). They are not signature material, but
`log_sequence` is not either and is stripped for the reason that applies here more strongly:
a model that can see which of its earlier findings carry `resolved` + `ran` can optimise for
that state. The fields reach the pack through `SnapshotItem`, never through model context.

Independently valuable: the shape and its integrity property are pinned before anything
writes the fields.

### Phase B — the ordering in both loop paths (FR-001, FR-002, FR-006, FR-007; D005-1)

`_persist_finding(agent_action, provenance)` gains the required second argument. Call sites
move below the point where the outcome is known, in `run` and in `run_turn` alike:

| Turn shape | `action_resolution` | Where the call now sits |
|---|---|---|
| terminal (`complete` / `escalate`) | `unresolved` | inside the terminal branch, before the return |
| unknown `next_action` | `unresolved` | inside the unknown branch, before the continue |
| `validate_action` rejected | `unresolved` | after the rejection is turned into data |
| dispatch returned a terminal status | `resolved` | after `executor.execute` returns |
| dispatch returned `pending` | `pending` | after `executor.execute` returns |

FR-007 is unchanged behaviour and gets a regression test rather than new code: a pack that
declines to build the finding still produces no record.

### Phase C — the resume record (D-A, FR-014; D005-6)

On the resume path, after the dispatch resolves, find this session's finding records with
`action_resolution == "pending"` and a matching `action_operation_id`, and write one
resolution record per match carrying `resolves_record_id`. No `supersedes`, no tier change.
Both records survive in the store and in the snapshot.

### Phase D — snapshot exposure (FR-016, FR-017; D005-5)

Four fields onto `SnapshotItem` (which is `extra="forbid"`, so this is a real model change),
populated from the record envelope in `EpisodicMemory.snapshot`. `payload_kind` stays empty on
findings, so `_snapshot_kind` keeps returning `"finding"` and they stay in `SNAPSHOT_KINDS`.

### Phase E — contract, docs, gates

The contract in `contracts/finding-provenance.md` is the deliverable Repo B is waiting on and
ships with the commit pin. Then: full suite, the security and architecture suites, the MI
harness at its existing bar, and `docs/kernel.{md,ru.md}`.

## Risks

- **The eight call sites.** Two loop paths × four exits is the largest source of a silent
  divergence, and it is exactly what the old code got wrong in one direction. Mitigated
  twice: the required provenance argument (D005-1) makes an early call impossible to write
  accidentally, and the scenario table in `test_finding_provenance_paths.py` is parametrised
  over **both** entry points from one table, so a path that stops covering a scenario fails
  rather than drifts.
- **A crash between the pause record and the resume record.** The pending record survives —
  that is why D-A writes it — and reads as `pending`, which the consumer treats as a
  hypothesis. Losing the resolution record loses evidence, never manufactures it.
- **Snapshot capacity.** The pause pair doubles items for paused findings against a
  fail-closed 10000 / 32 MiB envelope (D005-8). Accepted; documented for the consumer.
- **`SnapshotItem` is frozen and forbids extras.** Any consumer constructing one by hand
  breaks. Repo B does not; the risk is noted so a future second pack is not surprised.

## Downstream

Repo B (`audit_agent`): pack/005 starts on the commit pin plus the written contract, not on
this plan. Their projector will list a paused finding as two rows until they update — their
work, explicitly not a reason to revisit `supersedes`.
