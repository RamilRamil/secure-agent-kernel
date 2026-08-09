# Implementation Plan: Task-agnostic action taxonomy & contract naming

**Branch**: `001-task-agnostic-contract` | **Date**: 2026-08-09 | **Spec**: [spec.md](spec.md)

**Repository**: secure-agent-kernel (Repo A, `sr_agent`). Paired with araratsec-agent `002-pack-owns-action-taxonomy`.

**Input**: Feature specification from `specs/001-task-agnostic-contract/spec.md`

## Summary

The kernel hardcodes audit vocabulary that Constitution III assigns to a pack: the closed `ActionType` enum (`action.py`), the domain privileged-status set (`memory.py:34`), the audit `TOOL_REGISTRY`, the per-action `_validate_params` ladder, plus audit-flavored names (`audit_root`, `AuditResult`, `PackContext.poc_*`) and an audit default prompt. This feature closes that gap.

Technical approach, grounded in the code as it stands:

- **Open the taxonomy** — `Action.action_type: ActionType` (`action.py:85`) becomes `str`. Introduce two *named, distinct* kernel-owned sets: `KERNEL_GENERIC_ACTIONS = {write_memory, request_human_confirmation, read_file, search_code}` (resolvable, carry kernel `ActionSpec`s — the control/memory machinery plus the generic scope-bounded reads, D6) and `LOOP_TERMINALS = {escalate, complete}` (intercepted before validation, no `ActionSpec`). `validate_action` resolves ids that reach it against `KERNEL_GENERIC_ACTIONS ∪ pack.actions`, fail-closed. The `validate_action` pack-path already keys by string and fail-closes on `pack.actions.get(key) is None` (`action.py:53–71`) — so the mechanism exists; this feature removes the enum annotation and the legacy `pack is None` branch, and relocates the entry fail-closed that today lives in `ActionType(next_action)` coercion (`loop.py:202`/`:357`) and the chat guard `_value2member_map_` (`loop.py:349`).
- **Pack-supplied privileged statuses, bound at session construction** — build a new coupling edge (pack → memory plane): the kernel composes the effective set from `pack.privileged_statuses` once at session/memory construction and injects it into `EpisodicMemory` (today `_enforce_status_rules` is a `@staticmethod` reading a module constant, `episodic.py:210`; `__init__` has no pack ref, `episodic.py:47`). Immutable for the session.
- **Renames with a shim** — `audit_root`→`scope_root` (frozen `PackContext` ⇒ `__getattr__` delegation shim for one release), `AuditResult`→`RunResult` (`loop.py:181,191`; `TurnResult` untouched), neutral default prompt (`claude_client.py`).
- **Remove PoC context** — `PackContext.poc_dir`/`poc_generator` deleted (D2 = removal).
- **Pin it** — new boundary test **B6** (asserts `KERNEL_GENERIC_ACTIONS`/`LOOP_TERMINALS` exactly, no domain id/status in `sr_agent/`), new hostile-pack **H4** (empty/reduced `privileged_statuses`), positive **SC-005** fixture-pack test (primary proof), and **SC-009** chat-path test. Test-first: all four are written to fail first.

The MI guarantee (Constitution I) is held by the *unchanged* security suite as an independent guard; this feature adds tests, weakens none.

## Technical Context

**Language/Version**: Python 3.11 (`sr_agent` + tests).

**Primary Dependencies**: standard library + pydantic (models) for the changed paths; pytest. No new dependency. `secure-agent-kernel` is consumed downstream by araratsec via a pinned version — the contract this feature changes.

**Storage**: Episodic memory is append-only HMAC-signed JSONL. **Not touched by the renames** — HMAC signs `record.fields_for_hmac()` (MemoryRecord fields), and `audit_root`/`scope_root` is a path parameter, not a signed field (`memory/hmac.py`, `episodic.py:76`). The only memory-plane change is *injecting* the bound privileged-status set at construction (no record-format change).

**Testing**: pytest under `tests/security/` (MI + hostile-pack, incl. new H4), `tests/architecture/` (boundary, incl. new B6), and `tests/fixtures/pack/` (the kernel-only FIXTURE_PACK that SC-005/SC-009 drive). No reasoning backend, Docker, or network needed for the Secure axis.

**Target Platform**: Linux/macOS dev; the kernel is importable-library only (no composition root ships here).

**Project Type**: single library — the task-agnostic secure kernel.

**Performance Goals**: N/A. Resolution changes from an enum lookup to a dict-union lookup; negligible.

**Constraints**: Constitution I NON-NEGOTIABLE (protected MI ASR = 0, differential ≥ 0.40 unchanged); every merge boundary of the 3-PR sequence leaves both repos green (FR-016).

**Scale/Scope**: kernel side ≈15 `audit_root` refs + the enum/status/validator relocation; ~6 source files edited, 3 test files touched (B6, H4, fixture), 2 doc bundles (EN+RU) reconciled. The wide churn (`audit_root` ≈72 core/≈91 repo-wide) is on the *araratsec* side (feature 002), not here.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Secure-Kernel Trust Invariants (NON-NEGOTIABLE)** — **Held, not weakened.** DATA-wrapping, SourceType hierarchy, HMAC append-only, tool-budget are untouched. The status gate's *rule* is unchanged; only the *source* of its membership moves (US2). The independent guard is the pre-existing MI suite (protected ASR = 0, differential ≥ 0.40), which this feature must leave green and does not author. ✅ (verified by SC-004).
- **II. Human Authority for Privileged & Irreversible Actions** — ⚠️ **TOUCHED — deliberate, governed narrowing.** Today the kernel gates `{verified_safe, skip_analysis, audit_complete}` *unconditionally*; after US2 the gate covers exactly what the active pack declares (bound at session start, immutable by turn — D5). This relocates the *locus* of a near-non-negotiable guarantee. It is legitimate (a pack under-declaring only weakens its own gate; post-change there is no kernel-default status to bypass) and made **intentional and tested by H4**. It requires a constitution amendment (Principle II wording, **MINOR bump 2.0.0 → 2.1.0**) as part of Definition of Done. Justified in **Complexity Tracking**. The confirmation gate for `write_execute` actions is *unchanged* — still kernel-derived from `action_class`, and a pack still cannot skip it (H1). ✅ with amendment.
- **III. Kernel / Capability-Pack Separation** — **Brought into compliance.** III already *mandates* "concrete action types … domain privileged-statuses … lives in a capability pack"; this feature is the code catching up. The **kernel-generic exception** (`KERNEL_GENERIC_ACTIONS`/`LOOP_TERMINALS` stay kernel-owned) is *not* domain vocabulary and so not a violation: the control/memory machinery and loop signals are Principle I/II machinery, and the generic scope-bounded reads `read_file`/`search_code` (D6) are generic capability whose containment guard is a Principle I safety primitive (they are kept kernel-owned by anti-duplication over that primitive, not by the structural necessity that binds the machinery — see D6's weight note) — neither is a "concrete domain action type." The amendment note records the exception so a future reader does not misread it. No dynamic plugin registry (YAGNI; still one pack). ✅ (improves compliance).
- **IV. Human-Gated Knowledge Promotion** — Not touched. No lesson/knowledge promotion path involved. ✅
- **V. Provider-Agnostic Kernel, Smallest Capable Model** — **Improved.** Neutralizing the default system prompt (FR-013) removes an audit assumption from the kernel; provider-agnosticism is unaffected (the prompt is a capability default, never a security lever). ✅
- **Development Workflow & Quality Gates** — Test-first: B6, H4, SC-005, SC-009 written to fail first. Reuse-first: `ActionSpec`/`CapabilityPack.actions`/`privileged_statuses`/the string-keyed `validate_action` path already exist — this is relocation, not new abstraction. Commit only on request; Co-Authored-By trailer. ✅

**Result: PASS with one governed exception (Principle II narrowing), justified in Complexity Tracking and discharged by the MINOR amendment in the DoD.** Re-check after Phase 1: unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/001-task-agnostic-contract/
├── spec.md              # Feature spec (done, round-2 reviewed)
├── plan.md              # This file
├── research.md          # Phase 0 — resolves the open items below (NOT yet written)
├── data-model.md        # Phase 1 — the action-id/status/ActionSpec model deltas
├── contracts/
│   ├── action-resolution.md      # validate_action over KERNEL_GENERIC_ACTIONS ∪ pack.actions
│   └── privileged-status-binding.md  # pack → memory binding at session construction
├── quickstart.md        # Phase 1 — how to run B6/H4/SC-005/SC-009 locally
└── tasks.md             # Phase 2 (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
sr_agent/
├── models/
│   ├── action.py            # EDIT: Action.action_type: ActionType → str (L85); remove the
│   │                        #       domain ActionType enum + ACTION_CLASS_MAP + REVERSIBLE
│   │                        #       (they move to the pack); define KERNEL_GENERIC_ACTIONS
│   │                        #       {write_memory, request_human_confirmation, read_file,
│   │                        #       search_code} + their ActionSpecs (reads: read_only/reversible,
│   │                        #       validate_params = containment — D6) and LOOP_TERMINALS
│   │                        #       {escalate, complete} constants.
│   └── memory.py            # EDIT: REQUIRES_HUMAN_CONFIRMATION (L34) emptied of domain statuses
│                            #       (becomes kernel-generic/empty); membership now bound from pack.
├── orchestrator/
│   ├── action.py            # EDIT: resolve id vs KERNEL_GENERIC_ACTIONS ∪ pack.actions; drop the
│   │                        #       legacy `pack is None` branch (enum-indexed, breaks under str);
│   │                        #       validate_params now comes only from the resolved ActionSpec.
│   ├── pack.py              # EDIT: PackContext — audit_root→scope_root (+ __getattr__ shim),
│   │                        #       remove poc_dir/poc_generator (D2). CapabilityPack unchanged.
│   └── loop.py              # EDIT: AuditResult→RunResult (L181,191); terminals reference
│                            #       LOOP_TERMINALS constants (L180/189/341); chat guard L349
│                            #       consults the union, not _value2member_map_; L202/357 no
│                            #       longer coerce to enum — build Action(action_type=str).
├── memory/
│   └── episodic.py          # EDIT: inject the bound privileged-status set at __init__ (L47);
│                            #       _enforce_status_rules (L210) consults the bound set, not the
│                            #       module constant. New pack→memory coupling edge (D5).
├── tools/
│   ├── registry.py          # PARTIAL MOVE-OUT: the audit *analyzer* TOOL_REGISTRY entries →
│   │                        #           the pack (feature 002); read_file/search_code entries STAY
│   │                        #           (kernel-generic, D6). scope_root rename for kept plumbing.
│   └── readonly.py          # EDIT: STAYS kernel (read_file/search_code are kernel-generic, D6);
│                            #       _contained/read_file audit_root → scope_root; de-domain
│                            #       search_code (.sol default + "Solidity" wording → file_ext is a
│                            #       REQUIRED param, no default: a missed call-site fails loud, not
│                            #       silent). Path-containment guard is a generic safety primitive.
├── orchestrator/action.py   # PARTITION (D6, not whole move): the read_file/search_code branches
│                            #       (L93–99) + _check_filepath STAY kernel as the generic reads'
│                            #       validate_params; only the domain-analyzer branches (L101–121)
│                            #       move to the pack (feature 002), importing the kernel helper.
└── llm_core/
    └── claude_client.py     # EDIT: default system prompt → task-neutral (L19); audit wording is
                             #       the pack's reasoning_prompt (already overrides).

tests/
├── architecture/
│   └── test_kernel_pack_boundary.py  # ADD B6: assert no domain id/status in sr_agent/ (read_file/
│                                     #        search_code excluded — kernel-generic, D6);
│                                     #        KERNEL_GENERIC_ACTIONS == {write_memory,
│                                     #        request_human_confirmation, read_file, search_code},
│                                     #        LOOP_TERMINALS == {escalate, complete}; remove B3
│                                     #        poc_* tolerance.
├── security/
│   └── test_hostile_pack.py          # ADD H4: pack with empty/reduced privileged_statuses.
└── fixtures/
    └── pack/fixture_pack.py          # EDIT: kernel-only FIXTURE_PACK gains an invented domain id
                                      #       + invented privileged status → drives SC-005 (validate→
                                      #       confirm + status reject + inherited write_memory) and
                                      #       SC-009 (same via run_turn / chat path).

docs/
├── kernel.md / kernel.ru.md                       # EDIT: "task-agnostic residue" note → resolved.
└── capability-pack-interface.md / .ru.md          # EDIT: pack owns domain taxonomy + statuses;
                                                   #       kernel retains generic (non-domain) ids:
                                                   #       control/memory machinery + reads (D6).

.specify/memory/constitution.md                    # EDIT (DoD): mirror the Principle II MINOR
                                                   #             amendment (2.1.0); source of truth
                                                   #             is araratsec's copy.
```

**Structure Decision**: Single library. Edits stay within `sr_agent/`, `tests/`, `docs/`. The MOVE-OUT items — the audit *analyzer* `TOOL_REGISTRY` entries and the domain-analyzer branches of `_validate_params` — are **fully removed from the kernel in PR-1**, not kept behind a domain compat. B6 (added in PR-1) asserts no domain id lives in `sr_agent/`; keeping `run_slither`/`run_mythril`/… behind a compat would make B6 red in PR-1, and the hedge is self-defeating anyway — once the kernel suite runs on FIXTURE_PACK (no domain ids), there is nothing for a domain compat to keep green. The pinned dependency is what makes this safe: araratsec keeps consuming the *pre-PR-1* kernel until it migrates in PR-2, so the kernel need not carry an audit compat for it. Two things are **not** removed and are not a hole: (1) the `audit_root`→`scope_root` shim (a name alias, orthogonal to the domain move — removed later in PR-3), and (2) the `read_file`/`search_code` registry entries + read validators, which stay kernel-side by D6.

## Phasing (maps to the 3-PR coordination sequence)

- **Phase 0 — research.md**: resolve the open items below.
- **Phase 1 — design**: `data-model.md` (action-id/status/ActionSpec deltas), `contracts/` (resolution + binding), `quickstart.md`. Write B6/H4/SC-005/SC-009 as failing tests.
- **Phase 2 — /speckit-tasks** (later): task list.
- **Implementation = PR-1** (this repo, with shim) → araratsec **PR-2** → this repo **PR-3** (shim removal). FR-016 keeps every boundary green.

### Phase 0 open items (for research.md)

1. **Kernel-generic `ActionSpec` shapes** — the exact `action_class` for `write_memory` (`memory`) and `request_human_confirmation` (`control`) and their `validate_params` (likely a no-op/param-shape check), so `KERNEL_GENERIC_ACTIONS` is self-contained without the audit validator.
2. **Binding mechanism (D5)** — where the session/memory plane is constructed and how the bound set is threaded into `EpisodicMemory` (constructor arg vs a session-scoped wrapper) such that it is immutable by any model turn. Confirm no code path reconstructs the set mid-session.
3. **Tool-registry split — RESOLVED by D6** (was an open question; the round-3 review required it be a decision, not research). The partition is settled: `read_file`/`search_code` (ids, registry entries, `readonly.py` tools, and their validator branches) STAY kernel-side as `KERNEL_GENERIC_ACTIONS` reads; the domain analyzers (`run_slither`/`run_mythril`/`build_graph`/`analyze_transactions`/`decompile_bytecode`/`write_poc`/`run_tests`/`deploy_test_contract`) move to the pack. The only residual mechanical items for research.md: (a) confirm the exact `TOOL_REGISTRY` entries that stay vs move, and (b) de-domain `search_code`'s `.sol` default + "Solidity" description — make `file_ext` a **required** parameter (no default) so a missed pack call-site fails loud (`TypeError`) rather than silently changing search scope (SC-004 does not snapshot `search_code` results).
4. **De-domaining the kernel's own tests** — which existing kernel tests reference the domain enum/`TOOL_REGISTRY` and must be rehosted on FIXTURE_PACK before PR-1 can be green.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| **Principle II narrowing** — the privileged-status gate becomes pack-declared (bound at session start) rather than kernel-unconditional. | Constitution III explicitly requires domain privileged-statuses to live in the pack; keeping them kernel-hardcoded is the very violation this feature closes, and duplicating them (kernel *and* pack) invites drift. | Keeping the statuses kernel-side (status quo) rejected: it violates III and leaves the duplication with `AUDIT_PRIVILEGED_STATUSES`. Making the kernel *union* its own hardcoded set with the pack's rejected: it re-introduces domain vocabulary in the kernel and makes B6 unwritable. The narrowing is bounded (a pack only weakens its own gate) and pinned by H4; discharged by the MINOR constitution amendment (2.1.0) in the DoD. |
| **New pack → memory coupling edge (D5)** | US2 cannot consult `pack.privileged_statuses` without threading it into the memory plane, which today has no pack reference. | A global/module-level set (status quo) rejected: it is exactly the hardcoded-domain-status violation. A per-write pack lookup rejected: it would let a mid-session change alter the gate, breaking immutability. Binding once at construction is the minimal edge that preserves immutability. |
