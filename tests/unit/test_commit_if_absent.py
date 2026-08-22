"""Exactly-once commit of a dispatch bundle (feature 003, FR-006 / FR-008, SC-002 / SC-004).

The property being bought: a transition that has been committed cannot be
committed twice, and a transition that has not been committed leaves no trace.
Both matter across a crash, which is why the revision the commit is checked
against is derived from the log itself rather than from a counter kept beside it.
"""
import pytest

from sr_agent.memory.episodic import EpisodicMemory, MemoryWriteError
from sr_agent.models.dispatch import DispatchPayload

SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"


@pytest.fixture
def memory(tmp_path):
    return EpisodicMemory(memory_root=tmp_path, secret_key=SECRET)


def commit(memory, *, operation_id="op-1", transition_key="tk-1", expected_revision=0,
           note="n", session_id="sess-1"):
    return memory.commit_if_absent(
        project_id=PROJECT,
        target="Vault.sol",
        session_id=session_id,
        tool="run_slither",
        operation_id=operation_id,
        transition_key=transition_key,
        expected_revision=expected_revision,
        payloads=[DispatchPayload(body={"note": note})],
    )


def lines(tmp_path):
    path = tmp_path / PROJECT / "Vault.sol.jsonl"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


# ── Exactly once ────────────────────────────────────────────────────────────


def test_first_commit_appends_one_signed_bundle(memory, tmp_path):
    record = commit(memory)
    assert record.payload_kind == "dispatch_commit"
    assert record.source_type.value == "tool_output"
    assert record.hmac and record.log_sequence == 1
    assert len(lines(tmp_path)) == 1


def test_retry_returns_the_stored_bundle_without_appending(memory, tmp_path):
    first = commit(memory)
    again = commit(memory, note="different-but-same-transition")

    assert again.record_id == first.record_id
    assert again.payload["payloads"] == first.payload["payloads"]
    assert len(lines(tmp_path)) == 1


def test_transition_key_alone_identifies_a_commit(memory, tmp_path):
    """Lookup is by either id, so a retry cannot slip through on the other one."""
    commit(memory)
    commit(memory, operation_id="op-other")
    assert len(lines(tmp_path)) == 1


def test_a_committed_transition_is_not_re_dispatched(memory):
    """The pre-dispatch lookup is what makes the guarantee reachable (FR-006 step 3).

    Deciding after the fact is not enough: by then the analyzer has already run
    a second time, and for a non-idempotent effect that is the damage itself.
    """
    commit(memory)
    calls = {"n": 0}

    def dispatch():
        calls["n"] += 1
        return [DispatchPayload(body={"note": "second run"})]

    existing = memory.find_committed_bundle(
        project_id=PROJECT, session_id="sess-1", transition_key="tk-1"
    )
    if existing is None:
        dispatch()

    assert existing is not None and calls["n"] == 0


# ── Optimistic concurrency ──────────────────────────────────────────────────


def test_revision_mismatch_refuses_with_no_append(memory, tmp_path):
    commit(memory)
    with pytest.raises(MemoryWriteError):
        commit(memory, operation_id="op-2", transition_key="tk-2", expected_revision=0)
    assert len(lines(tmp_path)) == 1


def test_revision_counts_only_dispatch_commits(memory, tmp_path):
    """`session_revision` is the dispatch counter, not the append watermark.

    Other durable records advance `log_sequence` but must not move the revision,
    or an unrelated append would invalidate a caller's in-flight expectation.
    """
    commit(memory)
    memory.put_external_response_if_absent(
        project_id=PROJECT,
        target="Vault.sol",
        session_id="sess-1",
        operation_id="op-9",
        correlation_id="c-9",
        body={"decision": "approve"},
    )
    commit(memory, operation_id="op-2", transition_key="tk-2", expected_revision=1)
    assert memory.session_revision(PROJECT, "sess-1") == 2


def test_revision_is_per_session(memory):
    commit(memory)
    commit(memory, session_id="sess-2", operation_id="op-2", transition_key="tk-2",
           expected_revision=0)
    assert memory.session_revision(PROJECT, "sess-1") == 1
    assert memory.session_revision(PROJECT, "sess-2") == 1


# ── Crash ───────────────────────────────────────────────────────────────────


def test_commit_survives_a_restart_and_is_not_duplicated(memory, tmp_path):
    """Kill point: after the line is written and fsynced, before the caller returns."""
    first = commit(memory)

    fresh = EpisodicMemory(memory_root=tmp_path, secret_key=SECRET)
    again = fresh.commit_if_absent(
        project_id=PROJECT,
        target="Vault.sol",
        session_id="sess-1",
        tool="run_slither",
        operation_id="op-1",
        transition_key="tk-1",
        expected_revision=0,
        payloads=[DispatchPayload(body={"note": "retry after restart"})],
    )
    assert again.record_id == first.record_id
    assert len(lines(tmp_path)) == 1


def test_torn_tail_is_recovered_before_the_lookup(memory, tmp_path):
    """A partial line must not hide an existing commit, nor be counted as one."""
    commit(memory)
    with (tmp_path / PROJECT / "Vault.sol.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"record_id":"half","proj')

    again = commit(memory)
    assert len(lines(tmp_path)) == 1
    assert again.log_sequence == 1
