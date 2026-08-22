"""`write_memory` is not a dispatch transition (feature 002, D13 / T011).

`session_revision` counts only `dispatch_commit` records; a note must not move
it, must not be findable through `find_committed_bundle`, and must never
itself carry `payload_kind="dispatch_commit"`. Two identical notes are two
things the model said, not one deduplicated thing — an append-only log offers
no dedup on this path, unlike the exactly-once commit path `003` built for
actual external effects.
"""
from __future__ import annotations

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.action import Action, ActionClass
from sr_agent.models.chat import ChatSession
from sr_agent.models.dispatch import DispatchStatus
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.executor import KernelActionExecutor
from sr_agent.orchestrator.lease import WriterLease
from sr_agent.orchestrator.pack import ActionSpec, CapabilityPack

SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"
NOTE = "withdraw() has no reentrancy guard"


def _asserting_pack():
    def boom(action, ctx):
        raise AssertionError("pack.dispatch must not be called for write_memory")

    return CapabilityPack(
        name="fixture",
        actions={"do_thing": ActionSpec(ActionClass.read_only, True, lambda a, r: None)},
        tools=(),
        privileged_statuses=frozenset(),
        reasoning_prompt="",
        dispatch=boom,
        execute_confirmed=lambda a, ctx: ("", None),
        persist_finding=lambda p, ctx: None,
        domain_escalation=lambda *a, **k: None,
        signal_from=lambda aa: None,
    )


def _env(tmp_path):
    session = ChatSession(
        principal=Principal(user_id="u", platform="cli", project_id=PROJECT),
        scope_root=str(tmp_path),
        include=["*"],
    )
    lease = WriterLease(tmp_path, SECRET)
    lease.acquire(PROJECT, session.session_id)
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease)
    executor = KernelActionExecutor(
        memory=memory,
        scope_root=tmp_path,
        pack_id="fixture",
        pack_contract_version="1",
        confirmations_dir=tmp_path / "conf",
        relay_dir=tmp_path / "relay",
    )
    return session, memory, executor


def test_session_revision_is_unmoved_by_a_note(tmp_path):
    session, memory, executor = _env(tmp_path)
    pack = _asserting_pack()

    before = memory.session_revision(PROJECT, session.session_id)
    executor.execute(pack, session, Action(action_type="write_memory", params={"note": NOTE}))
    after = memory.session_revision(PROJECT, session.session_id)

    assert before == after == 0


def test_find_committed_bundle_finds_nothing_for_a_note(tmp_path):
    session, memory, executor = _env(tmp_path)
    pack = _asserting_pack()

    executor.execute(pack, session, Action(action_type="write_memory", params={"note": NOTE}))

    # A note derives no transition_key/operation_id at all (D8/D13); querying
    # with an arbitrary pair must still find nothing, because nothing on this
    # path ever calls commit_if_absent to produce a bundle to find.
    assert memory.find_committed_bundle(
        PROJECT, session.session_id, operation_id="anything", transition_key="anything"
    ) is None


def test_no_dispatch_commit_record_is_ever_created(tmp_path):
    session, memory, executor = _env(tmp_path)
    pack = _asserting_pack()

    executor.execute(pack, session, Action(action_type="write_memory", params={"note": NOTE}))

    assert all(r.payload_kind != "dispatch_commit" for r in memory._all_records(PROJECT))


def test_two_identical_notes_produce_two_records_not_one(tmp_path):
    session, memory, executor = _env(tmp_path)
    pack = _asserting_pack()

    r1 = executor.execute(pack, session, Action(action_type="write_memory", params={"note": NOTE}))
    r2 = executor.execute(pack, session, Action(action_type="write_memory", params={"note": NOTE}))

    assert r1.status is DispatchStatus.ran
    assert r2.status is DispatchStatus.ran
    notes = [r for r in memory._all_records(PROJECT) if r.payload_kind == "model_note"]
    assert len(notes) == 2
    assert notes[0].record_id != notes[1].record_id
    assert notes[0].payload == notes[1].payload == {"note": NOTE}
