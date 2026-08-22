# Contract: finding provenance

**Feature**: kernel/005-finding-provenance (FR-003, FR-014, FR-016) · **Decisions**: D005-2,
D005-4, D005-5

**Audience**: `audit_agent` (Repo B), specifically pack/005-proof-loop-closure. This is the
kernel half of pack/003 FR-008a.

This document is what the kernel commits to. It ships with the commit pin, not before.

---

## What the kernel claims, and what it does not

The kernel reports the outcome of the action proposed in the **same `AgentAction`** that
carried the finding. That is co-occurrence within one model turn.

It is **not** a claim that the finding was derived from that tool's output. The model may
attach a finding to an unrelated action. There is no `grounded` field, under that or any
other name, and there will not be one: a flag naming the stronger claim would create an
evidence tier nothing verified.

`DispatchStatus.ran` means the dispatch completed. It does not mean an analyzer produced
grounded output. The kernel's own `DispatchStatus` docstring names collapsing those two as
an error, and this contract does not collapse them.

## The fields

On `MemoryRecord` and, identically, on `SnapshotItem`:

| Field | Type |
|---|---|
| `action_resolution` | `"resolved"` \| `"unresolved"` \| `"pending"` \| absent |
| `action_operation_id` | `str` \| absent |
| `action_dispatch_status` | a `DispatchStatus` value \| absent |
| `resolves_record_id` | `str` \| absent |

| Case | `action_resolution` | `action_operation_id` | `action_dispatch_status` | `resolves_record_id` |
|---|---|---|---|---|
| dispatch resolved | `resolved` | present | terminal | absent |
| rejected · unknown id · terminal without a tool | `unresolved` | absent | absent | absent |
| paused | `pending` | present | `pending` | absent |
| resume resolution record | `resolved` | present | terminal | present |
| written before kernel/005 | absent | absent | absent | absent |

No other combination is written. An illegal combination is refused at write time.

## What the consumer must ignore

Not proof-eligible, in every case: `unresolved`, `pending`, any `action_dispatch_status`
other than `ran`, and a record with all four fields absent.

**Absent is `unknown`, never `unresolved`.** A record written before kernel/005 says nothing
about its turn. Treating absence as "no action resolved" invents a claim the kernel never
made.

## What the consumer may build on

`action_resolution == "resolved"` together with `action_dispatch_status == "ran"` is an
**input** to the consumer's own join — it is the operation id to look up. It is not a
verdict. Grounding remains the consumer's policy over its own tool-result records.

## The pause pair

A finding reported in a turn that pauses produces two records:

- **A**, written at the pause: `pending`, carrying the operation id it paused with.
- **B**, written on resume: `resolved`, the same operation id, a terminal status, and
  `resolves_record_id = A.record_id`.

Both survive. `supersedes` is not used — it requires `human_input`, and a finding is
`external_llm_output`.

**A's `record_id` remains the finding's address.** A consumer that captured it before an
out-of-band confirmation still addresses the same finding afterwards. B is the outcome
record, not a replacement.

The link runs B → A only. A has no forward pointer: it is written first, and the store is
append-only.

A projector that has not yet been updated will show a paused finding as two rows sharing one
`finding_id`. Collapsing them is the consumer's join, not a kernel behaviour.

## Two operation ids

| Field | Question | On a finding record |
|---|---|---|
| `operation_id` | which operation is this record the commit of? | `None` |
| `action_operation_id` | which operation did the turn that produced this finding dispatch? | may be set |

Do not read `operation_id` expecting the second meaning.

## Unchanged, and stated so a reader does not have to check

- `source_type` on a finding stays `external_llm_output`. Nothing here promotes it.
- `payload_kind` on findings stays empty, so findings remain in `SNAPSHOT_KINDS` and in the
  snapshot. The kernel/002 `memory_write` event contract is a **no-op** for this feature.
- The provenance fields are never written into `finding.model_dump()`.
- `PackContext` still has no memory handle. `MemorySnapshot` is still the only read seam.
- The kernel still reads the domain `Finding` no deeper than `.location`.
- A finding the pack declines to build still produces no record — no partial, no strip.
- Signature material (`hmac`, `seq`, `chain_prev`) and record bodies stay out of the live
  trace, per the kernel/002 contract.

## Integrity, stated because an earlier message got it wrong

An earlier note to the consumer described a harmless downgrade: strip the fields from a
stored line, the record still verifies, and it now reads as `unknown`. **That is not what
happens.** The fields are excluded from the signed dict only when *unset*, which is what
lets stores written before this feature keep verifying. Once set they are signed like every
other field, so altering or removing one breaks the signature and the record is dropped —
and the signed chain head still attests it was there, so the removal reaches the operator.

For a consumer nothing changes: absence still means unknown and is still not
proof-eligible. The correction matters because the weaker claim was the one written down.

## Capacity

A paused finding occupies two of the snapshot's 10000 items / 32 MiB. The envelope is fail
closed and never truncated; a session that pauses on many findings reaches it sooner. The
remedy is unchanged: complete the session and start a new one.
