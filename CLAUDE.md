<!-- SPECKIT START -->
This working directory is **secure-agent-kernel** (Repo A): package `sr_agent`.
The audit CapabilityPack lives in the sibling **araratsec-agent** (Repo B,
package `audit_agent`) and depends on this kernel as a pin — see `README.md`.

Constitution: `.specify/memory/constitution.md`.

Active Speckit feature: `specs/003-dispatch-result-resume/plan.md` —
deterministic dispatch identity, durable `DispatchResult`, `MemorySnapshot`,
writer lease, content scope, pause/resume, trusted prompt registry.
Status: implemented (all tasks done).

Snapshot capacity is **10000 items / 32 MiB**. Over: fail closed. The
operator-facing remedy is to complete the session and start a new one
(FR-009b), not to truncate.

Pack feature `004-audit-loop-methodology` (Repo B) may generate its
`plan.md` / `tasks.md` only after this feature is **implemented and merged**.
<!-- SPECKIT END -->
