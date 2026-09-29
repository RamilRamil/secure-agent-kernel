# Quickstart — Rollback detection

Runnable walkthrough of the seven scenarios. No network, no Docker; `SR_SECRET_KEY` is the
only env dependency, and the tests pass keys/roots directly. Run:

```bash
.venv/bin/python -m pytest tests/security/test_memory_rollback.py -v
```

## The attack, reproduced (US1 / SC-001)

The faithful reproduction keeps `anchor_root` in a subtree the "adversary" never touches:

```python
memory_root = tmp / "mem"
anchor_root = tmp / "anchor"          # outside memory_root, adversary cannot reach
lease = WriterLease(memory_root, SECRET)
mem = EpisodicMemory(memory_root, SECRET, lease=lease, anchor_root=anchor_root)
lease.acquire(PROJECT, "sess")

# advance the log; a human correction retracts H-3 partway through
for i in range(5):
    mem.write(_finding(f"H-{i}"), principal=P)
retract_h3 = mem.write(_human_supersede_of("H-3"), principal=P)   # log_max now 6

# adversary snapshots memory/<project>/ at an EARLIER point and restores it whole
backup = snapshot_dir(memory_root / PROJECT)      # taken before the retraction
... more writes ...                               # log advances further
restore_over(memory_root / PROJECT, backup)       # .jsonl + head + lease rolled back
# anchor_root is left untouched -> it still records the high watermark

with pytest.raises(MemoryRollbackDetected):
    mem.snapshot(project_id=PROJECT, session_id="sess")   # V > L -> fail closed
```

Without this feature the snapshot would return the pre-retraction projection and H-3 would
be live again. With it, the watermark in `anchor_root` (untouched by the restore) exceeds the
rolled-back log and the read fails closed.

## The write refusal (US2 / SC-001)

```python
restore_over(memory_root / PROJECT, backup)       # rolled back again
with pytest.raises(MemoryRollbackDetected):
    mem.write(_finding("H-new"), principal=P)      # nothing appended onto the stale log
```

## Head present, anchor missing (US2 — reconciled, D006-3)

A pre-006 store has a signed head and no anchor. This is **not** treated as tampering
(that would brick every 004 store); it is the not-yet-anchored path:

```python
mem_no_anchor_yet = EpisodicMemory(memory_root, SECRET, lease=lease, anchor_root=fresh_anchor)
mem_no_anchor_yet.load(PROJECT, target)            # loads normally, no raise
mem_no_anchor_yet.write(_finding("H-0"), principal=P)   # establishes the anchor at L
assert (fresh_anchor / f"{PROJECT}.rollback.json").exists()
```

## No false rollback (US3 / SC-002)

```python
for i in range(50):
    mem.write(_finding(f"H-{i}"), principal=P)      # steady growth, V tracks L
    mem.snapshot(project_id=PROJECT, session_id="sess")   # never raises
```

Crash-lag: simulate a write whose head landed but whose anchor bump did not (leave the
anchor file at `L-1`); the next read does not raise and the next write catches `V` up to `L`.

## Forged anchor cannot lock a project out (US3 / SC-005)

```python
# an adversary who cannot sign writes a huge watermark with a bogus hmac
poison_anchor(anchor_root / f"{PROJECT}.rollback.json", watermark=10**9, hmac="bogus")
mem.snapshot(project_id=PROJECT, session_id="sess")   # bogus sig -> treated as absent -> proceeds
```

A verifying watermark can only come from the key holder, so a keyless adversary cannot raise
`V` to force a permanent `V > L` lockout.

## Multi-project isolation (FR-008)

A rollback or a corrupt anchor for project A leaves project B's reads and writes unaffected —
per-project anchor files, per-project comparison.

## Operator scan (FR-009)

```python
report = EpisodicMemory(memory_root, SECRET, anchor_root=anchor_root).verify_integrity(PROJECT)
assert report.has_rollback
assert PROJECT in report.rollbacks           # distinct from report.chain_breaks
```
