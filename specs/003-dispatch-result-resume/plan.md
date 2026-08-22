# Implementation Plan: DispatchResult, durable scope, and honest turn resume

**Branch**: `003-dispatch-result-resume` | **Date**: 2026-08-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/003-dispatch-result-resume/spec.md` (revision 11, accepted 2026-08-22)

## Summary

`CapabilityPack.dispatch` returns a string today, so nothing a pack computes becomes a
signed memory record, a session has no durable target, and two of three pause states
cannot resume. This feature makes the kernel own the whole durable path: `dispatch`
returns a structured `DispatchResult`, a single `KernelActionExecutor` is the only
production caller of it, payloads are committed exactly once under a deterministic
`operation_id`, pauses persist as one `pause_checkpoint` carrying the Action needed to
re-dispatch, and reads are bounded by the same `ContentScopePolicy` that the content
digest is taken over. Packs gain no memory handle: prior state arrives as an immutable
`MemorySnapshot` argument.

The technical approach is additive around the existing `EpisodicMemory` rather than a
new store. The already-implemented `004-memory-composition-integrity` supplies record
composition (`seq` / `chain_prev` / signed head, project-wide `supersedes`, fail-closed
break); this feature adds a second, project-global `log_sequence` for snapshot ordering
and layers the executor, checkpoint, lease, and scope contracts on top.

## Technical Context

**Language/Version**: Python 3.11+ (dev environment runs 3.14)

**Primary Dependencies**: pydantic v2 (record/DTO models), stdlib `hmac` via
`sr_agent/memory/hmac.py`, stdlib `uuid` (UUIDv5), stdlib `fcntl` (flock), `click` for
operator commands. No new third-party dependency is introduced.

**Storage**: existing append-only JSONL under `memory/<project_id>/<target>.jsonl` plus
`004`'s signed `_chain_head.json`. No database.

**Testing**: pytest (`tests/unit`, `tests/architecture`, `tests/security`), with crash
points exercised by killing/aborting between the write and the `fsync` or checkpoint.

**Target Platform**: local CLI on macOS/Linux; directory `fsync` where the platform
allows it.

**Project Type**: importable library + CLI (`sr_agent`), consumed by capability packs.

**Performance Goals**: no throughput target. One characterized bound: at the snapshot
capacity envelope (10000 items / 32 MiB) the per-append union scan is linear in total
project-log records and bytes, and this cost is measured, recorded, and accepted
(SC-014d).

**Constraints**: MI-resistance ASR stays 0; no memory write handle reaches a pack; every
snapshot / checkpoint body re-enters the model only as DATA; the constants
`MAX_SNAPSHOT_ITEMS = 10000` and `MAX_SNAPSHOT_BYTES = 33554432` live only in kernel
code.

**Scale/Scope**: one writer per project; one audit session per project at a time;
session history bounded by the snapshot capacity above.

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design.*

| Principle | Gate | Status |
|---|---|---|
| I — trust invariants | Snapshot bodies, `external_response` bodies, and checkpoint fields re-enter as DATA; system prompt bytes come from the trusted registry, never from memory; HMAC stays integrity-only; per-turn tool budget restored across resume | PASS (FR-017, FR-020, FR-010a) |
| II — human authority | No new path executes a privileged action from inside a turn; `takeover_lease`, `abandon_session`, `complete_session`, `rebind_scope` are operator-only and absent from model vocabulary; `read_only` dispatch persistence gains no confirmation shortcut | PASS (FR-016, FR-019) |
| III — kernel/pack separation | Persistable kind stays generic `dispatch_payload`; no audit vocabulary in the kernel; prompt registry is a kernel mechanism with pack-owned content; the active pack stays composition-root wired (no dynamic registry) | PASS (FR-003, FR-010c, FR-020) |
| IV — knowledge promotion | Untouched; no observation self-promotes | PASS |
| V — provider agnosticism | `pending` / `local_model_retry` keeps the loop provider-neutral; relay output stays `external_llm_output` | PASS (FR-002) |
| Security requirements | New MI/hostile tests for checkpoint prompt reference, aliased memory objects, and forged identity on both surfaces | PASS (SC-005, SC-011, SC-013) |
| Test-first | Every guarantee below is written as a failing test before its implementation | Enforced in tasks |

No violations to justify, so Complexity Tracking stays empty.

## Project Structure

### Documentation (this feature)

```text
specs/003-dispatch-result-resume/
├── spec.md              # revision 11 (accepted)
├── plan.md              # this file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks, not created here)
```

### Source Code (repository root)

```text
sr_agent/
├── models/
│   ├── memory.py           # + log_sequence (signed, stripped for context)
│   ├── session.py          # + scope_root, content_identity, scope_generation,
│   │                       #   lease_mode, SessionStatus completed/abandoned/detached
│   └── dispatch.py         # NEW: DispatchResult, PendingWait, ActionSnapshot,
│                           #      MemorySnapshot, ContentScopePolicy
├── memory/
│   ├── episodic.py         # + log_sequence allocation over the project union,
│   │                       #   torn-tail recovery, fsync, commit_if_absent,
│   │                       #   put_external_response_if_absent, snapshot builder
│   └── canonical.py        # NEW: canonical encoding + UUIDv5 namespace (FR-005a)
├── orchestrator/
│   ├── executor.py         # NEW: KernelActionExecutor (sole dispatch path)
│   ├── lease.py            # NEW: writer lease state machine
│   ├── prompts.py          # NEW: trusted prompt registry
│   ├── scope.py            # NEW: ContentScopePolicy + rebind_scope
│   ├── loop.py             # resume_turn, pause via one checkpoint
│   ├── pack.py             # DispatchResult in the protocol; PackContext unchanged
│   └── chat_session.py     # session lifecycle APIs
└── tools/
    └── readonly.py         # reads bounded by the include set

tests/
├── unit/                   # canonical encoding, sequence, commit, checkpoint, scope
├── architecture/           # no-write invariant, sole dispatch caller
└── security/               # MI + hostile pack + prompt registry + snapshot provenance
```

**Structure Decision**: the existing single-package layout is kept. New concerns become
new modules under `sr_agent/orchestrator/` and `sr_agent/memory/` rather than growing
`loop.py` and `episodic.py`, because the architecture test (FR-018a) must be able to
name one production dispatch caller and one production write path.

## Phase sequencing

The order below is dependency-driven; it is the input to `/speckit-tasks`.

1. **Foundation — identity and encoding.** `canonical.py` (FR-005a) with its golden
   vector, then deterministic `transition_key` / `operation_id` (FR-005). Everything
   else keys off these, and they are pure functions, so they come first.
2. **Foundation — durable log.** `log_sequence` in the signed shape beside `004`'s
   `seq` / `chain_prev` (FR-006a, D37), union allocation with duplicate/gap validation,
   torn-tail recovery and `fsync` (FR-007a), legacy behaviour inherited from `004`
   (FR-006b).
3. **Commit path.** `commit_if_absent` (FR-006) and
   `put_external_response_if_absent` with a kernel-computed digest (FR-002c).
4. **Read seam.** Six-step snapshot pipeline (FR-009a), capacity constants (FR-009b),
   composition-break withholding (FR-007c).
5. **Session state.** Durable scope + `content_identity` + `scope_generation`
   (FR-013a, FR-013c), lease state machine (FR-014), lifecycle APIs (FR-019).
6. **Executor and pause/resume.** `KernelActionExecutor` (FR-018), `DispatchResult`
   including `pending` (FR-002, FR-002a), one `pause_checkpoint` with the Action
   snapshot (FR-010a/b/c), `resume_turn` (FR-011–FR-013).
7. **Scope enforcement on reads.** `read_file` / `search_code` and the sandbox mount
   bound to the include set (FR-013b).
8. **Prompt registry.** Trusted registry + fail-closed resume (FR-020).
9. **Invariants.** Architecture test (FR-018a), runtime hostile test (FR-018b),
   effect-port idempotency (FR-021), MI coverage (FR-017).

Steps 1–4 are kernel-internal and can be reviewed independently of the loop. Step 6 is
where the user-visible defect (resume losing the target, two pause states not resuming)
actually disappears.

## Risks and how the plan handles them

- **Regressing `004`.** Adding a signed field changes the record shape. Mitigation:
  `004`'s composition tests run unchanged in CI as a gate for step 2, and the legacy
  behaviour is inherited rather than redefined (FR-006b / D39).
- **A second orchestration path appearing by accident.** Mitigation: the architecture
  test lands with step 6, not after it, so any new production caller of `dispatch` or of
  memory writes fails immediately.
- **Crash-consistency claims that are asserted rather than tested.** Mitigation: each
  crash contract names its kill point (before checkpoint, after `fsync` failure, after
  ingest, torn append across two targets), and those are tasks, not remarks.
- **Capacity limit discovered late.** Mitigation: the two constants are fixed in the
  spec and their four boundary cases are tasks in step 4, with the at-limit case doubling
  as the performance characterization.

## Downstream

Pack `araratsec-agent/specs/004-audit-loop-methodology` (revision 12) consumes this
contract and stays blocked on the **implementation** of this feature, not on this plan.
