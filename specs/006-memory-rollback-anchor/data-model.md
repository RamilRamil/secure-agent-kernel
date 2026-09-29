# Phase 1 — Data model: the rollback anchor

No change to `MemoryRecord` or any record shape. This feature adds one out-of-store
artifact and one constructor/config field. Everything inside `memory/<project_id>/` is
untouched (no new `payload_kind`, no signed-shape change, no migration).

## Entity — RollbackAnchor (out-of-store)

One signed file per project, **outside** `memory_root`.

| Field | Type | Meaning |
|---|---|---|
| `project_id` | str | the project this watermark belongs to |
| `watermark` | int | highest project-wide `log_sequence` ever observed for this project |
| `hmac` | str | signature over `{project_id, watermark}` with the orchestrator key |

- **Path**: `<anchor_root>/<project_id>.rollback.json` (D006-2). `anchor_root` is distinct
  from `memory_root` and never a subdirectory of it (enforced at construction, D006-1).
- **Signing**: identical mechanism to `_chain_head.json` / `_writer_lease.json` —
  `hmac_module.sign({project_id, watermark}, key)`, verified on read. A file that does not
  verify is treated as **absent** (D006-3), never as an authoritative value.
- **Not a record**: never enters the append-only log, `snapshot`, `SnapshotItem`,
  `for_llm_context`, or `verify_integrity`'s per-target counts. It appears only in the new
  `IntegrityReport.rollbacks` map (operator channel).

## The rule (single source of truth)

Let `L` = `max(log_sequence)` over the authenticated records of the project, `V` = the
verified watermark (or ∅ if the anchor is absent/unverifiable).

| Condition | Verdict | Where enforced |
|---|---|---|
| `V = ∅` | not yet anchored → **proceed**; write establishes `V ← L` | read seam, write path |
| `V ≤ L` | healthy (steady state, or crash-lag `V < L`) → **proceed** | read seam, write path |
| `V > L` | **ROLLBACK → fail closed** (raise, do not serve/append) | read seam, write path |

Monotonic maintenance: on every successful durable write, after the record and chain head
are durable, `V ← max(V, L_new)` (D006-5). `V` is therefore non-decreasing across the life
of the store; only a restore of an older `memory/` (dropping `L`) can produce `V > L`.

## Lifecycle / states

```
(no anchor, pre-006 or fresh)
        │  first post-006 durable write
        ▼
   V = L  ──────── every write ────────►  V = L'  (L' ≥ L, monotonic)
        │                                    │
        │  memory/ rolled back to L₀ < V     │  crash between head and anchor
        ▼                                    ▼
   V > L₀  → ROLLBACK (fail closed)     V < L (lag) → proceed; next write sets V = L
```

## Config / construction surface

- `KernelConfig.anchor_root: Path | None` — new field; env `SR_ANCHOR_ROOT`. The composition
  root places it outside `memory_root` on a hardened access boundary (D006-1). `None` (unset)
  ⇒ guard inert.
- `EpisodicMemory.__init__(..., anchor_root: Path | None = None)` — bound like `lease` /
  `event_sink` / `privileged_statuses`. Construction raises if `anchor_root` is inside
  `memory_root` (fail fast on a misconfiguration that would silently void the guarantee).

## In-memory cache field

- `_ProjectView.anchor: int | None` — the verified watermark carried alongside the verified
  log while the writer lease is held (D006-4). Populated when the view is built, advanced by
  `_extend_cache` on each append. Absent view (no lease) ⇒ read the file directly, but a
  reader role has no `anchor_root` so the guard is inert there anyway.
