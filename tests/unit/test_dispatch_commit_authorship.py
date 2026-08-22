"""Who authors a durable record (feature 003, FR-006 / FR-009, SC-001).

The separation this feature buys: a pack computes, the kernel persists. Two
things have to hold for that to be worth anything -- an executed transition
really does produce exactly one kernel-authored record, and the pack has no way
to produce one itself. The second is asserted by inspecting the surface rather
than by convention, because a memory handle added to `PackContext` in good faith
would silently reopen the forgery path.
"""
import dataclasses

import pytest

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.dispatch import DispatchPayload
from sr_agent.orchestrator.pack import PackContext

SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"


@pytest.fixture
def memory(tmp_path):
    return EpisodicMemory(memory_root=tmp_path, secret_key=SECRET)


def test_one_bundle_per_executed_transition(memory):
    record = memory.commit_if_absent(
        project_id=PROJECT,
        target="Vault.sol",
        session_id="sess-1",
        tool="run_slither",
        operation_id="op-1",
        transition_key="tk-1",
        expected_revision=0,
        payloads=[
            DispatchPayload(body={"n": 1}),
            DispatchPayload(body={"n": 2}),
        ],
    )
    commits = [
        r for r in memory.load(PROJECT, "Vault.sol") if r.payload_kind == "dispatch_commit"
    ]
    assert len(commits) == 1
    # Two payloads, one record: the bundle is the unit of commit, so a crash can
    # never leave half a transition durable.
    assert len(record.payload["payloads"]) == 2


def test_pack_context_exposes_no_memory_surface():
    names = {f.name for f in dataclasses.fields(PackContext)}
    assert not [n for n in names if "memory" in n or "write" in n or "persist" in n]


def test_pack_context_fields_are_the_declared_narrow_set():
    """A whitelist, not a blacklist: any new capability must be argued for here."""
    assert {f.name for f in dataclasses.fields(PackContext)} <= {
        "scope_root", "sandbox", "wrap_data", "operation_id", "transition_key",
        "scope_policy",
    }
