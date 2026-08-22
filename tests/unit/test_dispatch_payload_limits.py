"""Payload shape and size limits (feature 003, FR-003 / FR-008 / SC-009).

Two separate guarantees live here and they are easy to conflate:

* The kernel stays pack-agnostic. `dispatch_payload` is the ONLY persistable kind.
  A pack discriminates inside its own opaque `body`; the kernel never learns an
  audit word. An allowlist of domain kinds in kernel code would be the boundary
  violation that Principle III exists to prevent.
* Oversize refuses the WHOLE transition, not the offending item. Dropping one
  payload and committing the rest would produce a bundle that looks complete and
  is not -- the pack has no way to discover the silent loss.
"""
from pathlib import Path

import pytest

from sr_agent.memory.episodic import EpisodicMemory, MemoryWriteError
from sr_agent.models.dispatch import MAX_PAYLOAD_BODY_BYTES, MAX_PAYLOADS, DispatchPayload

SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"
KERNEL_SOURCE = Path(__file__).resolve().parents[2] / "sr_agent"


@pytest.fixture
def memory(tmp_path):
    return EpisodicMemory(memory_root=tmp_path, secret_key=SECRET)


def commit(memory, payloads, expected_revision=0):
    return memory.commit_if_absent(
        project_id=PROJECT,
        target="Vault.sol",
        session_id="sess-1",
        tool="run_slither",
        operation_id="op-1",
        transition_key="tk-1",
        expected_revision=expected_revision,
        payloads=payloads,
    )


def test_limits_are_kernel_constants():
    assert (MAX_PAYLOAD_BODY_BYTES, MAX_PAYLOADS) == (8192, 32)


def test_oversize_body_refuses_the_whole_transition(memory):
    payloads = [
        DispatchPayload(body={"note": "small"}),
        DispatchPayload(body={"note": "x" * (MAX_PAYLOAD_BODY_BYTES + 1)}),
    ]
    with pytest.raises(MemoryWriteError):
        commit(memory, payloads)
    assert memory.load(PROJECT, "Vault.sol") == []


def test_too_many_payloads_refuses_the_whole_transition(memory):
    payloads = [DispatchPayload(body={"n": i}) for i in range(MAX_PAYLOADS + 1)]
    with pytest.raises(MemoryWriteError):
        commit(memory, payloads)
    assert memory.load(PROJECT, "Vault.sol") == []


def test_at_the_limit_is_accepted(memory):
    payloads = [DispatchPayload(body={"n": i}) for i in range(MAX_PAYLOADS)]
    assert commit(memory, payloads).payload_kind == "dispatch_commit"


def test_body_must_be_json_serializable():
    with pytest.raises(Exception):
        DispatchPayload(body={"when": object()})


def test_kernel_allowlists_no_audit_domain_kind():
    """No audit vocabulary anywhere in kernel source (Principle III)."""
    forbidden = ("stage_event", "execution_evidence", "tool_result")
    offenders = [
        f"{path.relative_to(KERNEL_SOURCE)}:{word}"
        for path in KERNEL_SOURCE.rglob("*.py")
        for word in forbidden
        if word in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
