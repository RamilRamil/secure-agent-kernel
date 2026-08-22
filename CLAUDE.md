<!-- SPECKIT START -->
This working directory is **secure-agent-kernel** (Repo A): package `sr_agent`.
The audit CapabilityPack lives in the sibling **araratsec-agent** (Repo B,
package `audit_agent`) and depends on this kernel as a pin — see `README.md`.

Constitution: `.specify/memory/constitution.md`.

Active Speckit feature: `specs/002-memory-write-path/plan.md` — kernel-owned
`write_memory` (intercepted in `KernelActionExecutor`, NOT the loop) and a
first-class `memory_write` event on every durable `EpisodicMemory.write`.
Status: implemented, T037 included.
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
