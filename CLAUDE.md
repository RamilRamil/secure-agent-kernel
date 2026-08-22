<!-- SPECKIT START -->
This working directory is **secure-agent-kernel** (Repo A): package `sr_agent`.
The audit CapabilityPack lives in the sibling **araratsec-agent** (Repo B,
package `audit_agent`) and depends on this kernel as a pin — see `README.md`.

Constitution: `.specify/memory/constitution.md`.

Active Speckit feature: `specs/005-finding-provenance/plan.md` — a model-reported
finding is persisted only after the action proposed in the SAME `AgentAction`
resolves, stamped with `action_resolution` / `action_operation_id` /
`action_dispatch_status`, plus `resolves_record_id` on the record written when a
paused turn resumes. Kernel half of pack/003 FR-008a. Status: implemented.
Decisions D005-1..D005-8 in `specs/005-finding-provenance/research.md`.

Three things in `005` that a later change will be tempted to "fix", and must not:

* **There is no `grounded` field and there must not be one.** The kernel observes
  co-occurrence within one model turn, not derivation — the model may attach a
  finding to an unrelated action. A flag naming the stronger claim manufactures an
  evidence tier nothing verified, which is worse than the gap it closes. Pinned by
  `test_no_field_claims_a_finding_is_grounded`.
* **The resume record uses `resolves_record_id`, not `supersedes`.** `supersedes`
  requires `human_input`; a finding is `external_llm_output`. Do not relax
  `_enforce_status_rules` to let the kernel supersede — that makes the kernel a
  second authority able to delete records by id, and it would also destroy the
  paused record's id, which is the finding's stable address for the pack.
* **Findings keep an empty `payload_kind`.** `_snapshot_kind` reads `payload_kind`
  first; giving findings a kind drops them out of `SNAPSHOT_KINDS` and out of the
  pack's projection silently.

The provenance fields are outside `fields_for_hmac()` **when unset** — that is what
keeps pre-005 stores verifying — and inside it once set. `for_llm_context()` strips
all four for the same reason `log_sequence` is stripped.

Previous feature: `specs/002-memory-write-path/plan.md` — kernel-owned
`write_memory` (intercepted in `KernelActionExecutor`, NOT the loop) and a
first-class `memory_write` event on every durable `EpisodicMemory.write`.
Status: implemented and merged (`a33ee04`), T037 included.
Decisions D8–D13 live in `specs/002-memory-write-path/research.md`.

`validate_action` lets `pack.actions` shadow a kernel-generic id, so the
`write_memory` path re-checks params with `validate_write_memory` inside the
executor. Do not remove that second call as a duplicate: it is what stops a
pack from switching the param policy off (Principle III).

The dispatch-commit target is bounded by `validate_commit_target` **before**
`pack.dispatch`, in both `execute` and `resume` (T037). Do not "simplify" it
down to the `commit_if_absent` call site where the value is used: a refusal
there lands after the pack acted, leaving the effect done and uncommitted, and
an uncommitted effect re-dispatches on retry.

Previous feature: `specs/003-dispatch-result-resume/plan.md` — deterministic
dispatch identity, durable `DispatchResult`, `MemorySnapshot`, writer lease,
content scope, pause/resume, trusted prompt registry. Status: implemented and
merged (`3675bac`). `002` was specified before it and lands after it, so
`002` FR-001 and FR-007 name places `003` moved — see D8 and D9.

Snapshot capacity is **10000 items / 32 MiB**. Over: fail closed. The
operator-facing remedy is to complete the session and start a new one
(FR-009b), not to truncate.

`payload_kind="model_note"` is deliberately NOT in `SNAPSHOT_KINDS` (D10): a
model note is `llm_inference` and must never become a premise for the pack's
projection. Do not "complete" the allowlist.

Pack feature `004-audit-loop-methodology` (Repo B) may generate its
`plan.md` / `tasks.md` now: its blocker was kernel `003`, which is merged.
It does not wait on `002`.
<!-- SPECKIT END -->
