"""Under a held lease the anchor is cached in _ProjectView (feature 006, D006-4).

The rollback check must not re-read the anchor file on every snapshot/read while the writer
lease is held — the lease owner is the project's only writer, so the anchor cannot change
underneath it. This mirrors 004's verified-log cache and fails if the anchor read stops
being absorbed by the view.
"""
from pathlib import Path

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.memory import MemoryRecord, SourceType
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.lease import WriterLease


SECRET = bytes.fromhex("12" * 32)
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


def _writer(tmp_path: Path):
    memory_root = tmp_path / "mem"
    lease = WriterLease(memory_root, SECRET)
    memory = EpisodicMemory(memory_root, SECRET, lease=lease, anchor_root=tmp_path / "anchor")
    principal = Principal(user_id="u", platform="cli", project_id=PROJECT)
    lease.acquire(PROJECT, "sess")
    return memory, principal


def test_anchor_is_carried_in_the_project_view(tmp_path: Path):
    memory, principal = _writer(tmp_path)
    for i in range(3):
        memory.write(_finding(i), principal=principal)
    view = memory._cache[PROJECT]
    assert view.anchor == 3            # matches log_max, carried on the view
    memory.write(_finding(3), principal=principal)
    assert memory._cache[PROJECT].anchor == 4   # advanced by the append, no rebuild


def test_repeated_reads_do_not_reread_the_anchor_file(tmp_path: Path):
    memory, principal = _writer(tmp_path)
    for i in range(3):
        memory.write(_finding(i), principal=principal)

    reads = 0
    original = memory._read_anchor

    def counting(project_id):
        nonlocal reads
        reads += 1
        return original(project_id)

    memory._read_anchor = counting
    for _ in range(3):
        memory.snapshot(project_id=PROJECT, session_id="sess")
    assert reads == 0, f"anchor file re-read {reads} time(s) despite a held lease + warm view"
