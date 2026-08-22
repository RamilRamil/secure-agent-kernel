"""Idempotent ingestion of an external response (feature 003, FR-002c / D25 / D30).

When a turn pauses for a relay or an operator decision, the answer arrives as a
file. Reading that file again on resume is what makes the decision mutable: an
operator (or anyone who can write the file) can turn a `deny` into an `approve`
between the pause and the resume, and nothing downstream can tell.

So the first read wins and becomes durable, and equality is decided only on
digests the kernel computed from bytes it holds. A caller-supplied digest is not
part of the signature at all -- an API that accepts one has already lost, because
the check then only proves the caller is self-consistent.
"""
import inspect

import pytest

from sr_agent.memory.episodic import EpisodicMemory, ExternalResponseConflict
from sr_agent.memory.canonical import canonical_digest

SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"


@pytest.fixture
def memory(tmp_path):
    return EpisodicMemory(memory_root=tmp_path, secret_key=SECRET)


def ingest(memory, body, *, operation_id="op-1", correlation_id="c-1"):
    return memory.put_external_response_if_absent(
        project_id=PROJECT,
        target="Vault.sol",
        session_id="sess-1",
        operation_id=operation_id,
        correlation_id=correlation_id,
        body=body,
    )


def lines(tmp_path):
    return (tmp_path / PROJECT / "Vault.sol.jsonl").read_text(encoding="utf-8").splitlines()


# ── Idempotency ─────────────────────────────────────────────────────────────


def test_first_ingest_stores_the_body_and_its_own_digest(memory):
    record = ingest(memory, {"decision": "deny"})
    assert record.payload_kind == "external_response"
    assert record.payload["body"] == {"decision": "deny"}
    assert record.payload["body_digest"] == canonical_digest({"decision": "deny"})


def test_equal_body_reuses_the_record_with_no_second_append(memory, tmp_path):
    first = ingest(memory, {"decision": "deny"})
    again = ingest(memory, {"decision": "deny"})
    assert again.record_id == first.record_id
    assert len(lines(tmp_path)) == 1


def test_key_is_the_operation_and_correlation_pair(memory, tmp_path):
    ingest(memory, {"decision": "deny"})
    ingest(memory, {"decision": "deny"}, correlation_id="c-2")
    assert len(lines(tmp_path)) == 2


# ── Conflict ────────────────────────────────────────────────────────────────


def test_different_body_fails_closed(memory, tmp_path):
    ingest(memory, {"decision": "deny"})
    with pytest.raises(ExternalResponseConflict):
        ingest(memory, {"decision": "approve"})
    assert len(lines(tmp_path)) == 1
    assert ingest(memory, {"decision": "deny"}).payload["body"] == {"decision": "deny"}


def test_the_stored_decision_stands_after_the_source_is_mutated(memory, tmp_path):
    """Ingest, kill, edit the file, resume: the conflict is reported, deny stands.

    This is the whole point of the durable record -- resume reads it, never the
    source artifact.
    """
    ingest(memory, {"decision": "deny"})

    fresh = EpisodicMemory(memory_root=tmp_path, secret_key=SECRET)
    with pytest.raises(ExternalResponseConflict):
        fresh.put_external_response_if_absent(
            project_id=PROJECT,
            target="Vault.sol",
            session_id="sess-1",
            operation_id="op-1",
            correlation_id="c-1",
            body={"decision": "approve"},
        )
    stored = fresh.find_external_response(
        project_id=PROJECT, session_id="sess-1", operation_id="op-1", correlation_id="c-1"
    )
    assert stored.payload["body"] == {"decision": "deny"}


# ── The digest is never the caller's to assert ──────────────────────────────


def test_signature_accepts_no_caller_supplied_digest(memory):
    """Structural, not behavioural: there is no parameter to abuse (D30)."""
    parameters = inspect.signature(memory.put_external_response_if_absent).parameters
    assert not [p for p in parameters if "digest" in p or "hash" in p]


def test_every_stored_digest_matches_its_own_stored_body(memory):
    ingest(memory, {"decision": "deny"})
    ingest(memory, {"decision": "approve"}, correlation_id="c-2")
    for record in memory.load(PROJECT, "Vault.sol"):
        assert record.payload["body_digest"] == canonical_digest(record.payload["body"])
