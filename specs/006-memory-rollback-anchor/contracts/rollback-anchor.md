# Contract — Rollback anchor (kernel ⇄ composition root)

The rollback guard is a mechanism the kernel provides and the **composition root arms**.
This contract is what a caller (the `sr-agent` CLI / `audit_agent` in Repo B, or any
embedder) must do and must expect. Analogous to the lease and privileged-status contracts.

## What the composition root MUST do to arm the guard

1. Provide `anchor_root` — a path **outside `memory_root`**, on an access boundary the
   memory-write adversary cannot cross (separate uid / perms / mount). Wire it via
   `SR_ANCHOR_ROOT` → `KernelConfig.anchor_root` → `EpisodicMemory(anchor_root=...)`.
2. Construct writer-role `EpisodicMemory` instances with that `anchor_root`. A memory built
   without it has an **inert** guard (no rollback detection) — acceptable for reader-only
   inspection, never for the session writer.

**Kernel guarantee**: the protection is exactly as strong as the `anchor_root` vs
`memory_root` access separation the operator provides. The kernel enforces only that
`anchor_root` is not inside `memory_root` (construction-time error); it cannot verify the
perms are actually stronger. Stated, not assumed.

## New failure the caller MUST handle

`EpisodicMemory.snapshot(...)` and `EpisodicMemory.write(...)` may raise:

```
MemoryRollbackDetected(MemoryWriteError)
    "Refusing to <serve a snapshot for | append to> project <id>: the store's
     recorded high-water mark (<V>) exceeds the log it now contains (<L>). This is
     consistent with the memory directory being restored from an older backup.
     Run `sr-agent memory verify`."
```

- It is **fail-closed**: no projection is returned and no record is appended.
- It is distinct from `MemoryCompositionBreak` (004, records disagree with each other) and
  from the silent signature drop (Constitution I). A caller SHOULD surface it to the operator
  and MUST NOT retry blindly — a retry re-hits the same rolled-back state.
- The operator remedy is out-of-band: restore the correct `memory/` state or re-establish
  trust, then `sr-agent memory verify`. The kernel never auto-repairs a rollback (it cannot
  know which side is authoritative).

## `verify_integrity()` report addition

`IntegrityReport` gains:

```
rollbacks: dict[str, str]   # project stem/id -> human reason ("watermark V exceeds log L")
has_rollback: bool          # property, True when rollbacks is non-empty
```

Populated on the operator-run scan only. Reported alongside `chain_breaks`, never folded
into it (the two are different failures).

## What does NOT change

- No `MemoryRecord` field, no `payload_kind`, no `SNAPSHOT_KINDS` entry, no signed-shape
  change. A pre-006 store verifies and reads unchanged until its first post-006 write.
- No model-facing surface changes: the watermark never reaches `SnapshotItem` or
  `for_llm_context`.
- The pack (`PackContext`) is not involved and gains nothing — this is entirely a
  kernel-plane guarantee.
