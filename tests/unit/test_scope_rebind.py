"""Scope rebind is an operator control, never an in-turn widening (FR-013c / D27).

`scope_generation` is part of `transition_key`, so a rebind cannot resurrect an
effect authorized against the previous scope. That only holds if rebind is
refused while an operation is in flight -- converting it into a pause would
leave the in-flight identity and the new generation both "current", which is
the hole this API exists to close.
"""
import pytest

from sr_agent.memory.canonical import derive_operation_id, derive_transition_key
from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.chat import ChatSession
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.lease import WriterLease
from sr_agent.orchestrator.scope import (
    ContentScopePolicy,
    RebindRefused,
    ScopeGenerationMismatch,
    bind_scope,
    check_resume_scope,
    rebind_scope,
)


SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "tree"
    (root / "contracts").mkdir(parents=True)
    (root / "contracts" / "Vault.sol").write_text("contract Vault {}", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "Lib.sol").write_text("library Lib {}", encoding="utf-8")
    lease = WriterLease(tmp_path / "memory", SECRET)
    memory = EpisodicMemory(tmp_path / "memory", SECRET, lease=lease)
    session = ChatSession(
        principal=Principal(user_id="u", platform="cli", project_id=PROJECT)
    )
    lease.acquire(PROJECT, session.session_id)
    bind_scope(session, ContentScopePolicy(scope_root=root, include=("contracts/**",)))
    return session, memory, lease, root


def _identity(generation: int) -> str:
    return str(
        derive_operation_id(
            derive_transition_key(
                session_id="sess-1",
                action_id="run_slither",
                params={"target": "contracts/Vault.sol"},
                chunk_id="chunk-0",
                expected_revision=0,
                scope_generation=generation,
            )
        )
    )


def test_rebind_at_a_turn_boundary_increments_generation_and_records_an_event(env):
    session, memory, lease, root = env
    assert session.scope_generation == 1

    rebind_scope(
        session,
        ContentScopePolicy(scope_root=root, include=("contracts/**", "src/**")),
        memory=memory,
        approved_by="operator",
    )

    assert session.scope_generation == 2
    events = [
        r for r in memory.load(PROJECT, f"chat:{session.session_id}")
        if r.payload_kind == "control_event"
    ]
    assert len(events) == 1
    assert events[0].source_type.value == "human_input"
    assert events[0].payload["event"] == "rebind_scope"
    assert events[0].payload["previous_scope_generation"] == 1
    assert events[0].payload["new_scope_generation"] == 2


def test_rebind_during_a_pending_operation_is_refused_and_is_not_a_pause(env):
    session, memory, lease, root = env
    session.status = "paused_relay"
    session.pending_relay_request_id = "relay-9"

    with pytest.raises(RebindRefused) as excinfo:
        rebind_scope(
            session,
            ContentScopePolicy(scope_root=root, include=("src/**",)),
            memory=memory,
            approved_by="operator",
        )
    assert "relay-9" in str(excinfo.value)
    assert session.scope_generation == 1
    assert session.status == "paused_relay"
    assert memory.load(PROJECT, f"chat:{session.session_id}") == []


def test_post_rebind_transition_cannot_reuse_pre_rebind_identity():
    assert _identity(1) != _identity(2)


def test_resuming_a_checkpoint_from_the_previous_generation_fails_closed(env):
    session, memory, lease, root = env
    previous = session.scope_generation
    previous_identity = session.content_identity

    rebind_scope(
        session,
        ContentScopePolicy(scope_root=root, include=("contracts/**", "src/**")),
        memory=memory,
        approved_by="operator",
    )

    with pytest.raises(ScopeGenerationMismatch):
        check_resume_scope(
            checkpoint_generation=previous,
            checkpoint_identity=previous_identity,
            session=session,
        )
