# Feature Specification: Rollback detection for the episodic store

**Repository**: secure-agent-kernel (Repo A, package `sr_agent`)

**Feature Branch**: `006-memory-rollback-anchor`

**Created**: 2026-09-11

**Status**: Draft

**Depends on**: `004-memory-composition-integrity` (this feature closes the one
attack 004 explicitly left out of scope — see its "Out of scope" note). Touches
`sr_agent/memory/episodic.py`, `sr_agent/memory/hmac.py`.

## Why this exists

Feature 004 made a record removed or a file truncated **detectable**: every record
commits to its neighbours (`seq`/`chain_prev`) and a signed `_chain_head.json`
attests the chain length per target. But 004 named one attack it could not catch
and deferred it verbatim:

> *Detecting a rollback of the store to a previously-valid state (file **and** head
> restored together from an old backup). That needs a monotonic anchor outside the
> memory directory.*

This is that anchor.

The adversary is unchanged from 004 and the [MI threat model](../../docs/mi-threat-model.md):
**write access to `memory/<project>/` but NOT the orchestrator key** — a poisoned
backup, a compromised volume, another process on the same host. A *rollback* is the
one move that survives every 004 check: the adversary snapshots the entire project
directory at time T0 (a genuinely signed, valid state) and later restores it whole —
`*.jsonl` files, `_chain_head.json`, and `_writer_lease.json` together. Every record
still verifies, every chain still reconstructs, the head still matches: it is a real
past of the store, signed by the real key. Nothing 004 inspects is out of place.

What is lost is everything written between T0 and the restore. If a `human_input`
correction ("finding H-3 is a false positive") landed at T0.5, the rollback deletes
it and H-3 silently returns to the pack's projection as an active finding — the exact
resurrection 004's supersede-chain exists to prevent, reached by a route 004 cannot
see because it only ever compares the store against *itself*.

A store that can only attest to its own internal consistency cannot detect being
replaced by an older internally-consistent copy of itself. Catching that requires one
fact held **outside** the store, where the adversary who owns `memory/` cannot reach
it: the highest point the log has ever reached.

## Adversary & trust boundary

- **In scope**: an adversary with write access to `memory/<project>/` and no
  orchestrator key. This is the 004 adversary; the anchor lives outside `memory_root`,
  under the same access-control boundary as the secret key, so this adversary can
  neither read nor write it.
- **Residual (out of scope, documented)**: an adversary who *also* controls the
  key-store location can roll the anchor back in step with the store, defeating
  detection. That is a strictly larger compromise (they are one step from the key
  itself) and needs a different anchor — a hardware counter or an external monotonic
  service — which Constitution V (provider-agnostic, no network hard dependency) puts
  out of scope here. Same shape as 004's own out-of-scope note.

## Scenarios

| # | What happens | Behaviour before (004 only) | Behaviour now |
|---|---|---|---|
| 1 | Whole project dir restored from an older signed backup | everything verifies; lost corrections silently resurrect | anchor > log_max → **fail closed**, operator warned |
| 2 | A write is attempted onto a rolled-back log | append lands on a truncated history; effect re-runs | write refused before the append |
| 3 | Normal growth (append after append) | fine | fine — anchor tracks the log, never trips |
| 4 | Crash between the head update and the anchor update | fine | fine — anchor lags by one, next write catches it up; a lagging anchor is never a rollback |
| 5 | Adversary raises the anchor value without the key | n/a | forged value fails verification, treated as absent — cannot lock a project out permanently |
| 6 | Anchor gone/corrupt (crash, or the out-of-scope key-store adversary) | n/a | treated as *not yet anchored* → **proceed**; next write re-establishes it (D006-3). Detecting a deleted anchor needs reaching `anchor_root`, which is out of scope. |
| 7 | Fresh or pre-006 project: no anchor yet | fine | fine — first durable write establishes the anchor |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A rolled-back store is refused at the pack's read seam (Priority: P1)

The capability pack reads prior state through one seam (`snapshot`). A rollback that
restores an older valid copy of the store must not be served to the pack as if it were
current — that is the whole exploit, because the pack would then reason over a past in
which a retracted finding is still live.

**Why this priority**: This is the feature. The pack projection is the primary MI
vector; serving a rolled-back projection is indistinguishable, to the pack, from the
real thing. Everything else supports this.

**Independent Test**: Build a store under a held lease, append several records
(advancing `log_sequence`), then copy `memory/<project>/` aside, append more, and
finally restore the earlier copy over the live directory while the anchor keeps its
current value. `snapshot()` must raise a rollback error rather than return the older
projection.

**Acceptance Scenarios**:

1. **Given** a project whose log has reached sequence N and whose anchor records N,
   **When** the project directory is rolled back to an earlier signed state at
   sequence M < N, **Then** `snapshot()` fails closed (raises) and does not return the
   rolled-back items.
2. **Given** the same rollback, **When** the operator integrity scan runs, **Then** it
   reports the rollback on the operator channel, distinct from a signature failure.

---

### User Story 2 - A write is refused onto a rolled-back log (Priority: P1)

If a rollback is not caught before an append, the kernel would sign a new record onto
a truncated history — re-running an effect the log already recorded as done, and
producing a store whose newest record sits on a foundation the anchor knows is stale.

**Why this priority**: 004 already refuses to append onto a broken chain; a rollback is
a break the chain itself cannot show, so the write path must consult the anchor too.
Without this, exactly-once (feature 003) is defeated by a rollback: the committed
transition disappears and re-dispatches.

**Independent Test**: Roll a store back as in US1, then attempt `write()`. The append
is refused before any record is signed or written; the operator remedy is surfaced.

**Acceptance Scenarios**:

1. **Given** an anchor recording sequence N and a log rolled back to M < N, **When** a
   durable write is attempted, **Then** it is refused and nothing is appended.
2. **Given** a project whose head exists but whose anchor is missing or does not verify,
   **When** a durable write is attempted, **Then** it proceeds and establishes/repairs the
   anchor at the current log max (not-yet-anchored, D006-3) — a missing anchor is not a
   rollback and does not fail closed.

---

### User Story 3 - Legitimate operation is never mistaken for a rollback (Priority: P2)

Normal growth, a crash between the two-file update, a pre-006 store with no anchor yet,
and an adversary's forged anchor value must all resolve to the correct outcome — a
false rollback would fail-close a healthy project (a self-inflicted denial of service),
and a missed integrity check would reopen the hole.

**Why this priority**: The asymmetry of the rule and the compatibility path are what
make the feature safe to turn on by default. Getting them wrong is either a lockout or
a bypass.

**Independent Test**: Exercise each non-attack case — many sequential appends; a
simulated crash that updates the head but not the anchor; a store written before this
feature (no anchor file); a forged anchor whose signature does not verify — and assert
each behaves as specified (no false rollback; catch-up on next write; compat load;
forged value ignored).

**Acceptance Scenarios**:

1. **Given** a healthy project, **When** many records are appended in sequence, **Then**
   no read or write ever raises a rollback.
2. **Given** a head updated but an anchor left behind by a crash, **When** the next read
   runs, **Then** it does not raise; **and when** the next write runs, **Then** the
   anchor is caught up to the log.
3. **Given** a store written before this feature (a signed head, no anchor), **When** it
   is loaded, **Then** it loads normally; **and** its first post-006 durable write
   establishes the anchor.
4. **Given** an anchor value inflated by an adversary without the key, **When** it is
   read, **Then** it fails verification and is treated as absent — it cannot force a
   legitimate session into a permanent rollback lockout.

### Edge Cases

- **Anchor absent, head absent** (truly fresh or empty project): proceed; the first
  durable write creates the anchor. Not a rollback.
- **Anchor absent, head present**: *not yet anchored* — proceed; the next durable write
  establishes the anchor at the current log max. Not fail-closed (D006-3): the in-scope
  adversary cannot reach `anchor_root`, so a missing anchor is never their doing, and
  fail-closing here would brick every pre-006 store.
- **Anchor lags the log** (value < log_max): legitimate — normal after a crash between
  the head update and the anchor update. Never a rollback. The next write advances it.
- **Anchor exceeds the log** (value > log_max): the defining rollback condition. Fail
  closed on both the read seam and the write path.
- **Anchor equals the log**: healthy steady state.
- **Forged/inflated anchor** (fails HMAC): treated as absent, not as authoritative — a
  keyless adversary must not be able to raise the watermark and lock a project out.
- **Multi-project**: the watermark is per project; a rollback or a damaged anchor for
  one project never fails-closes another.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The kernel MUST maintain, per project, a monotonic **rollback anchor** —
  the highest project-wide append sequence ever observed — persisted **outside**
  `memory_root`, under the same access-control boundary as the orchestrator key
  material. It MUST NOT live inside `memory/<project>/`, where the in-scope adversary
  can write.
- **FR-002**: Every successful durable write MUST advance the anchor to the log's new
  high-water mark, **after** the record and the chain head are durable. The anchor is
  the last of the write's persisted artifacts, so a crash can only ever leave it
  behind the log, never ahead.
- **FR-003**: On the read path, the anchor MUST be compared against the log's current
  maximum sequence. `anchor > log_max` MUST fail closed (raise; do not serve the
  projection or records). `anchor <= log_max` MUST proceed. The rule is asymmetric by
  design: a lagging anchor is legitimate, an exceeding anchor is a rollback.
- **FR-004**: The anchor MUST be HMAC-signed with the orchestrator key over a canonical
  encoding, exactly as `_chain_head.json` and `_writer_lease.json` are. An anchor that
  does not verify MUST be treated as **absent**, never as an authoritative value — a
  keyless adversary must be unable to inflate the watermark and thereby lock a project
  out permanently.
- **FR-005**: A **missing or unverifiable anchor** MUST be treated as *not yet anchored* —
  the read/write MUST proceed, and the next durable write MUST establish (or repair) the
  anchor at the current log max. It MUST NOT fail closed. Rationale (research D006-3): the
  anchor lives outside the in-scope adversary's reach (FR-001), so a missing anchor is never
  their doing — failing closed there defends only the out-of-scope key-store adversary while
  bricking every pre-006 store (a self-inflicted denial of service). The rollback that must
  fail closed is `anchor > log_max` (FR-003), which a full-directory rollback still triggers
  because the external anchor keeps its watermark. *(This supersedes an earlier draft of
  FR-005 that fail-closed on a missing anchor; it contradicted FR-006.)*
- **FR-006**: A project with **no chain head and no anchor** (pre-006 store, or fresh
  project) MUST load and operate normally; its first post-006 durable write MUST
  establish the anchor. No migration re-signs old records (same posture as 004).
- **FR-007**: The rollback check MUST run at least at the pack read seam (`snapshot`)
  and on the durable write path. Under a held writer lease the anchor MAY be cached
  alongside the verified-log view, since the lease owner is the project's only writer.
- **FR-008**: A rollback or a damaged anchor for one project MUST NOT affect any other
  project (per-project isolation, as in 004 FR-003 within a project and principal
  isolation across them).
- **FR-009**: The rollback signal MUST go to the operator channel only, never into
  model context, and MUST be distinguishable from an ordinary signature failure (which
  stays silent — no tamper oracle, Constitution I). `verify_integrity()` MUST report a
  rollback alongside its existing counts and 004 chain breaks.
- **FR-010**: The anchor MUST NOT introduce any network, hardware-counter, or external-
  service dependency (Constitution V). It is a local, key-signed file.
- **FR-011**: No anchor material (the watermark or its signature) may reach the model
  context, consistent with 004 FR-008 for `seq`/`chain_prev`.

### Key Entities

- **Rollback anchor**: a per-project record of the highest append sequence the log has
  ever reached, held outside the memory directory and signed by the orchestrator key.
  It is not part of the append-only log and is never projected to the pack or the
  model; it exists solely so the store can be measured against its own past.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A whole-directory rollback that restores a genuinely-signed older state
  is detected and fails closed at the pack read seam in 100% of trials — a new
  rollback MI vector reaches attack-success rate 0.
- **SC-002**: Zero false rollbacks: across a long run of legitimate appends, a
  crash-lag case, and multi-project operation, no read or write raises a rollback.
- **SC-003**: A store created before this feature loads and continues to operate with
  no error, and becomes rollback-protected after its first post-006 durable write.
- **SC-004**: The existing MI suite and 004 composition tests stay green; protected ASR
  is unchanged (0). This feature adds a guarantee and lowers none.
- **SC-005**: An adversary without the key cannot use the anchor to deny service —
  a forged or inflated anchor value never becomes authoritative and never locks a
  legitimate session out of a healthy project.

## Assumptions

- The anchor's storage location shares the orchestrator key's access-control boundary:
  the in-scope adversary (write to `memory/`, no key) can reach neither. Where exactly
  that location is (a path beside the key material) is an implementation decision for
  the plan; the security property is only that it is outside `memory_root` under the
  key's boundary.
- Reuses the existing `sr_agent/memory/hmac.py` signing and canonical encoding; no new
  cryptographic primitive is introduced.
- The residual adversary who also controls the key-store is accepted as out of scope
  and documented, not defended here.
- `log_sequence` (feature 003/004) remains the project-wide monotonic append counter
  the anchor watermarks; this feature adds no second ordering source.
