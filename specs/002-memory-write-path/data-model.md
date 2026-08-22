# Phase 1 Data Model: `002-memory-write-path`

**Feature**: `002-memory-write-path` · **Date**: 2026-08-22 · **Plan**: [plan.md](./plan.md)

No new persisted schema. `MemoryRecord` is unchanged — this feature adds a
`payload_kind` value and a transient event, neither of which touches
`fields_for_hmac()`. Records written before this feature verify after it.

---

## Entity 1 — model note record

A `MemoryRecord` (`sr_agent/models/memory.py`), not a new type. Every field below
is kernel-set; none is readable from `action.params` (D12, structural layer).

| Field | Value | Set by | Why not from params |
|---|---|---|---|
| `project_id` | `session.principal.project_id` | kernel | Params-supplied would cross project isolation. |
| `session_id` | `session.session_id` | kernel | Provenance. |
| `source_type` | `SourceType.llm_inference` | kernel (FR-002) | The whole trust hierarchy is computed from this field. |
| `tool` | `None` | kernel | `tool` is for `tool_output`; a note is not one. |
| `target` | `params["target"]`, else `action.action_type` | validated params (D11) | Honoured: an agent moves across targets, and the same expression already builds a `dispatch_commit`'s target (`executor.py:170`). Bounded, not free-form. |
| `payload_kind` | `"model_note"` | kernel (FR-003) | Discriminator; see the namespace below. |
| `payload` | `{"note": <validated str>}` | kernel, from validated params | The one field whose *content* originates with the model. |
| `finding` / `checkpoint` / `status_change` | `None` | — | FR-003: finding-shaped writes stay on the finding path. |
| `supersedes` | `None` | — | Refused at validation; also blocked by `_enforce_status_rules` for any non-`human_input` source. |
| `seq` / `chain_prev` / `log_sequence` / `hmac` | assigned in `EpisodicMemory.write` | kernel | Signature and composition material. |

**Validation rules** (FR-004, enforced in `_validate_write_memory`, `sr_agent/orchestrator/action.py`):

1. `params["note"]` MUST be a non-empty string after strip. Empty or missing →
   reject. Not a silent no-op that reads as success (spec, Edge Cases).
2. The encoded note MUST NOT exceed `MAX_PAYLOAD_BODY_BYTES` (8192,
   `sr_agent/models/dispatch.py`). Reusing `003`'s constant rather than minting a
   second limit.
3. `params["target"]`, when present, MUST be non-empty after strip, free of NUL
   and control characters, and at most 200 bytes UTF-8. Absent → the action id.
   The bound exists because a target becomes a filename: without it an empty or
   oversized value surfaces as an `OSError` instead of a refusal.
4. Any of these keys present in `params` → reject the action, do not strip:
   `source_type`, `hmac`, `supersedes`, `status_change`, `status`, `seq`,
   `chain_prev`, `log_sequence`, `record_id`, `project_id`, `session_id`, `tool`.
   `target` is deliberately NOT in this set (D11); it is the one params key this
   path honours.
5. Rejection surfaces as `DispatchResult(status=error, ...)` and re-enters the
   model's context DATA-wrapped. It never raises (D12.3).

**On the target not being a containment boundary**: `project_id` comes from
`session.principal`, never from params, so a target cannot reach another project.
`EpisodicMemory._target_stem` replaces `/` and `:`, so `"../../etc/x"` becomes the
ordinary filename `.._.._etc_x.jsonl` inside the project directory. The bound
in rule 3 is about error quality, not about escape.

**Lifecycle**: written once, never mutated. Correctable only by the append-only
`supersedes` chain, which requires `human_input` — so the model cannot retract its
own note, by design.

---

## The `payload_kind` namespace

`payload_kind` is the kernel's record-kind discriminator. After this feature:

| Value | Written by | In `SNAPSHOT_KINDS`? |
|---|---|---|
| `dispatch_commit` | `EpisodicMemory.commit_if_absent` (`003`) | yes |
| `external_response` | `EpisodicMemory.put_external_response_if_absent` (`003`) | yes |
| `pause_checkpoint` | `KernelActionExecutor.write_pause_checkpoint` (`003`) | no |
| `chat_turn`, `poc_status`, session snapshots | `orchestrator/chat_session.py` | no |
| **`model_note`** | **`KernelActionExecutor`, this feature** | **no — D10** |

Standalone findings carry no `payload_kind` (they predate it) and are mapped to
the synthetic kind `"finding"` by `EpisodicMemory._snapshot_kind`.

**Invariant (D10)**: `model_note` MUST NOT be added to `SNAPSHOT_KINDS`. A model
note is `llm_inference`; admitting it to the pack's projection input would let the
model's own prose become a premise for what the agent does next. Pinned by a test,
not only by this document.

---

## Entity 2 — `memory_write` event

Transient. Not persisted, not signed, not addressable. Emitted from
`EpisodicMemory.write` after the append is durable (D9.2).

```python
{
    "type": "memory_write",
    "record_id":    str,          # addresses the record for a consumer that wants the body
    "project_id":   str,
    "target":       str,
    "session_id":   str,
    "source_type":  str,          # SourceType value
    "payload_kind": str | None,   # None for findings / checkpoints / status changes
    "log_sequence": int,          # project-wide append order
}
```

**Excluded on purpose**:

- `hmac`, `seq`, `chain_prev` — signature material. Same rule as
  `MemoryRecord.for_llm_context`; `chain_prev` *is* another record's signature.
- the record body (`payload`, `finding`, `checkpoint`) — bodies routinely carry
  attacker-influenced text, and the trace channel is rendered to an operator who
  is deciding what to approve. A consumer that needs the body reads it by
  `record_id` through the verified path, where DATA wrapping still applies.

**Emission scope** (D9.1): *every* successful `EpisodicMemory.write`. Model notes,
findings, chat turns, session snapshots, PoC status, scope rebinds, dispatch
commits, external responses, and pause checkpoints. There is no exempt writer.

**Delivery guarantees**: none, deliberately. Absent sink → no event, write
succeeds. Raising sink → exception swallowed and logged at DEBUG, write succeeds,
record returned unchanged (FR-008). Observability is not a transaction.

---

## State transitions

None. Neither entity has states. The model note is append-only; the event is
fire-and-forget.
