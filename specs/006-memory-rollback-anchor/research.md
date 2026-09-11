# Phase 0 — Research: Rollback detection for the episodic store

Decisions D006-1 … D006-9. Each resolves an unknown the spec left to the plan, or
reconciles a tension the spec's own text created. Two (D006-2, D006-3) change what the
spec said and are called out as **spec deltas** — see the plan's report.

---

## D006-1 — Where the anchor lives, and who wires it

**Decision**: Add an `anchor_root: Path | None` constructor parameter to
`EpisodicMemory`, bound by the composition root exactly as `lease`, `event_sink`, and
`privileged_statuses` are (D5 pattern). Add a matching `anchor_root` field to
`KernelConfig` (env `SR_ANCHOR_ROOT`). When `anchor_root is None` the rollback guard is
**inert** — the same posture as "no lease ⇒ no cache": the mechanism exists, the
composition root decides whether this deployment arms it.

**Rationale**: The spec said "adjacent to the key material (same access-control boundary
as the secret key)." Grounding that against the code changes the honest framing: the
secret key is an **environment variable** (`SR_SECRET_KEY`, `config.py:64`), not a file,
so there is no filesystem "beside the key" to sit next to. What the property actually
requires is a path **outside `memory_root`** whose access control the operator hardens so
the in-scope adversary (write to `memory/`, no key) cannot reach it.

The kernel cannot verify that separation — it is a deployment fact (separate uid, stricter
perms, a different mount). So the kernel's job is to (a) accept an `anchor_root` distinct
from `memory_root`, (b) refuse to silently place the anchor inside `memory_root`, and (c)
document that the guarantee is only as strong as the operator's `anchor_root` vs
`memory_root` access separation. This is the same shape as the existing, unstated
assumption that `SR_SECRET_KEY` is not readable from `memory/`.

**Alternatives rejected**:
- *Derive the location from `memory_root.parent`* — still on the memory volume the
  adversary may own. Rejected: defeats the entire point.
- *Always-on, kernel-chosen path* — the kernel has no basis to pick a path with the right
  access boundary, and a wrong default (a sibling dir with identical perms) would advertise
  protection it does not have. Binding it at the composition root is honest and matches
  every other trust-relevant dependency in this class.

---

## D006-2 — One anchor file per project (SPEC DELTA vs a single map)

**Decision**: One signed file per project: `<anchor_root>/<project_id>.rollback.json`,
holding `{project_id, watermark, hmac}`. Not a single `_chain_head`-style map of all
projects in one file.

**Rationale**: FR-008 requires that a damaged anchor for one project not affect another.
A single shared file makes one corrupt or half-written entry fail-close **every** project
that has a head — the opposite of FR-008. Per-project files confine a crash/corruption to
its own project, and match principal isolation (each project already owns its own
`memory/<project_id>/` subtree). The watermark is the project-wide `log_sequence` high mark
(003/004's existing counter), so no second ordering source is introduced (FR-001, and
the spec's closing assumption).

**Alternatives rejected**: single-file map (fails FR-008 isolation as above); a file
*inside* `memory/<project_id>/` (adversary-writable — the whole thing must be outside).

---

## D006-3 — "No usable anchor" adopts; it does NOT fail closed (SPEC DELTA vs FR-005)

**The tension**: FR-005 says *head present + anchor missing/unverifiable ⇒ fail closed
(tampering)*. FR-006 says *pre-006 store ⇒ loads normally*. But every pre-006 store made
after feature 004 **has a signed `_chain_head`** and **no anchor**. Taken literally, FR-005
fail-closes every existing 004 store on its first read. The two requirements contradict for
the realistic case.

**Decision**: Resolve in favour of compatibility and the declared trust boundary. The rule
on the read/write path is exactly the asymmetric comparison, with "no usable anchor" meaning
*not yet anchored*, not *tampered*:

| Anchor state vs `log_max = L` | Verdict |
|---|---|
| verified watermark `V`, `V > L` | **ROLLBACK → fail closed** |
| verified watermark `V`, `V ≤ L` | proceed (≤ covers steady state and crash-lag) |
| absent, or present but fails HMAC | **not yet anchored → proceed**; the next durable write establishes/repairs the anchor at `L` |

**Why relaxing a fail-closed is correct here, not a weakening**: FR-005 was defending
against "the adversary deleted the anchor." But the anchor is, by FR-001/D006-1, outside the
in-scope adversary's reach — they have write to `memory/`, not to `anchor_root`. So *missing
anchor is never something the in-scope adversary can cause*. The only actors who can delete
it are a crash, disk corruption, or the **out-of-scope** adversary who already controls the
key-store location. Failing closed on missing-anchor therefore buys **zero** security against
the in-scope threat while bricking every innocent pre-006 store — a self-inflicted denial of
service in exchange for nothing.

The real rollback protection is untouched: a *full-directory rollback* restores an older
`memory/` while the external anchor (out of the adversary's reach) keeps its high watermark,
so `V > L` fires by the first row above. The adversary cannot reach the third row without
also reaching `anchor_root`, which is the residual out-of-scope case the spec already names.

**Consequence for the spec**: FR-005 must be rewritten to "*head present + anchor **present
but not exceeding, or absent*** …", i.e. missing-anchor is the not-yet-anchored path. Flagged
in the report for the operator's sign-off before the spec text is edited.

**Alternatives considered**:
- *Keep FR-005 fail-closed + add a signed "anchored" marker inside `_chain_head`* to tell
  pre-006 (no marker) from anchor-deleted (marker present). This defends the anchor-deletion
  case too — but that case is only reachable by the out-of-scope adversary, so it adds a head
  shape change and a 004 interaction to defend something already declared out of scope.
  Rejected by YAGNI and by "don't complicate the in-scope story to chase an out-of-scope one."
  Recorded here so a future reviewer sees it was a conscious call, not an oversight.

---

## D006-4 — Check points: `snapshot()` and the write path only

**Decision**: Evaluate the rule in `EpisodicMemory.snapshot()` (the pack's read seam) and
on the durable write path (`write()`), not in generic `load()`.

**Rationale**: The pack projection is the primary MI vector, and the write path must refuse
to append onto a rolled-back log or exactly-once (003) is defeated. `load()` /
`load_for_principal` feed chat-history reconstruction; adding the check there too is the
"everywhere" option the operator explicitly did not choose (cost vs. the snapshot seam
already being the gate that matters). Under a held writer lease the anchor value is carried
in `_ProjectView` (new field) and refreshed by `_extend_cache`, so the hot path reads the
anchor file at most once per view — consistent with 004's cache story. Without a lease
(reader role) there is no view and the guard is inert anyway (no `anchor_root` on a reader,
D006-1), so no per-`load` file read is added.

---

## D006-5 — Bump order: the anchor is the LAST durable artifact of a write

**Decision**: In `write()`, call `_bump_anchor(project_id, new_max)` **after** the record
append and `_write_head`, before `_emit_write`. The persisted order is: record line
(fsynced) → chain head → anchor.

**Rationale**: FR-002. Making the anchor last means a crash can only ever leave it **behind**
the log (`V < L`, the tolerated row), never ahead (`V > L`, the rollback row). A false
rollback from our own crash is thereby impossible by construction. The next successful write
advances `V` to the new `L`. This also composes with `_recover_torn_tails`: a torn final
record never counted, its head update never ran, and its anchor bump never ran — so after
truncation `L`, head, and `V` agree.

---

## D006-6 — Operator reporting via `verify_integrity`, kept off the model channel

**Decision**: `IntegrityReport` gains a `rollbacks: dict[str, str]` (project_id → reason).
`verify_integrity()` populates it when `V > L`. The rollback is logged on the operator
channel (WARNING), distinct from the silent signature-drop.

**Rationale**: FR-009 / Constitution I. A rollback is a **composition-class** signal (like
004's chain break): it is caused by manipulation of validly-signed material, carries no
tamper-oracle (the attacker learns only "noticed", never a bit of secret), and must be
visible to the operator for incident response. An ordinary signature failure stays silent.
Reuse 004's `_report_chain_break` sibling pattern; do not fold rollback into `chain_breaks`,
so the operator can tell "records disagree with each other" from "the store is an older whole
copy of itself."

---

## D006-7 — Nothing new reaches the model

**Decision**: The watermark and its signature never enter `SnapshotItem`,
`for_llm_context()`, or any model-facing surface. There is nothing to strip because the
anchor is not a record field — it lives in its own file. Asserted by a test regardless
(FR-011), mirroring 004 FR-008's stance on `seq`/`chain_prev`.

---

## D006-8 — Compatibility: no migration, adopt on first write

**Decision**: A pre-006 store (signed head, no anchor) reads normally (D006-3, third row)
and is adopted — the anchor is established at the current `log_max` — on its first post-006
durable write. No record is re-signed; no file shape inside `memory/` changes.

**Rationale**: Same posture as 004's pre-chain records (spec 004 Decisions). A store is
rollback-protected from its first post-006 write onward (SC-003); the pre-first-anchor window
is inherent to any watermark scheme and is documented, not hidden.

---

## D006-9 — Test harness for a whole-directory rollback

**Decision**: Tests simulate the attack by keeping `anchor_root` in a **separate** tmp
subtree from `memory_root`, snapshotting `memory/<project>/` to a sidecar dir, advancing the
log, then copying the sidecar back over `memory/<project>/` (restoring `.jsonl` + head + lease
together) while `anchor_root` is left untouched — reproducing "adversary owns memory/, not the
anchor." Assert `snapshot()` and `write()` raise `MemoryRollbackDetected`.

**Rationale**: This is the only faithful reproduction — mutating individual records would trip
004's chain instead and prove nothing about rollback. `shutil.copytree`/`rmtree` on the
project subtree, anchor kept aside, is the minimal honest fixture. Security-critical ⇒
test-first (constitution Development Workflow).
