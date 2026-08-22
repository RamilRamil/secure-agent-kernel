# Quickstart — finding provenance

**Feature**: kernel/005-finding-provenance · **Contract**:
[contracts/finding-provenance.md](./contracts/finding-provenance.md)

Every snippet below is meant to be runnable against the implemented feature. Written during
Phase 1 as the acceptance walkthrough; each of the seven spec scenarios appears once.

## 1. A finding after a tool that ran

```python
result = loop.run_turn(user_message="check the withdraw path", system_prompt=PROMPT)
record = [r for r in memory.load_for_principal(principal) if r.finding][-1]

assert record.source_type is SourceType.external_llm_output   # never promoted
assert record.action_resolution == "resolved"
assert record.action_dispatch_status == "ran"
assert record.action_operation_id is not None
```

`resolved` + `ran` is the *only* combination a consumer may use as an input to its own
grounding join. It is not a verdict — see the contract.

## 2. A finding whose action failed

Same shape, `action_dispatch_status` is `error` / `did_not_run` / `timeout` /
`unavailable`. The record exists — a hypothesis is still recorded — and is not
proof-eligible.

## 3-5. A finding with no action at all

Rejected params, an unknown `next_action`, or a turn that ended on `complete` without
touching a tool:

```python
assert record.action_resolution == "unresolved"
assert record.action_operation_id is None
assert record.action_dispatch_status is None
```

`unresolved` is a positive marker. It is **not** the same as a record with the fields
absent, which means the record predates this feature.

## 6. A finding reported in a turn that pauses

```python
paused = loop.run_turn(user_message="write a PoC for F-1", system_prompt=PROMPT)
assert paused.status.startswith("paused")     # _confirmation or _relay

a = [r for r in memory.load_for_principal(principal) if r.finding][-1]
assert a.action_resolution == "pending"
assert a.action_dispatch_status == "pending"
address = a.record_id            # keep this: it stays valid

# ... a human approves out of band ...
loop.resume_turn(system_prompt=PROMPT)

records = [r for r in memory.load_for_principal(principal) if r.finding]
b = records[-1]
assert b.action_resolution == "resolved"
assert b.resolves_record_id == address
assert any(r.record_id == address for r in records)   # A is still there
```

`A` survives resolution — `supersedes` is not used — so `address` still resolves after the
resume. That is the whole reason the pair exists rather than a correction.

## 7. A finding the pack declines to build

```python
before = len(memory.load_for_principal(principal))
loop.run_turn(user_message="...", system_prompt=PROMPT)   # persist_finding returns None
assert len(memory.load_for_principal(principal)) == before
```

No record, no partial, no near-finding.

## Reading it from the pack's side

The pack has no memory handle; `MemorySnapshot` is the only seam.

```python
snapshot = memory.snapshot(project_id=PROJECT, session_id=session.session_id)
findings = [i for i in snapshot.items if i.kind == "finding"]

eligible = [
    i for i in findings
    if i.action_resolution == "resolved" and i.action_dispatch_status == "ran"
]
```

Findings are still `kind == "finding"` — `payload_kind` on them stays empty, so they remain
inside `SNAPSHOT_KINDS`. Note `i.operation_id` is `None` on a finding: the id you want is
`i.action_operation_id`.

A resolved pause pair shows up as **two** items sharing one `finding_id`, and only the
resolution record is eligible. Collapsing the pair into one roadmap row is the consumer's
join, not a kernel behaviour.

*Walked against the implementation on 2026-08-22; the outputs above are what it prints.*

## Verifying a store written before this feature

```python
old = EpisodicMemory(pre_005_root, SECRET)
assert old.load_for_principal(principal) != []      # still verifies, still readable
assert all(r.action_resolution is None for r in old.load_for_principal(principal))
```

The unset fields are excluded from the signed dict, so nothing that was written before
kernel/005 changed shape. Absent means unknown.
