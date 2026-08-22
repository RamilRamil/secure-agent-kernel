# Contract: the `memory_write` live-trace event

**Feature**: kernel `002-memory-write-path` (FR-007, FR-008) · **Decision**: D9

**Audience**: consumers of the kernel's live-trace channel — chiefly the
araratsec-agent CLI (Repo B), which renders the trace to an operator.

This is the contract the kernel commits to. Rendering is out of scope for the
kernel feature; a consumer implements against this document.

---

## Channel

The existing live-trace sink. Two producers now write to it:

| Producer | Bound where | Events |
|---|---|---|
| `OrchestratorLoop` | `OrchestratorLoop.__init__(event_sink=...)` | `routing`, `reasoning`, `tool` |
| `EpisodicMemory` | `EpisodicMemory.__init__(event_sink=...)` | `memory_write` |

Both take the same shape: `Callable[[dict], None]`, default `None`. A composition
root that wants a unified trace passes the **same** callable to both. It is not
required to; a consumer may take memory events only.

Every event is a `dict` with a `"type"` key. This is unchanged.

---

## Event shape

```json
{
  "type": "memory_write",
  "record_id": "3f2a...-...",
  "project_id": "proj1",
  "target": "Vault.sol",
  "session_id": "sess-1",
  "source_type": "llm_inference",
  "payload_kind": "model_note",
  "log_sequence": 42
}
```

| Key | Type | Notes |
|---|---|---|
| `type` | `"memory_write"` | Constant. Distinguishes it from `tool` / `reasoning` / `routing` (FR-007, SC-003). |
| `record_id` | `str` | Addresses the record for a consumer that wants the body. |
| `project_id` | `str` | |
| `target` | `str` | The record's target, not the file stem. |
| `session_id` | `str` | |
| `source_type` | `str` | A `SourceType` value: `human_input`, `tool_output`, `external_llm_output`, `human_relayed_tool`, `llm_inference`. |
| `payload_kind` | `str \| null` | `null` for findings, checkpoints, and status changes, which predate the field. |
| `log_sequence` | `int` | 1-based, project-wide, gap-free. Usable for ordering and gap detection across targets. |

### Never present

`hmac`, `seq`, `chain_prev`, and the record body (`payload` / `finding` /
`checkpoint`). A consumer MUST NOT expect them and MUST NOT be written to require
them. Signature material is excluded because `chain_prev` is another record's
HMAC; the body is excluded because bodies carry attacker-influenced text and this
channel is rendered to a human who is deciding what to approve.

A consumer that needs a body reads the record by `record_id` through the normal
verified read path, where the `[DATA START]..[DATA END]` wrapping rules apply.

---

## When it fires

**After the append is durable** — after `fsync`, after the signed chain head is
updated. The event means *this record is on disk*, never *this record is being
written*. There is no corresponding failure event: a refused or failed write emits
nothing.

**On every successful `EpisodicMemory.write`.** There is no exempt writer. As of
this feature the observable set is:

| Origin | `payload_kind` | `source_type` |
|---|---|---|
| model-proposed `write_memory` | `model_note` | `llm_inference` |
| kernel finding persist | `null` | `external_llm_output` |
| chat turn / session snapshot / PoC status | `chat_turn`, `poc_status`, … | varies |
| dispatch bundle commit (`003`) | `dispatch_commit` | `tool_output` |
| external response ingest (`003`) | `external_response` | `external_llm_output` |
| pause checkpoint (`003`) | `pause_checkpoint` | `tool_output` |
| scope rebind | `null` / kind per writer | `human_input` |

> **Volume note for Repo B.** Kernel `003` shipped after this spec was written, so
> a consumer built against the August reading of FR-007 will now also see
> `dispatch_commit`, `external_response`, and `pause_checkpoint` events. This is
> intended (D9.1): a dispatch commit is the most consequential append the kernel
> makes, and exempting it would invert the requirement. Filter on `payload_kind`
> if a surface wants a narrower view — do not assume the old set.

---

## Guarantees, and what is deliberately not guaranteed

**Guaranteed**

- Fires exactly once per successful `write`, after durability.
- Never fires for a refused or failed write.
- Carries no signature material and no record body.
- Never affects the write: the record returned by `write()` is byte-identical
  whether the sink is absent, present, or raising.

**Not guaranteed**

- *Delivery.* No sink → no event. This is not an error (FR-008).
- *Sink survival.* An exception from the sink is caught and logged at DEBUG. The
  write still succeeds and is not rolled back. Observability is not a transaction.
- *Ordering across producers.* `log_sequence` orders memory events against each
  other. It says nothing about interleaving with `tool` or `reasoning` events.
- *Completeness as an audit log.* This is a trace, not the record of truth. The
  signed store is. A consumer MUST NOT treat a missing event as evidence that no
  write happened.

---

## Requirements on the consumer

1. **The sink MUST NOT write episodic memory.** A sink that does is a broken
   observer. The kernel guards against the recursion (a nested write from the
   same instance while the sink is running does not emit), but the guard exists to
   contain the bug, not to license it.
2. **The sink SHOULD be cheap and non-blocking.** It runs inline on the write
   path, after the fsync but before `write()` returns.
3. **The sink MUST tolerate unknown `payload_kind` values.** The namespace grows
   with features; an unrecognised kind is normal and must render as generic rather
   than crash or be dropped.
4. **A consumer MUST NOT infer authority from an event.** `source_type` describes
   provenance, not permission. `human_input` in an event means the record claims
   that tier and the kernel accepted it under its own rules — it is not a signal
   that anything was approved.
