import json
import pytest
from pathlib import Path

from sr_agent.memory.episodic import EpisodicMemory, MemoryWriteError
from sr_agent.models.memory import MemoryRecord, SourceType, StatusChange

SECRET = b"test-secret-key-32-bytes-exactly!"


@pytest.fixture
def memory(tmp_path: Path) -> EpisodicMemory:
    return EpisodicMemory(tmp_path, SECRET)


def _make_record(**kwargs) -> MemoryRecord:
    defaults = dict(
        project_id="proj1",
        target="Vault.sol",
        source_type=SourceType.tool_output,
        tool="run_slither",
        session_id="sess1",
        finding={"finding_id": "HIGH-001", "severity": "high", "location": "Vault.sol:10", "function_name": "withdraw"},
    )
    defaults.update(kwargs)
    return MemoryRecord(**defaults)


def test_write_load_roundtrip(memory):
    record = _make_record()
    saved = memory.write(record)
    assert saved.hmac is not None

    loaded = memory.load("proj1", "Vault.sol")
    assert len(loaded) == 1
    assert loaded[0].record_id == record.record_id


def test_tampered_record_dropped(memory):
    """A record whose HMAC does not match should be silently dropped on load."""
    record = _make_record()
    memory.write(record)

    # Corrupt the JSONL file directly
    path = memory._path("proj1", "Vault.sol")
    lines = path.read_text().splitlines()
    data = json.loads(lines[0])
    data["project_id"] = "evil-project"    # tamper with a signed field
    path.write_text(json.dumps(data) + "\n")

    loaded = memory.load("proj1", "Vault.sol")
    assert loaded == []   # silently dropped, no exception


def test_status_gate_blocks_llm_inference(memory):
    """LLM inference must not be able to set verified_safe status."""
    record = MemoryRecord(
        project_id="proj1",
        target="Vault.sol",
        source_type=SourceType.llm_inference,
        tool=None,
        session_id="sess1",
        status_change=StatusChange(
            finding_id="HIGH-001",
            old_status="open",
            new_status="verified_safe",
            reason="looks fine to me",
        ),
    )
    with pytest.raises(MemoryWriteError, match="requires source_type=human_input"):
        memory.write(record)


def test_human_input_can_set_verified_safe(memory):
    record = MemoryRecord(
        project_id="proj1",
        target="Vault.sol",
        source_type=SourceType.human_input,
        tool=None,
        session_id="sess1",
        status_change=StatusChange(
            finding_id="HIGH-001",
            old_status="open",
            new_status="verified_safe",
            reason="manually reviewed",
        ),
    )
    saved = memory.write(record)
    assert saved.hmac is not None


def test_supersedes_chain(memory):
    """Record B supersedes record A — only B should appear in load()."""
    record_a = _make_record()
    saved_a = memory.write(record_a)

    record_b = MemoryRecord(
        project_id="proj1",
        target="Vault.sol",
        source_type=SourceType.human_input,
        tool=None,
        session_id="sess1",
        supersedes=saved_a.record_id,
        finding={"finding_id": "HIGH-001-corrected", "severity": "medium",
                 "location": "Vault.sol:10", "function_name": "withdraw"},
    )
    memory.write(record_b)

    loaded = memory.load("proj1", "Vault.sol")
    assert len(loaded) == 1
    assert loaded[0].record_id == record_b.record_id
