"""Operator session lifecycle (feature 003, FR-019 / FR-014).

`complete` / `abandon` / `detach` / `takeover` / `rebind` are operator acts.
They are absent from the model vocabulary because a turn that could complete
itself, or take over a paused reservation, would be exercising human authority
from inside the loop. Each one therefore writes a `human_input` control event
so the reason the writer role changed is in the log, not inferred from silence.
"""
import pytest

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.action import LOOP_TERMINALS
from sr_agent.models.chat import ChatSession
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.action import KERNEL_GENERIC_ACTIONS
from sr_agent.orchestrator.chat_session import (
    SessionLifecycleError,
    abandon_session,
    complete_session,
    detach_session,
    reacquire_lease,
    takeover_lease,
)
from sr_agent.orchestrator.lease import LeaseMode, LeaseUnavailable, WriterLease
from sr_agent.orchestrator.pack import CapabilityPack


SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"


@pytest.fixture
def env(tmp_path):
    lease = WriterLease(tmp_path, SECRET)
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease)
    session = ChatSession(
        principal=Principal(user_id="u", platform="cli", project_id=PROJECT)
    )
    lease.acquire(PROJECT, session.session_id)
    return session, memory, lease


def _events(memory, session):
    return [
        r for r in memory.load(PROJECT, f"chat:{session.session_id}")
        if r.payload_kind == "control_event"
    ]


def test_complete_records_a_human_input_event_and_releases_the_lease(env):
    session, memory, lease = env
    complete_session(session, memory, approved_by="operator")

    assert session.status == "completed"
    assert lease.state(PROJECT).mode is LeaseMode.completed
    events = _events(memory, session)
    assert events[0].source_type.value == "human_input"
    assert events[0].payload["event"] == "complete_session"


def test_a_completed_session_cannot_write_again_even_after_someone_else_uses_the_project(env):
    """The durable answer the lease file cannot hold (see T039 xfail).

    The lease has one slot, so it forgets sess-1 completed once sess-2 takes the
    project. SessionStatus is what remembers, and reacquire_lease consults it.
    """
    session, memory, lease = env
    complete_session(session, memory, approved_by="operator")

    other = ChatSession(
        principal=Principal(user_id="u", platform="cli", project_id=PROJECT)
    )
    lease.acquire(PROJECT, other.session_id)
    detach_session(other, memory, approved_by="operator")

    with pytest.raises((LeaseUnavailable, SessionLifecycleError)):
        reacquire_lease(session, memory)


def test_abandon_is_terminal_too(env):
    session, memory, lease = env
    abandon_session(session, memory, approved_by="operator")
    assert session.status == "abandoned"
    with pytest.raises((LeaseUnavailable, SessionLifecycleError)):
        reacquire_lease(session, memory)


def test_detach_releases_and_the_same_session_may_return(env):
    session, memory, lease = env
    detach_session(session, memory, approved_by="operator")
    assert session.status == "detached"
    assert reacquire_lease(session, memory).mode is LeaseMode.active_process
    assert session.status == "active"


def test_detach_during_an_in_flight_turn_is_refused(env):
    session, memory, lease = env
    session.status = "paused_relay"
    session.pending_relay_request_id = "relay-1"
    with pytest.raises(SessionLifecycleError) as excinfo:
        detach_session(session, memory, approved_by="operator")
    assert "relay-1" in str(excinfo.value)
    assert session.status == "paused_relay"


def test_takeover_abandons_a_reservation(env):
    session, memory, lease = env
    lease.pause(PROJECT, session.session_id)
    session.status = "paused_confirmation"
    takeover_lease(session, memory, approved_by="operator")
    assert session.status == "abandoned"
    assert lease.state(PROJECT).mode is LeaseMode.abandoned


def test_lifecycle_verbs_are_absent_from_model_vocabulary():
    """A turn must not be able to name these. They are operator-only (FR-019)."""
    from tests.fixtures.pack.fixture_pack import FIXTURE_PACK

    verbs = {
        "complete_session", "abandon_session", "detach_session",
        "reacquire_lease", "takeover_lease", "rebind_scope",
    }
    assert not (verbs & set(KERNEL_GENERIC_ACTIONS))
    assert not (verbs & LOOP_TERMINALS)
    assert not (verbs & set(FIXTURE_PACK.actions))
    assert "rebind_scope" not in CapabilityPack.__dataclass_fields__


def test_operator_verbs_are_recorded_as_control_events(env):
    """Each FR-019 verb lands as an operator-authorized control event."""
    session, memory, lease = env
    complete_session(session, memory, approved_by="operator")
    other = ChatSession(principal=Principal(user_id="u", platform="cli", project_id=PROJECT))
    lease.acquire(PROJECT, other.session_id)
    abandon_session(other, memory, approved_by="operator")
    third = ChatSession(principal=Principal(user_id="u", platform="cli", project_id=PROJECT))
    lease.acquire(PROJECT, third.session_id)
    lease.pause(PROJECT, third.session_id)
    third.status = "paused_confirmation"
    takeover_lease(third, memory, approved_by="operator")

    events = []
    for sess in (session, other, third):
        events.extend(_events(memory, sess))
    names = {e.payload["event"] for e in events}
    assert {"complete_session", "abandon_session", "takeover_lease"} <= names
    assert all(e.source_type.value == "human_input" for e in events)
