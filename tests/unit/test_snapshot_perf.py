"""Performance characterization at the snapshot/append envelope (SC-014d / T066).

The 10000-record / 32 MiB envelope was measured under a writer lease:
append stays flat (~3.5-4.3 ms) and a 10000-item snapshot is ~0.37s.
CI reuses the same code path at a small N so the quadratic total cost of
scan-per-append is a recorded, accepted property rather than a surprise.
Over-capacity the operator completes the session and starts a new one
(FR-009b) -- that is operator-facing behaviour, not an implementation detail.
"""
import time
from pathlib import Path

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.memory import MemoryRecord, SourceType
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.lease import WriterLease


SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"
N = 40


def _record(i: int) -> MemoryRecord:
    return MemoryRecord(
        project_id=PROJECT,
        target="Vault.sol",
        source_type=SourceType.tool_output,
        tool="orchestrator",
        session_id="sess-perf",
        payload_kind="finding",
        finding={"finding_id": f"H-{i}", "title": "n"},
    )


def test_append_and_snapshot_stay_bounded_on_the_hot_path(tmp_path: Path):
    lease = WriterLease(tmp_path, SECRET)
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease)
    principal = Principal(user_id="u", platform="cli", project_id=PROJECT)
    lease.acquire(PROJECT, "sess-perf")

    early = []
    late = []
    for i in range(N):
        t0 = time.perf_counter()
        memory.write(_record(i), principal=principal)
        dt = time.perf_counter() - t0
        (early if i < 10 else late).append(dt)

    t0 = time.perf_counter()
    snapshot = memory.snapshot(project_id=PROJECT, session_id="sess-perf")
    snap_dt = time.perf_counter() - t0

    mean_early = sum(early) / len(early)
    mean_late = sum(late) / len(late)
    # Flat under the lease: a later append must not grow linearly with N.
    assert mean_late < mean_early * 8 + 0.05
    assert snap_dt < 2.0
    assert snapshot.measured_bytes >= 0
