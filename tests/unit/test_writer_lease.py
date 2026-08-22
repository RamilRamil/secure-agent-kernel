"""Writer lease state machine (feature 003, FR-014 / FR-016 / D16, SC-003).

One project, one writer. The subtlety that makes this more than a lock file: the
CLI releases its OS-level hold when a turn pauses, so "the process is gone" and
"the session is finished with the project" are different facts, and a lease that
conflates them either strands a paused session forever or lets a stranger steal
one that is merely waiting for an operator.

Hence the modes. `active_process` is stealable once its heartbeat dies, because a
crashed process will never come back to say so. `paused_reserved` is NOT stealable
on any timeout, because a paused turn is waiting for a human by design and the
waiting can outlast any window worth choosing -- taking it away is an operator
decision (`takeover_lease`), never a clock's.
"""
import pytest

from sr_agent.memory.episodic import EpisodicMemory, MemoryWriteError
from sr_agent.models.memory import MemoryRecord, SourceType
from sr_agent.orchestrator.lease import (
    HEARTBEAT_WINDOW_SECONDS,
    LeaseMode,
    LeaseNotHeld,
    LeaseUnavailable,
    WriterLease,
)

SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def lease(tmp_path, clock):
    return WriterLease(memory_root=tmp_path, secret_key=SECRET, clock=clock)


# ── Mutual exclusion ────────────────────────────────────────────────────────


def test_a_second_session_cannot_write_while_one_is_active(lease):
    lease.acquire(PROJECT, "sess-1")
    with pytest.raises(LeaseUnavailable):
        lease.acquire(PROJECT, "sess-2")


def test_the_owner_reacquiring_is_not_a_conflict(lease):
    lease.acquire(PROJECT, "sess-1")
    assert lease.acquire(PROJECT, "sess-1").session_id == "sess-1"


def test_a_dead_active_process_is_stealable(lease, clock):
    """A crashed process never says so; only the clock can report it."""
    lease.acquire(PROJECT, "sess-1")
    clock.advance(HEARTBEAT_WINDOW_SECONDS + 1)
    assert lease.effective_mode(PROJECT) is LeaseMode.crashed_active
    assert lease.acquire(PROJECT, "sess-2").session_id == "sess-2"


def test_a_heartbeat_keeps_the_lease_alive(lease, clock):
    lease.acquire(PROJECT, "sess-1")
    clock.advance(HEARTBEAT_WINDOW_SECONDS - 1)
    lease.heartbeat(PROJECT, "sess-1")
    clock.advance(HEARTBEAT_WINDOW_SECONDS - 1)
    assert lease.effective_mode(PROJECT) is LeaseMode.active_process
    with pytest.raises(LeaseUnavailable):
        lease.acquire(PROJECT, "sess-2")


# ── paused_reserved: never stolen by a clock ────────────────────────────────


def test_paused_reserved_never_times_out(lease, clock):
    """The defining property. A paused turn is waiting for a human by design."""
    lease.acquire(PROJECT, "sess-1")
    lease.pause(PROJECT, "sess-1")

    clock.advance(HEARTBEAT_WINDOW_SECONDS * 1000)

    assert lease.effective_mode(PROJECT) is LeaseMode.paused_reserved
    with pytest.raises(LeaseUnavailable):
        lease.acquire(PROJECT, "sess-2")


def test_the_paused_owner_reacquires_and_continues(lease, clock):
    lease.acquire(PROJECT, "sess-1")
    lease.pause(PROJECT, "sess-1")
    clock.advance(HEARTBEAT_WINDOW_SECONDS * 10)

    assert lease.reacquire(PROJECT, "sess-1").mode is LeaseMode.active_process


def test_takeover_is_an_operator_act_not_a_timeout(lease):
    """Releasing a reservation requires a human, and leaves an explicit trace."""
    lease.acquire(PROJECT, "sess-1")
    lease.pause(PROJECT, "sess-1")

    lease.takeover(PROJECT, approved_by="operator")

    assert lease.state(PROJECT).mode is LeaseMode.abandoned
    assert lease.acquire(PROJECT, "sess-2").session_id == "sess-2"


# ── detached / completed / abandoned ────────────────────────────────────────


def test_detach_releases_the_lease_and_keeps_the_session_resumable(lease):
    """REPL EOF detaches; it does not complete. The two are not the same event."""
    lease.acquire(PROJECT, "sess-1")
    lease.detach(PROJECT, "sess-1")

    assert lease.acquire(PROJECT, "sess-2").session_id == "sess-2"
    lease.detach(PROJECT, "sess-2")
    assert lease.reacquire(PROJECT, "sess-1").mode is LeaseMode.active_process


@pytest.mark.parametrize("finish", ["complete", "abandon"])
def test_a_finished_session_cannot_write_again(lease, finish):
    """History stays readable; the writer role does not come back."""
    lease.acquire(PROJECT, "sess-1")
    getattr(lease, finish)(PROJECT, "sess-1")

    with pytest.raises(LeaseUnavailable):
        lease.reacquire(PROJECT, "sess-1")
    with pytest.raises(LeaseUnavailable):
        lease.acquire(PROJECT, "sess-1")
    assert lease.acquire(PROJECT, "sess-2").session_id == "sess-2"


@pytest.mark.xfail(
    reason="The lease has one slot, so it forgets that sess-1 completed once "
           "another session takes the project. The durable answer belongs to "
           "SessionStatus (T040), not to the lease.",
    strict=True,
)
def test_a_finished_session_stays_finished_after_someone_else_uses_the_project(lease):
    lease.acquire(PROJECT, "sess-1")
    lease.complete(PROJECT, "sess-1")
    lease.acquire(PROJECT, "sess-2")
    lease.detach(PROJECT, "sess-2")

    with pytest.raises(LeaseUnavailable):
        lease.acquire(PROJECT, "sess-1")


def test_a_non_owner_cannot_change_the_lease(lease):
    lease.acquire(PROJECT, "sess-1")
    for action in ("pause", "detach", "complete", "abandon", "heartbeat"):
        with pytest.raises(LeaseNotHeld):
            getattr(lease, action)(PROJECT, "sess-2")


def test_a_forged_lease_file_is_not_believed(lease, tmp_path):
    """The lease is signed, so it cannot be rewritten into someone else's name."""
    lease.acquire(PROJECT, "sess-1")
    path = tmp_path / PROJECT / "_writer_lease.json"
    path.write_text('{"session_id":"sess-evil","mode":"active_process"}', encoding="utf-8")

    assert lease.state(PROJECT) is None
    assert lease.acquire(PROJECT, "sess-2").session_id == "sess-2"


def test_the_lease_file_is_not_mistaken_for_a_target(lease, tmp_path):
    lease.acquire(PROJECT, "sess-1")
    assert not list((tmp_path / PROJECT).glob("*.jsonl"))


# ── Coverage: every durable append takes the lease (D16) ────────────────────


def _record(session_id="sess-1"):
    return MemoryRecord(
        project_id=PROJECT,
        target="Vault.sol",
        source_type=SourceType.tool_output,
        tool="run_slither",
        session_id=session_id,
        payload={"note": "n"},
        payload_kind="dispatch_payload",
    )


@pytest.fixture
def leased_memory(tmp_path, lease):
    return EpisodicMemory(memory_root=tmp_path, secret_key=SECRET, lease=lease)


def test_the_owner_may_append(leased_memory, lease):
    lease.acquire(PROJECT, "sess-1")
    assert leased_memory.write(_record()).hmac is not None


def test_a_non_owner_append_is_refused(leased_memory, lease):
    lease.acquire(PROJECT, "sess-1")
    with pytest.raises(MemoryWriteError):
        leased_memory.write(_record(session_id="sess-2"))


def test_an_append_without_any_lease_is_refused(leased_memory):
    with pytest.raises(MemoryWriteError):
        leased_memory.write(_record())


def test_a_paused_owner_may_still_write_its_checkpoint(leased_memory, lease):
    """`pause_checkpoint` is written as the pause happens, so the mode it lands in
    must not lock out the very record that establishes it."""
    lease.acquire(PROJECT, "sess-1")
    lease.pause(PROJECT, "sess-1")
    assert leased_memory.write(_record()).hmac is not None


def test_a_detached_session_may_not_append(leased_memory, lease):
    lease.acquire(PROJECT, "sess-1")
    lease.detach(PROJECT, "sess-1")
    with pytest.raises(MemoryWriteError):
        leased_memory.write(_record())


def test_commit_if_absent_is_covered_too(leased_memory, lease):
    from sr_agent.models.dispatch import DispatchPayload

    lease.acquire(PROJECT, "sess-1")
    with pytest.raises(MemoryWriteError):
        leased_memory.commit_if_absent(
            project_id=PROJECT,
            target="Vault.sol",
            session_id="sess-2",
            tool="run_slither",
            operation_id="op-1",
            transition_key="tk-1",
            expected_revision=0,
            payloads=[DispatchPayload(body={"n": 1})],
        )


def test_unleased_memory_still_works_for_read_only_callers(tmp_path, lease):
    """Enforcement is bound at construction, like `privileged_statuses` (D5).

    A memory built without a lease is a reader; it is the composition root's job
    to bind the lease for anything that writes.
    """
    lease.acquire(PROJECT, "sess-1")
    writer = EpisodicMemory(memory_root=tmp_path, secret_key=SECRET, lease=lease)
    writer.write(_record())

    reader = EpisodicMemory(memory_root=tmp_path, secret_key=SECRET)
    assert len(reader.load(PROJECT, "Vault.sol")) == 1
