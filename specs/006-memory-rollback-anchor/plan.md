# Implementation Plan: Rollback detection for the episodic store

**Branch**: `006-memory-rollback-anchor` | **Date**: 2026-09-11 | **Spec**: [spec.md](./spec.md)

**Input**: `specs/006-memory-rollback-anchor/spec.md` — Status Draft. 11 functional
requirements, 7 scenarios. Closes the one attack feature 004 left out of scope.

**Phase 0**: [research.md](./research.md) — D006-1 … D006-9 (two are spec deltas).

**Phase 1**: [data-model.md](./data-model.md), [contracts/rollback-anchor.md](./contracts/rollback-anchor.md),
[quickstart.md](./quickstart.md).

## Summary

A whole-directory rollback restores an older, genuinely-signed copy of `memory/<project>/`
(records + `_chain_head` + `_writer_lease` together). Everything 004 checks compares the
store against itself, so a rollback is invisible: every record verifies and every chain
reconstructs. The corrections written since the backup — including `human_input` supersedes —
silently vanish and the records they retracted return to the pack's projection.

This feature holds one fact outside the store, where the in-scope adversary (write to
`memory/`, no key) cannot reach it: a per-project **rollback anchor** recording the highest
`log_sequence` the log has ever reached, HMAC-signed with the orchestrator key. On the pack
read seam (`snapshot`) and the durable write path, the kernel compares the anchor watermark
`V` to the log's current max `L`. `V > L` means the log got shorter than a point it
provably once passed — a rollback — and the kernel fails closed. `V ≤ L` (steady state, or a
crash that left the anchor lagging) proceeds. The anchor is advanced last on every write, so
our own crash can only leave it behind, never ahead.

The change is additive and outside `memory/`: one constructor/config parameter, one signed
file per project under a separate `anchor_root`, a comparison at two call sites, and an
operator-report field. No `MemoryRecord` field, no signed-shape change, no migration — a
pre-006 store reads unchanged and becomes protected on its first post-006 write.

## Technical Context

**Language/Version**: Python 3.11+ (matches the package; `.venv` runs 3.14, floor is 3.11).

**Primary Dependencies**: stdlib `hmac`/`hashlib` via the existing `sr_agent/memory/hmac.py`
and `canonical.py`. No new dependency (Constitution V — no network, no hardware counter).

**Storage**: unchanged inside `memory/<project_id>/` (append-only JSONL, per-record HMAC,
per-target signed `_chain_head.json`, project-wide `log_sequence`). **New**: one signed
`<anchor_root>/<project_id>.rollback.json` per project, outside `memory_root`.

**Testing**: pytest — `tests/security/` (the rollback harness and the MI bar),
`tests/unit/` (rule table, compat, cache). Test-first is mandatory for the security-critical
items (the rollback detection itself, the fail-closed, the forged-anchor no-DoS) per the
constitution's Development Workflow.

**Target Platform**: `sr_agent` as a library/CLI, consumed as a pin by `audit_agent`.

**Project Type**: single Python package.

**Performance Goals**: no new per-`load()` cost (guard is on `snapshot` + write path only,
D006-4). Under a held lease the anchor is cached in `_ProjectView`, so the hot write path
reads the anchor file at most once per view rebuild — same envelope as 004.

**Constraints**: pre-006 stores MUST keep verifying and reading (FR-006). The rollback signal
MUST stay off the model channel and distinct from a signature failure (FR-009, Constitution I).
No signed-shape change to any record (keeps 004/005 stores valid).

**Scale/Scope**: 11 functional requirements, 7 scenarios. Touches 3 production files
(`memory/episodic.py`, `config.py`, and a one-line env doc); adds no module. Two spec deltas
(D006-2, D006-3) to reconcile before merge.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after the Phase 1 design.*

| Principle | Verdict | Basis |
|---|---|---|
| **I — trust invariants** | **Strengthened** | Adds a detection the store lacked: a rollback to an older valid copy. No trust tier changes; the anchor is never model-facing (FR-011, D006-7); the rollback signal is a composition-class operator signal with no tamper-oracle (D006-6), exactly like 004's chain break. HMAC append-only is untouched — the anchor rides the same signing primitive. |
| **II — human authority** | **Preserved, and this is the point** | The attack this closes specifically erases `human_input` supersedes by rolling the store back. Detecting the rollback is what stops a retracted finding from silently returning. No gate added or removed. |
| **III — kernel / pack separation** | **Held** | Entirely kernel-plane. `PackContext` gains nothing; the pack sees only that `snapshot` may now raise. No domain vocabulary enters the kernel. |
| **IV — knowledge promotion** | **Not engaged** | No knowledge path touched. |
| **V — provider agnostic** | **Held by construction** | The anchor is a local key-signed file — no network, no TSA, no TPM (FR-010, D006-1). The out-of-scope stronger anchor is explicitly declined for this reason. |
| **Security requirements** | **Gated** | MI suite and 004 composition tests must stay green (SC-004); rollback is added as a new security test, test-first. The one fail-closed being *relaxed* (D006-3, missing-anchor) is justified below and buys the in-scope threat nothing. |

**One deviation to record** — D006-3 relaxes the spec's FR-005 fail-closed on *missing
anchor*. This is not a lowered guardrail: the anchor is outside the in-scope adversary's
reach, so a missing anchor is never their doing; fail-closing there only bricks pre-006
stores (a self-DoS) while defending solely against the already-out-of-scope key-store
adversary. The real rollback path (`V > L`) stays fail-closed. Logged in Complexity Tracking.

**Result**: PASS. One justified deviation, no unjustified violation.

## Project Structure

### Documentation (this feature)

```text
specs/006-memory-rollback-anchor/
├── spec.md                       # what & why (FR-005 to be reconciled per D006-3)
├── research.md                   # Phase 0 — D006-1..D006-9
├── plan.md                       # this file
├── data-model.md                 # Phase 1 — the anchor entity, states, the rule
├── contracts/
│   └── rollback-anchor.md        # Phase 1 — composition-root contract + new failure
├── quickstart.md                 # Phase 1 — the seven scenarios, runnable
├── checklists/
│   └── requirements.md           # spec quality gate (passed)
└── tasks.md                      # Phase 2 — /speckit-tasks, NOT created here
```

### Source code (repository root)

```text
sr_agent/
├── memory/
│   └── episodic.py     # + anchor_root param & no-inside-memory_root guard (D006-1)
│                       # + _anchor_path/_read_anchor/_bump_anchor (sign/verify like _head)
│                       # + _check_rollback(project_id, log_max)          (D006-3 rule)
│                       # + call in write() after _write_head (bump) and pre-append (check)
│                       # + call in snapshot() after _authenticated_project (check)
│                       # + _ProjectView.anchor cache field + _extend_cache maintenance
│                       # + IntegrityReport.rollbacks + verify_integrity population
│                       # + MemoryRollbackDetected exception
└── config.py           # + anchor_root field + SR_ANCHOR_ROOT (default None/unset)

tests/
├── security/
│   └── test_memory_rollback.py   # US1/US2: full-dir rollback -> snapshot & write fail closed;
│                                  #   forged anchor no-DoS; operator report; MI bar unchanged
└── unit/
    ├── test_rollback_rule.py     # the V vs L table incl. crash-lag, equal, absent (D006-3)
    ├── test_rollback_compat.py   # pre-006 store loads; first write adopts (FR-006, D006-8)
    └── test_rollback_cache.py    # anchor carried in _ProjectView; bump on append (D006-4/5)
```

**Structure Decision**: no new module. The anchor is a sibling of `_chain_head`/`_writer_lease`
— same signing primitive, same "signed sidecar the record globs must not pick up" pattern — so
it belongs in `episodic.py` beside them, not in a separate file that would split the memory
integrity story across two places.

## Phase sequencing

Test-first for every security-critical item. Each phase leaves the suite green → its own commit.

### Phase A — the anchor primitive, construction & monotonic bump (D006-1, D006-2, D006-5)

`anchor_root` on `EpisodicMemory.__init__` and `KernelConfig`; construction raises if
`anchor_root` is inside `memory_root`. `_anchor_path`, `_read_anchor` (verify → int | None),
`_write_anchor` (atomic tmp-swap, signed) — copied structurally from `_read_head`/`_write_head`.
First test written: a forged/absent anchor reads as `None` and a signed one round-trips.

**The monotonic bump is foundational, not part of the write-check phase.** `_bump_anchor`
advances the anchor after `_write_head` in `write()`, so the anchor is the last durable
artifact of a write (a crash can only leave it *behind* the log). This must land here, before
either detection phase: the read check (Phase B) and the write check (Phase C) have nothing to
compare against until writes actually record a watermark, so both stories are only
independently testable once the bump exists. (In `tasks.md` this is Foundational T007; the plan
originally placed the bump under Phase C — corrected here to match the task grouping.)

### Phase B — the rule & the read seam (D006-3, D006-4; FR-003)

`_check_rollback(project_id, log_max)` implementing the table (∅/≤/>). Wire into `snapshot()`
after `_authenticated_project`/watermark pin. Test-first with the full-directory rollback
harness (D006-9): the first assertion is `snapshot()` raising `MemoryRollbackDetected` on a
restored older `memory/`.

### Phase C — the write-path check (FR-007)

`_check_rollback` before the append in `write()` (the bump itself already landed in Phase A).
Tests: write refused onto a rolled-back log; crash-lag (`V<L`) never raises and the next write
catches up; steady growth never raises.

### Phase D — cache, compat, forged-anchor (D006-4, D006-8; FR-006, SC-005)

`_ProjectView.anchor` + `_extend_cache` maintenance; pre-006 adopt-on-first-write; forged
anchor treated as absent (no lockout). `test_rollback_compat` and `test_rollback_cache`.

### Phase E — operator report, docs, gates (D006-6; FR-009)

`IntegrityReport.rollbacks` + `verify_integrity` population + `_report_rollback` (WARNING,
operator channel). Then: full suite, the security + MI harness at its existing bar (SC-004),
`docs/kernel.{md,ru.md}` and `docs/mi-threat-model.{md,ru.md}` (the rollback vector now moves
from "out of scope" to "neutralised by the anchor"), and reconcile `spec.md` FR-005 per D006-3.

## Risks

- **The FR-005 reconciliation (D006-3).** Relaxing a fail-closed is exactly the kind of change
  that looks like a weakening. Mitigated by the argument in research (missing-anchor is
  unreachable by the in-scope adversary) and by a test proving a *full rollback* still fails
  closed — the guarantee that matters is pinned, only the innocent-store false-positive is
  removed. Needs operator sign-off before the spec text is edited.
- **`anchor_root` misdeployment.** If the operator points `anchor_root` at a path the
  memory-write adversary can also write, the guarantee silently drops to nothing. The kernel
  cannot detect "same access boundary", only "inside memory_root". Mitigated by the
  construction-time inside-memory_root guard and a loud note in the contract + threat-model doc.
  This is the same class of assumption as "SR_SECRET_KEY is not readable from memory/".
- **Guard inert when `anchor_root is None`.** A writer built without it has no protection. This
  is intended (mechanism vs. wiring, like the lease), but a composition root that forgets to
  wire it ships unprotected. Mitigated by the contract making writer-role wiring mandatory and
  by Repo B's integration picking it up from `KernelConfig`.
- **Pre-first-anchor window.** A rollback of a project that has never been anchored is
  undetectable (nothing to compare). Inherent to any watermark; documented (SC-003 protects
  from the first post-006 write onward), not hidden.

## Downstream

Repo B (`audit_agent`) — the composition root — must: add `SR_ANCHOR_ROOT` to its config load,
place it outside `memory_root` on a hardened boundary, pass `anchor_root` when constructing the
session writer memory, and handle `MemoryRollbackDetected` on the `snapshot`/`write` paths
(surface to operator, do not retry blindly). This is a config + wiring change, not a code
contract change; the contract doc ships with the commit pin.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected because |
|---|---|---|
| Relax FR-005 fail-closed on *missing* anchor (D006-3) | FR-005 as written contradicts FR-006 and bricks every pre-006 store on first read; the case it defends (anchor deleted) is unreachable by the in-scope adversary | Keeping fail-closed needs a signed in-`_chain_head` "anchored" marker to tell pre-006 from anchor-deleted — a head shape change + 004 interaction to defend an already-out-of-scope adversary. Rejected by YAGNI. |
