"""Pre-006 compatibility for the rollback anchor (feature 006, D006-3 / D006-8).

A store written before this feature has a signed 004 chain head and NO anchor. That MUST
NOT be treated as tampering (it would brick every existing store); it is the
not-yet-anchored case — reads proceed and the first post-006 write establishes the anchor.
"""
from pathlib import Path

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.memory import MemoryRecord, SourceType
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.lease import WriterLease


SECRET = bytes.fromhex("ef" * 32)
PROJECT = "proj1"


def _finding(i: int) -> MemoryRecord:
    return MemoryRecord(
        project_id=PROJECT,
        target="Vault.sol",
        source_type=SourceType.tool_output,
        tool="orchestrator",
        session_id="sess",
        payload_kind="finding",
        finding={"finding_id": f"H-{i}", "title": "n"},
    )


def test_pre006_store_loads_then_first_write_adopts_the_anchor(tmp_path: Path):
    memory_root = tmp_path / "mem"
    anchor_root = tmp_path / "anchor"
    p = Principal(user_id="u", platform="cli", project_id=PROJECT)

    # 1. A pre-006 writer: no anchor_root at all — head is written, no anchor exists.
    lease = WriterLease(memory_root, SECRET)
    pre = EpisodicMemory(memory_root, SECRET, lease=lease)
    lease.acquire(PROJECT, "sess")
    for i in range(3):
        pre.write(_finding(i), principal=p)
    assert not anchor_root.exists()   # nothing under the anchor root yet

    # 2. A 006-era writer opens the same store WITH an anchor_root.
    post = EpisodicMemory(memory_root, SECRET, lease=lease, anchor_root=anchor_root)

    # Reads must not fail closed on the missing anchor (not-yet-anchored, not tampering).
    assert len(post.load(PROJECT, "Vault.sol")) == 3
    post.snapshot(project_id=PROJECT, session_id="sess")   # no raise
    assert post._read_anchor(PROJECT) is None

    # 3. The first post-006 durable write establishes the anchor at the current log max.
    post.write(_finding(3), principal=p)
    assert (anchor_root / f"{PROJECT}.rollback.json").exists()
    assert post._read_anchor(PROJECT) == 4   # log_max after 4 writes
