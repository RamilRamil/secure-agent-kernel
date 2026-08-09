---
description: "Task list — Task-agnostic action taxonomy & contract naming (kernel, PR-1 + PR-3)"
---

# Tasks: Task-agnostic action taxonomy & contract naming

**Input**: [spec.md](spec.md), [plan.md](plan.md) (Phase 0/1 artifacts folded in — see Notes)

**Repository**: secure-agent-kernel (Repo A, `sr_agent`). Paired with araratsec `002-pack-owns-action-taxonomy`.

**Tests**: INCLUDED and **mandatory** — the constitution requires test-first, and B6/H4/SC-005/SC-009 are the feature's deliverable pins. Every test task is authored to **FAIL first**, before the implementation it guards.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no ordering dependency).
- **[Story]**: US1–US5 (traceability to spec user stories).
- Exact file paths + FR/SC/D ids are in each task.

> **Scope note — this is not the template's "independent MVP slices" shape.** FR-016 requires every merge boundary green, so US1–US3 (all P1) ship as **one atomic PR-1**; US4/US5 (P2) ride the same PR. They are grouped by story for traceability, **not** as separately shippable increments. PR-3 (shim removal) is a distinct later merge, gated on araratsec PR-2.
>
> **Governance ordering (resolves the mirror-before-source trap).** The Principle II MINOR amendment (2.0.0 → 2.1.0) is **authored in araratsec** (the constitution's source of truth) as a **standalone governance commit merged BEFORE this PR-1** — not inside a code PR. This kernel PR-1 only **mirrors** that already-merged amendment (T025), so the two `constitution.md` copies are never out of sync at a merge boundary. The full delivery order is: **araratsec governance commit → kernel PR-1 → araratsec PR-2 → kernel PR-3.**

---

## Phase 1: Setup & before-baseline

- [ ] T001 Confirm branch `001-task-agnostic-contract`; dev-install the kernel editable (`pip install -e .`), export `SR_SECRET_KEY`; run `pytest tests/security tests/architecture` **green** and record the before-baseline numbers (protected ASR, baseline ASR, differential) — the "before" half of SC-004's before/after. (FR-010, SC-004)
- [ ] T002 [P] Inventory every **existing** kernel test that constructs `Action(action_type=ActionType.…)` or references the domain `ActionType` / `TOOL_REGISTRY` / audit statuses (e.g. under `tests/security`, `tests/memory`, `tests/orchestrator`); this is the worklist **consumed by T009** (the rehost). These tests break on import the moment T006 removes the enum, so the inventory is a hard prerequisite for a green PR-1 (FR-016). Files: `tests/**`.

---

## Phase 2: Foundational (blocking prerequisite)

**⚠️ CRITICAL**: the fixture asset below is consumed by every test in US1–US3 and must exist first.

- [ ] T003 Author the kernel-only **FIXTURE_PACK** in `tests/fixtures/pack/fixture_pack.py`: an invented **domain** id `do_thing` = `ActionSpec(action_class=write_execute, is_reversible=False, validate_params=…)`, an invented privileged status `blessed`, and **no** control/memory/read ids declared (so the tests prove those are inherited from the kernel). Drives SC-005, SC-009, US1-S2, US2. (Note: H4's *empty*-`privileged_statuses` case needs a **separate minimal pack** declaring none — see T010, since this fixture declares `blessed`.) (SC-005, FR-002)

**Checkpoint**: fixture ready — US1–US3 test authoring can begin.

---

## Phase 3: User Story 1 — open taxonomy, pack owns *domain* ids (P1) 🎯

**Goal**: `Action.action_type` becomes `str`; kernel keeps `ActionClass` + `KERNEL_GENERIC_ACTIONS`/`LOOP_TERMINALS`; domain analyzers leave; `validate_action` resolves against `KERNEL_GENERIC_ACTIONS ∪ pack.actions`, fail-closed.

**Independent test**: `do_thing` round-trips through `validate_action` → out-of-band confirm (write_execute); `write_memory` resolves without the pack declaring it; grep of `sr_agent/` finds zero domain ids.

### Tests (write FIRST, must FAIL)

- [ ] T004 [P] [US1] SC-005 batch positive test: `Action(action_type="do_thing")` → `validate_action` resolves `write_execute` from the FIXTURE_PACK `ActionSpec` and the kernel derives "requires confirmation" from `action_class`; **and** a kernel-generic `write_memory` resolves from `KERNEL_GENERIC_ACTIONS` with the pack declaring nothing. (FR-001/002/003, SC-005 a+c)
- [ ] T005 [P] [US1] Fail-closed test: an id in neither `KERNEL_GENERIC_ACTIONS` nor `pack.actions` is **rejected** by `validate_action`, never defaulted to `read_only`. (FR-003, US1-S4, edge "unknown id")

### Implementation

- [ ] T006 [US1] `sr_agent/models/action.py`: define `KERNEL_GENERIC_ACTIONS = {write_memory, request_human_confirmation, read_file, search_code}` with kernel `ActionSpec`s (`write_memory`→`memory`; `request_human_confirmation`→`control`; `read_file`/`search_code`→`read_only`, reversible, `validate_params`=containment — D6) and `LOOP_TERMINALS = {escalate, complete}` constants; change `Action.action_type: ActionType → str` (L85); **remove** the `ActionType` enum + `ACTION_CLASS_MAP` + `REVERSIBLE`. (They are re-declared **in araratsec** — Repo B, feature 002/PR-2 — not in this repo; the old-kernel pin lets araratsec keep working until it migrates, so nothing is "moved within `sr_agent/`".) (FR-001/002, D1/D4/D6)
- [ ] T007 [US1] `sr_agent/orchestrator/action.py`: resolve each id vs `KERNEL_GENERIC_ACTIONS ∪ pack.actions`, fail-closed on miss; **drop** the legacy `pack is None` branch (its enum-indexed `ACTION_CLASS_MAP[action.action_type]` breaks under `str`); source `validate_params` from the resolved `ActionSpec`. **Partition the `_validate_params` ladder (D6, not a whole move)**: `read_file`/`search_code` branches (L93–99) + `_check_filepath`/`_contained` **stay** as the kernel-generic reads' validators; the domain-analyzer branches (L101–121) are removed here (re-hosted in feature 002). (FR-003/005, D6)
- [ ] T008 [US1] Relocate the entry fail-closed out of the loop's enum coercion: `orchestrator/loop.py` L202/357 build `Action(action_type=<str>)` (no `ActionType(...)`); rewrite the chat guard L349 to consult `KERNEL_GENERIC_ACTIONS ∪ pack.actions` instead of `_value2member_map_`; `LOOP_TERMINALS` interception (L180/189/341) references the kernel constants, not the removed enum. No loop path forwards an unvalidated `next_action`. (FR-004, edge "fail-closed relocated")
- [ ] T009 [US1] **Rehost the T002 inventory**: migrate every existing kernel test that built `Action(action_type=ActionType.…)` or imported the domain enum/`TOOL_REGISTRY`/audit statuses onto FIXTURE_PACK + string ids, so the kernel suite is green after T006 removes the enum. This is the consumer of T002 and a hard FR-016/SC-007 requirement — without it PR-1 is red at its own merge boundary. (FR-016, SC-007)

**Checkpoint**: T004/T005 pass; the rehosted suite (T009) is green; grep of `sr_agent/` for the nine domain ids (SC-001's list — the 8 analyzers that move **plus** the dropped `run_auditor_skill`) returns zero (SC-001).

---

## Phase 4: User Story 2 — pack-supplied privileged statuses, bound at session start (P1)

**Goal**: kernel stops hardcoding `{verified_safe, skip_analysis, audit_complete}`; the effective set is composed from `pack.privileged_statuses` **once at session construction**, injected into the memory plane, immutable for the session. The gate rule is unchanged.

**Independent test**: fixture pack declares `blessed`; `llm_inference` write of `status="blessed"` rejected, `human_input` accepted; the bound set cannot be mutated post-construction; `models/memory.py` names no audit status.

### Tests (write FIRST, must FAIL)

- [ ] T010 [P] [US2] **H4** in `tests/security/test_hostile_pack.py`: a pack with **empty/reduced** `privileged_statuses` yields a gate covering exactly the declared set — empty ⇒ "no privileged statuses", **not** a disabled gate — and under-declaration cannot bypass a kernel-enforced status (there is none post-US2). The empty case needs a **minimal pack declaring no statuses** (inline in the test or a second fixture — the Phase-2 FIXTURE_PACK declares `blessed`, so it exercises the *reduced/populated* branch, not empty). Sits alongside H1/H2/H3. (FR-009, US3-S3)
- [ ] T011 [P] [US2] Binding test (`tests/security/` or `tests/memory/`): with `blessed` bound at session start, `llm_inference` write of `status="blessed"` is rejected while `human_input` is accepted; a second assertion confirms the bound set is immutable after construction. (FR-006/007, US2-S1/S4)

### Implementation

- [ ] T012 [US2] `sr_agent/models/memory.py`: empty `REQUIRES_HUMAN_CONFIRMATION` (L34) of the domain statuses — becomes kernel-generic/empty; membership now comes from the bound pack set. (FR-006, SC-002)
- [ ] T013 [US2] Build the **pack → memory coupling edge (D5)**: compose the effective privileged set from `pack.privileged_statuses` once at session/memory construction and inject it into `EpisodicMemory.__init__` (`memory/episodic.py` L47); `_enforce_status_rules` (L210) consults the bound set, not the module constant; immutable for the session's lifetime (no turn/tool-result may widen or narrow it). (FR-006/007, D5)

**Checkpoint**: T010/T011 pass; `grep -rE 'verified_safe|skip_analysis|audit_complete' sr_agent/` returns zero definitions (SC-002).

---

## Phase 5: User Story 3 — guarantee preserved & pinned (P1, NON-NEGOTIABLE)

**Goal**: opening the taxonomy lowers no guardrail; B6 (boundary) + H4 (hostile) + SC-005 (positive) + SC-009 (chat) pin it. SC-005 is the **primary** proof; B6 is the anti-regression latch.

### Tests (write FIRST, must FAIL)

- [ ] T014 [P] [US3] **B6** in `tests/architecture/test_kernel_pack_boundary.py`: assert (a) `Action.action_type` is not a closed *domain* enum; (b) no domain audit id in `sr_agent/`; (c) no domain privileged-status in `sr_agent/`; (d) `KERNEL_GENERIC_ACTIONS == {write_memory, request_human_confirmation, read_file, search_code}` **and** `LOOP_TERMINALS == {escalate, complete}`, asserted **separately** — `complete` MUST NOT be in `KERNEL_GENERIC_ACTIONS`; `read_file`/`search_code` MUST be in it and MUST be excluded from check (b) per D6. (FR-008, SC-003)
- [ ] T015 [P] [US3] **SC-009** chat-path test on `run_turn`: a `next_action` absent from `KERNEL_GENERIC_ACTIONS ∪ pack.actions` is rejected and fed back as `[DATA]` (never forwarded unvalidated past `loop.py:349`); a `write_execute` domain action gates for out-of-band confirmation identically to the batch path. (FR-004, SC-009)

### Verification

- [ ] T016 [US3] Run `pytest tests/security tests/architecture`: protected ASR = 0 (≤0.05) / baseline ≥ 0.40 / differential ≥ 0.40 **unchanged vs the T001 before-baseline**; H1/H2/H3/**H4** green; SC-005 green. (FR-010, SC-004)
- [ ] T017 [US3] Prove B6 is a real latch: reintroduce one domain id/status into `sr_agent/` on a throwaway diff, confirm B6 goes **red**, then revert. (SC-003)

**Checkpoint**: PR-1's P1 core is complete and green; the taxonomy is provably task-agnostic (SC-005) and pinned (B6).

---

## Phase 6: User Story 4 — residual audit vocabulary renamed (P2)

- [ ] T018 [US4] Rename `audit_root` → `scope_root` across the kernel (`orchestrator/pack.py` `PackContext`, `orchestrator/loop.py`, `orchestrator/action.py`, `tools/readonly.py`, `tools/registry.py`) with a transitional `__getattr__` shim on the `frozen=True` `PackContext` delegating to `scope_root` + a deprecation signal (removed in PR-3). (FR-011, D3, SC-006)
- [ ] T019 [P] [US4] Rename `AuditResult` → `RunResult` in `orchestrator/loop.py` (L181, L191); leave `TurnResult` untouched; no change to the result object's shape. (FR-012, D3)
- [ ] T020 [P] [US4] Neutralize the default system prompt in `llm_core/claude_client.py` (L19) to a task-neutral kernel default; audit wording stays in the pack's `reasoning_prompt` (already overrides). (FR-013)
- [ ] T021 [US4] De-domain `search_code` in `tools/readonly.py`: drop the `.sol` default + "Solidity" wording and make `file_ext` a **REQUIRED** parameter (no default → a missed call-site fails loud with `TypeError`, never a silent scope change; SC-004 does not snapshot `search_code` results). Confirm every kernel call-site passes `file_ext`. (FR-005/D6; pairs with araratsec dispatch call-site)

**Checkpoint**: `grep -rE '\baudit_root\b|\bAuditResult\b' sr_agent/` returns zero *definitions* (at most the transitional `audit_root` shim); the kernel suite is green against `scope_root`/`RunResult` (SC-006).

---

## Phase 7: User Story 5 — PoC context leaves `PackContext` (P2)

- [ ] T022 [US5] Remove `PackContext.poc_dir` and `PackContext.poc_generator` from `sr_agent/orchestrator/pack.py` (frozen dataclass); PoC state is carried pack-side. (FR-014, D2)
- [ ] T023 [US5] Remove B3's current `poc_*` tolerance in `tests/architecture/test_kernel_pack_boundary.py`; B6 now forbids any `poc_*` field on `PackContext`. (FR-014)

---

## Phase 8: Polish & governance (Definition of Done)

- [ ] T024 [P] Reconcile docs: `docs/kernel.md` **and** `docs/kernel.ru.md` "task-agnostic residue" note → **resolved**; `docs/capability-pack-interface.md` **and** `.ru.md` → pack owns the *domain* action taxonomy + privileged-status set while the kernel retains the generic (non-domain) ids — control/memory machinery **and** reads `read_file`/`search_code` (D6). (FR-017, SC-008)
- [ ] T025 **Mirror** (do not author) the Principle II MINOR amendment (2.1.0) into `.specify/memory/constitution.md`, copying the version already merged in araratsec's source-of-truth copy (araratsec governance commit, landed before this PR-1 — see Governance ordering). Verify the two copies are byte-identical in the amended section, including the Sync Impact Report and the Principle III kernel-generic-exception **two-weights** note (machinery = structural necessity; reads = anti-duplication over a Principle I containment primitive). **Gate**: the araratsec governance commit MUST be merged first. (Governance, DoD)
- [ ] T026 [P] Write `quickstart.md` — how to run B6/H4/SC-005/SC-009 locally (venv + `SR_SECRET_KEY`, no network/Docker). (Phase-1 artifact)

---

## Phase 9: PR-3 (later, this repo) — shim removal

**Gate**: araratsec **PR-2** merged (it migrated `audit_root`→`scope_root` and bumped the pinned kernel dep). Until then the shim MUST stay.

- [ ] T027 Remove the transitional `audit_root` `__getattr__` shim and any now-unused compat; tighten B6 to its final form; re-run `pytest tests/security tests/architecture` green. (FR-011, SC-006/SC-007, coordination step 4)

---

## Dependencies & Execution Order

- **Governance commit (araratsec)** lands **before** T001 — it is the source T025 mirrors.
- **Phase 1 → Phase 2**: baseline captured (T001) before any edit; T002 inventory feeds T009; fixture (T003) before any US test.
- **US1 (Phase 3)**: T004/T005 (fail-first) → T006 → T007 → T008 → **T009 (rehost, consumes T002)**. T006 defines the constants everything resolves against.
- **US2 (Phase 4)**: T010/T011 (fail-first) → T012 → T013. US1 and US2 implementation can proceed in parallel once T003 exists, but **both** land in PR-1.
- **US3 (Phase 5)**: B6/SC-009 (T014/T015) are authored fail-first **before** US1/US2 implementation; the **verification** tasks T016/T017 run **after** US1+US2 (and the T009 rehost) are done — they assert the whole.
- **US4 (Phase 6) / US5 (Phase 7)**: after the P1 core is green; T018 (rename) should land before T021 confirms call-sites. T019/T020 are `[P]`.
- **Phase 8**: docs + constitution mirror + quickstart — after the code stabilizes; **T025 is gated on the araratsec governance commit**.
- **Phase 9 (PR-3)**: strictly after araratsec PR-2.

### Test-first ordering (constitution gate)

B6, H4, SC-005, SC-009 (T004, T010, T014, T015) are written to **fail** before their implementation lands. Do not implement T006–T008 / T012–T013 before the corresponding red tests exist.

### Parallel opportunities

- T004, T005 `[P]`; T010, T011 `[P]`; T014, T015 `[P]`.
- T019, T020, T024, T026 `[P]` (independent files).

---

## Notes

- `[P]` = different files, no ordering dependency.
- Commit only on request (Co-Authored-By trailer). Suggested grouping: one PR-1 commit series (Phases 1–8), one PR-3 commit (Phase 9); the constitution amendment itself is a **separate araratsec governance commit before PR-1**, not part of this series.
- FR-016 / SC-007: the kernel suite MUST be green at every merge boundary — never merge PR-1 with a red B6, a red MI suite, or an un-rehosted existing test (T009).
- Phase 0/1 artifacts (`research.md`, `data-model.md`, `contracts/`) were **not** separately authored; their content is folded into T002 (test inventory), T006–T008/T013 (the resolution + binding contracts), and T021 (the de-domain mechanical item). `quickstart.md` is T026. Author the standalone artifacts only if a later review requires them.
