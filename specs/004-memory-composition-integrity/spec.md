# Feature Specification: Composition integrity for the episodic store

**Repository**: secure-agent-kernel (Repo A, package `sr_agent`)

**Feature Branch**: `feat/004-memory-composition-integrity`

**Created**: 2026-08-22

**Status**: Implemented

**Depends on**: nothing; touches `sr_agent/memory/episodic.py`, `sr_agent/memory/hmac.py`,
`sr_agent/models/memory.py`

## Why this exists

`EpisodicMemory` signs every record with an HMAC the model never sees. That signature has
exactly one adversary: **someone who obtained write access to the memory file but not the
orchestrator key** — a poisoned backup, a compromised volume, another process on the same
host, tool output that landed in the store. It is not a defence against the model (which has
no write handle) or against a capability pack (which has neither the key nor a memory
handle).

Against that adversary the per-record signature proved the wrong property. It authenticated
each record's **content** and said nothing about the **set**. A record could be removed, and
the removal was indistinguishable from a record that had never been written. Because a
correction is itself just a record, "delete the correction" meant "restore the thing it
corrected". No key, no forged signature, nothing in the log above DEBUG.

An append-only log whose entries do not commit to one another is not an append-only log; it
is a set of signed lines that happen to share a file. Certificate Transparency, which this
design cites as its model, is structured the way it is for this reason.

## Scenarios

| # | What the attacker does | Behaviour before | Behaviour now |
|---|---|---|---|
| 1 | Corrupts the correcting record's signature | superseded record returns to context | project withheld, operator warned |
| 2 | Deletes the correcting line | superseded record returns to context | project withheld, operator warned |
| 3 | (not an attacker) correction filed under another `target` | silently ineffective | applies within the principal |
| 4 | (not an attacker) same claim restated, no `supersedes` | both versions in context | unchanged — known limit, pinned by test |

## Requirements

- **FR-001**: A record removed from a target file MUST be detectable at load time. Each
  record carries a kernel-set `seq` and `chain_prev` inside `fields_for_hmac()`.
- **FR-002**: Truncation of a file's tail MUST be detectable. A per-project signed
  `_chain_head.json` attests, per target, the chain length and the signature at that
  position. The head attests a **prefix**: growth beyond it is genuine by construction, so
  a crash between the record append and the head update is recoverable, while a file
  shorter than the head is a break.
- **FR-003**: On a composition break the load path MUST fail closed for the **whole
  project**, not just the damaged file. Corrections may live under a different target than
  the record they cancel, so serving the remaining targets would let an attacker resurrect a
  record by damaging the file that holds its correction.
- **FR-004**: An ordinary signature failure MUST remain silent — no WARNING, no exception,
  no change in what the agent does. The break signal MUST be reachable only through claims
  made by validly-signed records, so appended forgeries cannot trigger it. `compare_digest`
  stays on every signature comparison, chain links included.
- **FR-005**: The break signal MUST go to the operator channel only, never into model
  context. `verify_integrity()` reports `chain_breaks` alongside the existing counts.
- **FR-006**: `supersedes` MUST be honoured only from records the loader has already
  verified. Reading it off an unauthenticated line would convert file-write access into an
  unauthenticated delete-by-id — strictly worse than the resurrection it would fix.
- **FR-007**: Supersede resolution MUST run over the principal's whole project and MUST NOT
  cross the project boundary.
- **FR-008**: Signature material MUST NOT reach the model. `for_llm_context()` strips
  `chain_prev` (the previous record's signature) and `seq` along with `hmac`.
- **FR-009**: Scenario 4 MUST NOT be closed by putting a model in the mutation path. It
  stays a documented limit with a regression test that fails if the behaviour changes.

## Out of scope

- Detecting a rollback of the store to a previously-valid state (file **and** head restored
  together from an old backup). That needs a monotonic anchor outside the memory directory.
- Semantic deduplication of restated facts (FR-009).
- Extending out-of-band confirmation to the memory class.

## Decisions taken with the operator (2026-08-22)

- **Records written before this change are not migrated.** They carry no chain and, because
  the signed shape changed, none of them verifies — so they read as an empty store, which is
  the behaviour `models/memory.py` already documented for any change to the signed shape.
  What is new is that this must not also brick the store: a target file whose records all
  fail verification and which has no head entry starts a fresh chain on the next write.
  Pinned by `test_MI014_records_that_predate_the_chain_do_not_block_new_writes`.
  A re-signing migration was considered and rejected for now — it would stamp a valid
  signature onto records whose provenance can no longer be checked, so a store poisoned
  before the migration would come out of it authenticated.
- **The extra read cost of project-scoped resolution is accepted.** `load()` now walks the
  project directory on every call, and `chat_session` calls it three times per history
  reconstruction (`load_turns` → `load()` + `load_session()` → `load()`, plus `render_roadmap`
  → `load()`). Correctness first; revisit if it becomes measurable. Measured: under a held
  writer lease only the FIRST of those reads walks the directory — the 2nd and 3rd are served
  from the verified-log cache (`_ProjectView`), so the per-reconstruction cost is one scan,
  not three. Pinned by `test_history_reconstruction_reuses_project_cache_across_reads` in
  `tests/unit/test_history_reconstruction_cache.py`, which fails if the cache stops absorbing
  the repeat reads. Without a lease (a reader-role memory) the cache is disabled by design and
  all three reads hit disk.
