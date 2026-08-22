"""The `memory_write` live-trace event (feature 002, FR-007 / FR-008, D9).

Every successful `EpisodicMemory.write` fires exactly one `memory_write` event,
after the append is durable, carrying metadata only -- never a record body,
never signature material. There is no exempt writer, and the event never
affects the write itself: absent, present, or raising, the sink cannot change
what lands on disk.

See specs/002-memory-write-path/contracts/memory-write-event.md for the
contract this file pins.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.action import Action
from sr_agent.models.chat import ChatSession
from sr_agent.models.dispatch import (
    DispatchPayload,
    DispatchResult,
    DispatchStatus,
    PendingKind,
    PendingWait,
)
from sr_agent.models.memory import MemoryRecord, SourceType
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.chat_session import save_turn
from sr_agent.orchestrator.executor import KernelActionExecutor

SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"

EVENT_KEYS = {
    "type", "record_id", "project_id", "target", "session_id",
    "source_type", "payload_kind", "log_sequence",
}


class Recorder:
    """A sink that just appends every event it sees."""

    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(event)


class RaisingSink:
    def __call__(self, event: dict) -> None:
        raise RuntimeError("broken observer")


def _make_record(target: str = "Vault.sol", note: str = "n") -> MemoryRecord:
    return MemoryRecord(
        project_id=PROJECT,
        target=target,
        source_type=SourceType.tool_output,
        tool="slither",
        session_id="sess-1",
        payload={"note": note},
        payload_kind="dispatch_payload",
    )


@pytest.fixture
def sink():
    return Recorder()


@pytest.fixture
def memory(tmp_path, sink):
    return EpisodicMemory(tmp_path, SECRET, event_sink=sink)


# ── T016: exact key set ──────────────────────────────────────────────────────


def test_one_write_produces_exactly_one_event_with_the_exact_key_set(memory, sink):
    memory.write(_make_record())

    assert len(sink.events) == 1
    assert set(sink.events[0]) == EVENT_KEYS


def test_event_values_match_the_written_record(memory, sink):
    written = memory.write(_make_record(target="Vault.sol", note="hello"))
    event = sink.events[0]

    assert event["type"] == "memory_write"
    assert event["record_id"] == written.record_id
    assert event["project_id"] == written.project_id
    assert event["target"] == written.target
    assert event["session_id"] == written.session_id
    assert event["source_type"] == written.source_type.value
    assert isinstance(event["source_type"], str)  # the enum VALUE, not the enum
    assert event["payload_kind"] == written.payload_kind
    assert event["log_sequence"] == written.log_sequence


# ── T017: exclusions ─────────────────────────────────────────────────────────


def test_signature_material_and_body_are_absent_from_the_event(memory, sink):
    memory.write(_make_record())
    event = sink.events[0]

    for forbidden in ("hmac", "seq", "chain_prev", "payload", "finding", "checkpoint"):
        assert forbidden not in event


# ── T018: emission scope -- there is no exempt writer ───────────────────────


def test_a_finding_persist_emits_one_event(memory, sink):
    record = MemoryRecord(
        project_id=PROJECT,
        target="Vault.sol",
        source_type=SourceType.tool_output,
        tool="run_slither",
        session_id="sess-1",
        finding={
            "finding_id": "H-1", "severity": "high",
            "location": "Vault.sol:1", "function_name": "f",
        },
    )
    memory.write(record)

    assert len(sink.events) == 1
    assert sink.events[0]["payload_kind"] is None


def test_a_chat_turn_emits_one_event(memory, sink):
    from sr_agent.models.chat import ChatTurn

    session = ChatSession(principal=Principal(user_id="u", platform="cli", project_id=PROJECT))
    turn = ChatTurn(
        turn_id="t-1",
        session_id=session.session_id,
        source_type=SourceType.external_llm_output,
        user_message="hi",
    )
    sink.events.clear()
    save_turn(session, turn, memory)

    turn_events = [e for e in sink.events if e["payload_kind"] == "chat_turn"]
    assert len(turn_events) == 1


def test_a_commit_if_absent_bundle_emits_one_event(memory, sink):
    memory.commit_if_absent(
        project_id=PROJECT,
        target="Vault.sol",
        session_id="sess-1",
        tool="do_thing",
        operation_id="op-1",
        transition_key="tk-1",
        expected_revision=0,
        payloads=[DispatchPayload(body={"k": "v"})],
    )

    events = [e for e in sink.events if e["payload_kind"] == "dispatch_commit"]
    assert len(events) == 1


def test_a_put_external_response_if_absent_ingest_emits_one_event(memory, sink):
    memory.put_external_response_if_absent(
        project_id=PROJECT,
        target="Vault.sol",
        session_id="sess-1",
        operation_id="op-1",
        correlation_id="corr-1",
        body={"answer": "yes"},
    )

    events = [e for e in sink.events if e["payload_kind"] == "external_response"]
    assert len(events) == 1


def test_a_write_pause_checkpoint_emits_one_event(tmp_path, sink):
    from sr_agent.orchestrator.lease import WriterLease

    lease = WriterLease(tmp_path, SECRET)
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease, event_sink=sink)
    session = ChatSession(principal=Principal(user_id="u", platform="cli", project_id=PROJECT))
    lease.acquire(PROJECT, session.session_id)
    executor = KernelActionExecutor(
        memory=memory,
        scope_root=tmp_path,
        pack_id="fixture",
        pack_contract_version="1",
        confirmations_dir=tmp_path / "conf",
        relay_dir=tmp_path / "relay",
    )
    result = DispatchResult(
        status=DispatchStatus.pending,
        body="waiting",
        pending=PendingWait(kind=PendingKind.external_response, correlation_id="op-1"),
    )

    sink.events.clear()
    executor.write_pause_checkpoint(
        session, result, Action(action_type="do_thing", params={}),
        transition_key="tk-1", operation_id="op-1", expected_revision=0,
        turn_id=None, user_message="", system_prompt_id="", system_prompt_hash="",
        tool_calls_used=0, phase="dispatch",
    )

    events = [e for e in sink.events if e["payload_kind"] == "pause_checkpoint"]
    assert len(events) == 1


# ── T019: read-only paths emit nothing ──────────────────────────────────────


def test_load_emits_nothing(memory, sink):
    memory.write(_make_record())
    sink.events.clear()

    memory.load(PROJECT, "Vault.sol")

    assert sink.events == []


def test_verify_integrity_emits_nothing(memory, sink):
    memory.write(_make_record())
    sink.events.clear()

    memory.verify_integrity(PROJECT)

    assert sink.events == []


# ── T020: no sink, and a raising sink, never affect the write ──────────────


def _stripped(record: MemoryRecord) -> dict:
    """Model dump with the volatile fields excluded.

    `record_id` and `timestamp` vary run to run by construction. `hmac` is
    signed over both of them (`fields_for_hmac`), so it necessarily differs
    too even when every substantive field is identical -- excluding it here
    is the "or excluded" half of the instruction, not a weaker check.
    """
    return record.model_dump(exclude={"record_id", "timestamp", "hmac"})


def test_write_succeeds_with_no_sink(tmp_path):
    memory = EpisodicMemory(tmp_path, SECRET, event_sink=None)
    written = memory.write(_make_record())
    assert written.payload == {"note": "n"}


def test_a_raising_sink_does_not_break_the_write(tmp_path):
    no_sink_memory = EpisodicMemory(tmp_path / "a", SECRET, event_sink=None)
    no_sink_record = no_sink_memory.write(_make_record())

    raising_memory = EpisodicMemory(tmp_path / "b", SECRET, event_sink=RaisingSink())
    raising_record = raising_memory.write(_make_record())

    assert _stripped(raising_record) == _stripped(no_sink_record)
    assert raising_memory.load(PROJECT, "Vault.sol") == [raising_record]


def test_no_exception_escapes_write_when_the_sink_raises(tmp_path):
    memory = EpisodicMemory(tmp_path, SECRET, event_sink=RaisingSink())
    # Would raise RuntimeError("broken observer") if the guard were missing.
    memory.write(_make_record())


# ── T021: durability ordering -- the sink sees its own record already on disk


def test_sink_sees_its_own_record_already_durable(tmp_path):
    seen = {}

    def sink(event: dict) -> None:
        path = tmp_path / PROJECT / "Vault.sol.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        seen["line_count"] = len(lines)
        seen["last_line_has_record_id"] = event["record_id"] in lines[-1]

    memory = EpisodicMemory(tmp_path, SECRET, event_sink=sink)
    memory.write(_make_record())

    assert seen["line_count"] == 1
    assert seen["last_line_has_record_id"] is True


# ── T022: re-entrancy guard ──────────────────────────────────────────────────


def test_a_sink_that_writes_memory_does_not_recurse(tmp_path):
    calls = {"count": 0}
    events: list[dict] = []

    def recursive_sink(event: dict) -> None:
        calls["count"] += 1
        events.append(event)
        if calls["count"] == 1:
            # A broken observer. The guard must stop this from recursing
            # forever; it does not license the sink writing memory at all.
            memory.write(_make_record(target="Token.sol", note="nested"))

    memory = EpisodicMemory(tmp_path, SECRET, event_sink=recursive_sink)
    outer = memory.write(_make_record(target="Vault.sol", note="outer"))

    # Outer write completed normally...
    assert outer.payload == {"note": "outer"}
    # ...the nested write also completed (it is on disk)...
    nested = memory.load(PROJECT, "Token.sol")
    assert len(nested) == 1
    assert nested[0].payload == {"note": "nested"}
    # ...but only the outer write emitted -- the nested one was suppressed.
    assert calls["count"] == 1
    assert len(events) == 1
    assert events[0]["target"] == "Vault.sol"


# ── Feature 005: the moved finding write still emits, and a pause emits twice ─


def test_a_finding_write_still_emits_after_the_persist_moved(tmp_path) -> None:
    """kernel/005 moved the finding persist to after the action resolves.

    A moved call site is exactly how an event quietly stops firing, and nothing
    downstream would report the loss — the trace is not the record of truth, so a
    consumer is forbidden from treating a missing event as evidence. Hence a test
    rather than an assumption.

    `payload_kind` stays empty on findings, which is deliberate: `_snapshot_kind`
    reads it first, and giving findings a kind would drop them out of the pack's
    projection. Consumers are already required to tolerate an unrecognised kind,
    so `None` costs them nothing.
    """
    import os
    os.environ.setdefault("SR_SECRET_KEY", "00" * 32)
    from sr_agent.llm_core.schemas import AgentAction, FindingPayload
    from sr_agent.models.chat import ChatSession
    from sr_agent.models.principal import Principal
    from sr_agent.orchestrator.executor import KernelActionExecutor
    from sr_agent.orchestrator.lease import WriterLease
    from sr_agent.orchestrator.loop import OrchestratorLoop
    from sr_agent.models.dispatch import DispatchResult, DispatchStatus
    from tests.unit.test_finding_provenance_paths import _pack, _paused, _StubReasoning

    events: list[dict] = []
    session = ChatSession(
        principal=Principal(user_id="u", platform="cli", project_id=PROJECT),
        scope_root=str(tmp_path), include=["*"],
    )
    lease = WriterLease(tmp_path, SECRET)
    lease.acquire(PROJECT, session.session_id)
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease, event_sink=events.append)
    loop = OrchestratorLoop(
        session, memory, tmp_path, pack=_pack(_paused),
        reasoning_provider=_StubReasoning([
            AgentAction(next_action="do_thing", tool_params={},
                        finding=FindingPayload(finding_id="F-1", location="Vault.sol:42",
                                               function_name="withdraw", severity="high")),
        ]),
        confirmations_dir=tmp_path / "conf",
    )
    loop._executor = KernelActionExecutor(
        memory=memory, scope_root=tmp_path, pack_id="fixture",
        pack_contract_version="1", confirmations_dir=tmp_path / "conf",
        relay_dir=tmp_path / "relay",
    )

    loop.run_turn(user_message="look", system_prompt="p")
    finding_events = [e for e in events if e["target"] == "Vault.sol"]
    assert len(finding_events) == 1
    assert finding_events[0]["payload_kind"] is None
    assert finding_events[0]["source_type"] == "external_llm_output"

    loop._resolve_paused_findings(DispatchResult(status=DispatchStatus.ran, body="done"))
    finding_events = [e for e in events if e["target"] == "Vault.sol"]
    assert len(finding_events) == 2, "the pause pair must emit twice, not once"
    assert finding_events[0]["record_id"] != finding_events[1]["record_id"]
