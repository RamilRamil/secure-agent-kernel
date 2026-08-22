# Phase 1 — Data model: finding provenance

**Feature**: kernel/005-finding-provenance · **Plan**: [plan.md](./plan.md)

## Entity 1 — the provenance fields on `MemoryRecord`

Four optional fields on the record envelope. Kernel-set on every finding write; never from
model params, never from the pack, never inside `finding` (FR-012, FR-017).

| Field | Type | Meaning |
|---|---|---|
| `action_resolution` | `"resolved"` \| `"unresolved"` \| `"pending"` \| `None` | what happened to the action proposed in the same `AgentAction` |
| `action_operation_id` | `str \| None` | the kernel's operation id for that dispatch |
| `action_dispatch_status` | `DispatchStatus \| None` | the status that dispatch returned |
| `resolves_record_id` | `str \| None` | on a resume record: the `record_id` of the paused record it resolves |

### Legal combinations

Enforced by a `model_validator`, so a contradiction is a write-time error rather than a
stored inconsistency.

| Case | `action_resolution` | `action_operation_id` | `action_dispatch_status` | `resolves_record_id` |
|---|---|---|---|---|
| dispatch resolved | `resolved` | present | terminal (`ran` / `error` / `did_not_run` / `timeout` / `unavailable`) | absent |
| rejected · unknown id · terminal without a tool | `unresolved` | absent | absent | absent |
| paused | `pending` | present | `pending` | absent |
| resume resolution record | `resolved` | present | terminal | present |
| written before this feature | absent | absent | absent | absent |

Rejected by the validator: `resolved` without an operation id; `unresolved` carrying either
an operation id or a status; `pending` whose status is not `pending`; `resolves_record_id`
on anything but a `resolved` record; any of the three set on a record with no `finding`.

### Reading the absent case

All four absent means **unknown** — the record predates this feature. It does **not** mean
"no action resolved", and it is never proof-eligible (FR-003, D005-7). A consumer that
collapses `absent` into `unresolved` has invented a claim the kernel never made.

### Integrity

`fields_for_hmac()` omits each field when it is `None`, so a record that carries none of
them signs exactly as it does today and every pre-005 store keeps verifying (FR-015,
D005-3).

Once set, the fields are signed like every other field: altering them or removing them from
a stored line breaks the signature, the record is dropped, and the signed chain head still
attests it was there. An earlier draft claimed stripping would leave a verifying record that
merely read as `unknown`; that was wrong — the exclusion applies at signing time, not at
verification time. There is no downgrade path, and nothing an attacker can do without the key
turns a hypothesis into `resolved` + `ran`.

`for_llm_context()` strips all four alongside `hmac`, `seq`, `chain_prev` and
`log_sequence` (FR-010).

An earlier draft left them in, reasoning that they are turn metadata rather than signature
material. That reasoning does not survive the kernel's own precedent: `log_sequence` is
stripped because *"a turn that could see its own position in the log could reason, and then
argue, about it."* These fields are the stronger case, not the weaker one — they tell the
model exactly which state makes a finding proof-eligible downstream (`resolved` + `ran`),
and a model that can see which of its earlier findings earned that stamp can optimise for
producing it. Closing one evidence gap by opening a manipulation surface is not a trade this
feature makes.

The consumer loses nothing: `SnapshotItem` is a pack-facing projection, not model context,
and that is where FR-016 exposes the fields.

## Entity 2 — the pause pair

A paused turn produces two records, both surviving:

```
record A   finding=…  action_resolution=pending   action_operation_id=OP  action_dispatch_status=pending
record B   finding=…  action_resolution=resolved  action_operation_id=OP  action_dispatch_status=ran
           resolves_record_id=A.record_id
```

Record A keeps its `record_id` for the whole life of the store. That id is the finding's
address for a consumer that captured it before the pause. `supersedes` is deliberately not
used, so A is never removed from a load or a snapshot (D005-4).

The link runs B → A. A carries no forward pointer, because it is written before B exists and
the store is append-only.

## Entity 3 — `SnapshotItem` passthrough

`SnapshotItem` (pydantic, `extra="forbid"`, `frozen=True`) gains the same four fields, read
off the record envelope (FR-016).

Two `operation_id`-shaped fields now sit on the item and answer different questions:

| Field | Question | On a finding record |
|---|---|---|
| `operation_id` | which operation is this record the commit of? | `None` |
| `action_operation_id` | which operation did the turn that produced this finding dispatch? | may be set |

`operation_id` is read from the payload (`episodic.py:856`); `action_operation_id` comes off
the envelope. Unifying where the first is read from is a kernel/003 behaviour change and is
out of scope (D005-5).

`payload_kind` on findings stays empty, so `_snapshot_kind` keeps resolving them to
`"finding"` and they remain inside `SNAPSHOT_KINDS` (FR-013).
