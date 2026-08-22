# Feature Specification: Kernel-owned `write_memory` path + memory-write observability

**Repository**: secure-agent-kernel (Repo A, package `sr_agent`)

**Feature Branch**: `002-memory-write-path`

**Created**: 2026-08-10

**Status**: Draft

**Depends on**: `001-task-agnostic-contract` (keeps `write_memory` in `KERNEL_GENERIC_ACTIONS`; this feature resolves how that id is *executed* and how durable writes are *observed*)

**Note on numbering**: Kernel-local `002-*`. Distinct from araratsec's paired `002-pack-owns-action-taxonomy` (gitignored there). Do not conflate the two.

**Input**: User direction after architectural review: adopt path **(a) + A** — model-proposed `write_memory` is intercepted and executed by the kernel (not `pack.dispatch`), with kernel-set provenance and existing status rules; every durable episodic write emits a first-class observability event (A-hard). Content shape is payload/`model_note` only (not finding-shaped). Out-of-band confirmation is NOT extended to the memory class. `PackContext` remains without a memory handle.

## Why this exists

Feature 001 closed the Principle III taxonomy gap and explicitly retained `write_memory` as kernel control/memory machinery. It did **not** define the execution path. Today that id validates as `ActionClass.memory` (no OOB gate), then falls through to `pack.dispatch`, where packs stub it. Real durable writes are already kernel-authored side-effects (findings, chat turns, session snapshots) via `EpisodicMemory.write`, which is silent: no first-class operator-facing "memory write" signal.

That leaves a contract seam: a blessed non-gated generic id whose body is pack-owned. A future pack that implements `write_memory` in `dispatch` (or gains a memory handle) would get quiet durable writes without kernel provenance ownership. Conversational stealth-resistance for ordinary content writes is also missing: operators may see findings or chat turns as products of the loop, but there is no dedicated signal that *episodic memory was appended*.

This feature closes that seam without expanding Constitution II (OOB stays on `write_execute` + privileged statuses only).

## Resolved Decision (D7)

- **D7 — `write_memory` is kernel-executed machinery (variant a), with A-hard observability; content is payload-only.**
  - **(a) Interception**: After `validate_action` approves `write_memory`, the orchestration loop handles it in-kernel *before* `pack.dispatch`. The pack is not invoked for this id. The kernel builds the `MemoryRecord`, sets `source_type` itself (model-proposed writes → `llm_inference`), and calls `EpisodicMemory.write` (status rules still apply).
  - **Content shape (a1)**: Model params may supply a note via the generic `payload` / `payload_kind="model_note"` path only. Finding-shaped writes stay on the existing finding path (`persist_finding` → kernel write). Params MUST NOT be allowed to set `source_type`, `hmac`, `supersedes`, or a privileged `status_change` (reject or strip fail-closed at validation / construction — plan picks the exact fail mode; default: reject).
  - **(A-hard) Observability**: Every successful durable `EpisodicMemory.write` surfaces a first-class `memory_write` event on the existing live-trace channel (and remains distinguishable from a generic `tool` event). This covers model `write_memory` *and* kernel-authored writes (findings, turns, snapshots, PoC status, etc.).
  - **Explicitly rejected here**: (B) OOB-gating all memory-class writes; (C) removing `write_memory` from `KERNEL_GENERIC_ACTIONS`; finding-shaped `write_memory` (a2); leaving execution in `pack.dispatch` (variant 0).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Model `write_memory` is durable and kernel-owned (Priority: P1)

A model (batch or chat) proposes `next_action=write_memory` with a note payload. The kernel validates it, does **not** call `pack.dispatch`, persists an episodic record at `llm_inference`, and returns a DATA-wrapped outcome to the model. A privileged status change attempted through this path is rejected by existing status rules / param policy.

**Why this priority**: This is the contract fix — machinery id must be executed by the kernel, or 001's exception is hollow.

**Independent Test**: With a fixture pack whose `dispatch` records calls (or raises if invoked), a proposed `write_memory` produces a durable record, never invokes `dispatch`, and sets kernel provenance to `llm_inference`.

**Acceptance Scenarios**:

1. **Given** a validated `write_memory` with an allowed note payload, **When** the loop executes it, **Then** a durable episodic record exists with `payload_kind` discriminating a model note and `source_type=llm_inference`, and `pack.dispatch` was not called.
2. **Given** `write_memory` params that attempt to set `source_type=human_input` (or supply `hmac` / `supersedes`), **When** validation or construction runs, **Then** the attempt is rejected (fail-closed) and no durable record is written at the forged tier.
3. **Given** `write_memory` that attempts a privileged `status_change`, **When** it reaches the memory gate, **Then** it is rejected exactly as other non-`human_input` privileged status writes are today.
4. **Given** the same fixture pack on both batch and chat loops, **When** `write_memory` is proposed, **Then** both paths use the kernel interception (no chat shortcut into `dispatch`).

---

### User Story 2 - Every durable memory write is first-class observable (Priority: P1)

Whenever episodic memory successfully appends a record (model note, finding persist, chat turn, session snapshot, etc.), observers on the live-trace channel receive a `memory_write` event that is not a generic `tool` event. Operators (or CLI consumers) can tell that memory was written without scraping INFO logs.

**Why this priority**: Without A-hard, path (a) makes model notes durable but still conversationally quiet relative to a dedicated signal; findings/turns already have other surfaces, but the *write itself* stays silent.

**Independent Test**: With an event sink attached, one model `write_memory` and one kernel-authored finding persist each produce a `memory_write` event; a read-only tool call does not.

**Acceptance Scenarios**:

1. **Given** an attached event sink, **When** a model `write_memory` succeeds, **Then** the sink receives a `memory_write` event (not only `type=tool`).
2. **Given** an attached event sink, **When** the kernel persists a finding (or other existing kernel-authored write), **Then** the sink also receives `memory_write`.
3. **Given** no event sink (`None`), **When** writes succeed, **Then** the loop still completes (observability remains best-effort and MUST NOT become a control-plane dependency).
4. **Given** documentation for the live-trace contract, **When** a CLI/consumer implements rendering, **Then** it can distinguish `memory_write` from ordinary tool events (consumer rendering may land in a follow-up; kernel ships the event contract).

---

### User Story 3 - Pack boundary and Constitution II unchanged (Priority: P2)

A hostile or naive pack cannot gain a memory write handle via this feature, cannot skip confirmation by labeling something as memory, and cannot weaken privileged-status rules. OOB confirmation remains only for `write_execute`.

**Why this priority**: Security non-regression; proves (a)+A does not smuggle a II expansion or a pack memory backdoor.

**Independent Test**: Existing hostile-pack / MI suites stay green; `PackContext` still exposes no memory handle; `write_execute` still gates OOB; `ActionClass.memory` still does not set `human_confirmation=False`.

**Acceptance Scenarios**:

1. **Given** `PackContext` after this feature, **When** inspected, **Then** it still has no memory handle / write API.
2. **Given** a `write_execute` domain action, **When** proposed, **Then** OOB confirmation still applies unchanged.
3. **Given** protected MI / hostile-pack suites, **When** run after the feature, **Then** ASR and hostile assertions remain within existing bars (no weakened tests).

### Edge Cases

- **Stub removal is intentional**: packs that today return `[STUB] write_memory` must no longer be the execution path; interception happens earlier. Pack `dispatch` may still see other ids.
- **Finding path stays separate**: reporting a finding via `AgentAction.finding` continues through `persist_finding` + kernel write; it MUST NOT require the model to also call `write_memory`.
- **Empty / missing note payload**: fail-closed at validation (reject), not a silent no-op that looks like success.
- **Event sink failures**: swallowed as today for other emits — must not abort the turn or roll back a successful write (observability ≠ transaction).
- **Batch vs chat parity**: both loops must intercept; chat MUST NOT remain on dispatch-only behavior.
- **Out of scope**: CLI mandatory UI chrome in araratsec (may consume the event later); extending OOB to memory; removing `write_memory` from `KERNEL_GENERIC_ACTIONS`; auto-promotion of model notes into Principle IV steering knowledge.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: After `validate_action` approves `action_type=write_memory`, the orchestration loop (batch and chat) MUST execute the write in-kernel and MUST NOT call `pack.dispatch` for that action.
- **FR-002**: For model-proposed `write_memory`, the kernel MUST set `source_type=llm_inference` itself. Values for `source_type`, `hmac`, or `supersedes` supplied in model params MUST NOT be honored.
- **FR-003**: Allowed content for model-proposed `write_memory` MUST be the generic payload note form (`payload` with `payload_kind` discriminating a model note). Finding-shaped content MUST remain on the existing finding persist path, not this action.
- **FR-004**: `write_memory` param validation MUST fail-closed on disallowed shapes (including attempts to carry privileged `status_change` or forge provenance fields). Successful writes still pass through `EpisodicMemory` status enforcement.
- **FR-005**: `ActionClass.memory` MUST NOT gain out-of-band confirmation. Constitution II gates remain `write_execute` and privileged-status writes at `human_input` only.
- **FR-006**: `PackContext` MUST continue to expose no memory write capability. Packs MUST NOT become authors of episodic records via this feature.
- **FR-007**: Every successful `EpisodicMemory.write` MUST emit a first-class live-trace event of type `memory_write` (A-hard), distinct from `tool` / `reasoning` / `routing` events, when an event sink is present.
- **FR-008**: Absence or failure of the event sink MUST NOT block or reverse a successful write (observability is not a safety gate).
- **FR-009**: Existing MI-resistance and hostile-pack guarantees MUST be preserved (protected ASR target 0 / ≤ 0.05 bar unchanged; no deletion/weakening of security tests to pass this feature).
- **FR-010**: Tool/registry description text for `write_memory` MUST be updated to match the payload-note + kernel-authored provenance contract (description hash remains consistent with registry verification).
- **FR-011**: Kernel docs that describe `write_memory` execution / memory observability MUST be reconciled so they no longer imply pack-stub execution as the steady state.

### Key Entities

- **`write_memory`**: kernel-generic resolvable action id (`ActionClass.memory`); after this feature, kernel-executed.
- **Model note record**: episodic `MemoryRecord` with payload kind discriminating a model note; provenance `llm_inference` when produced via `write_memory`.
- **`memory_write` event**: first-class live-trace event fired on successful durable append; observability only.
- **`PackContext`**: unchanged least-privilege surface — still no memory handle.
- **Status rules**: existing privileged-status enforcement inside `EpisodicMemory.write` (bound pack set from 001/D5); unchanged rule, still applied.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A test demonstrates model `write_memory` creates a durable episodic record and that pack `dispatch` was not invoked.
- **SC-002**: A test demonstrates forged provenance fields / privileged status via `write_memory` are rejected with no forged-tier durable record.
- **SC-003**: With an event sink, both a successful `write_memory` and at least one other kernel-authored write (e.g. finding persist) produce `type=memory_write` events; a pure read tool does not.
- **SC-004**: Chat and batch both satisfy SC-001 (parity).
- **SC-005**: Protected MI ASR remains within the existing project bar; hostile-pack suite stays green.
- **SC-006**: `PackContext` still has no memory write API (architecture/hostile assertion or equivalent remains true).
- **SC-007**: Reviewer can point to D7 in this spec as the explicit resolution of the 001 `write_memory` execution ambiguity (no reliance on pack-stub as the permanent design).

## Assumptions

- Feature 001's open taxonomy and `KERNEL_GENERIC_ACTIONS` membership for `write_memory` remain in force; this feature does not reopen removal (variant C).
- `EpisodicMemory.write` remains the single durable append choke point suitable for A-hard emission (directly or via a thin kernel wrapper used by all writers).
- CLI/araratsec rendering of `memory_write` may follow in a separate change; this feature's Done bar is kernel event contract + tests, not a specific TUI.
- Principle IV (human-gated knowledge promotion) is unchanged: model notes are episodic DATA at `llm_inference`, not steering-knowledge promotion.
- `request_human_confirmation` execution semantics are out of scope unless they block `write_memory` work; do not expand this feature into a full control-id audit.

## Governance Impact (constitution)

- **Principle I**: Strengthened alignment — kernel owns provenance on the model write path; results re-enter as DATA.
- **Principle II**: No expansion — explicitly does not OOB-gate memory-class content writes.
- **Principle III**: Fulfills the "structural necessity" story for `write_memory` by making execution kernel-side, matching the exception text introduced around 001.
- **Principle IV**: Unchanged; no auto-promotion path added.
- No constitution version bump required if wording already covers machinery ownership; plan phase confirms whether a PATCH clarification note is useful. Amendment only if reviewers require explicit "memory_write observability is not a II gate" language.
