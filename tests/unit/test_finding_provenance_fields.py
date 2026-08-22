"""The provenance field shape (kernel/005, Phase 2).

Four optional fields on `MemoryRecord` describing what happened to the action the
model proposed in the same `AgentAction` as the finding. They are the kernel's
report of an observation, never a claim that the finding was *derived* from that
action — see `contracts/finding-provenance.md`.

The combination table is enforced at construction rather than trusted at the call
site. A contradiction that reaches disk is a contradiction that gets read back
and believed; refusing it is cheaper than explaining it later.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sr_agent.models.dispatch import DispatchStatus
from sr_agent.models.memory import MemoryRecord, SourceType

FINDING = {"finding_id": "F-1", "location": "Vault.sol:10"}


def _rec(**kw) -> MemoryRecord:
    base = dict(
        project_id="proj1",
        target="Vault.sol",
        source_type=SourceType.external_llm_output,
        session_id="sess-1",
        finding=dict(FINDING),
    )
    base.update(kw)
    return MemoryRecord(**base)


# ── T004: the legal rows from data-model.md ──────────────────────────────────


def test_dispatch_resolved() -> None:
    r = _rec(action_resolution="resolved", action_operation_id="OP-1",
             action_dispatch_status=DispatchStatus.ran)
    assert r.action_resolution == "resolved"
    assert r.action_dispatch_status is DispatchStatus.ran


@pytest.mark.parametrize("status", [
    DispatchStatus.ran, DispatchStatus.error, DispatchStatus.did_not_run,
    DispatchStatus.timeout, DispatchStatus.unavailable,
])
def test_every_terminal_status_is_a_legal_resolution(status) -> None:
    """`resolved` is about the action having an outcome, not about that outcome
    being good. A finding whose analyzer timed out is still a recorded
    hypothesis; it is simply not proof-eligible."""
    assert _rec(action_resolution="resolved", action_operation_id="OP-1",
                action_dispatch_status=status).action_dispatch_status is status


def test_no_action_resolved() -> None:
    r = _rec(action_resolution="unresolved")
    assert r.action_operation_id is None
    assert r.action_dispatch_status is None


def test_paused() -> None:
    r = _rec(action_resolution="pending", action_operation_id="OP-1",
             action_dispatch_status=DispatchStatus.pending)
    assert r.action_resolution == "pending"


def test_resume_resolution_record() -> None:
    r = _rec(action_resolution="resolved", action_operation_id="OP-1",
             action_dispatch_status=DispatchStatus.ran,
             resolves_record_id="rec-a")
    assert r.resolves_record_id == "rec-a"


def test_a_record_written_before_this_feature() -> None:
    r = _rec()
    assert (r.action_resolution, r.action_operation_id,
            r.action_dispatch_status, r.resolves_record_id) == (None, None, None, None)


# ── T004: the illegal ones ───────────────────────────────────────────────────


@pytest.mark.parametrize("kw, why", [
    (dict(action_resolution="resolved"),
     "resolved with no operation id"),
    (dict(action_resolution="resolved", action_operation_id="OP-1"),
     "resolved with no dispatch status"),
    (dict(action_resolution="resolved", action_operation_id="OP-1",
          action_dispatch_status=DispatchStatus.pending),
     "resolved carrying a non-terminal status"),
    (dict(action_resolution="unresolved", action_operation_id="OP-1"),
     "unresolved carrying an operation id"),
    (dict(action_resolution="unresolved", action_dispatch_status=DispatchStatus.ran),
     "unresolved carrying a status"),
    (dict(action_resolution="pending", action_operation_id="OP-1",
          action_dispatch_status=DispatchStatus.ran),
     "pending whose status is not pending"),
    (dict(action_resolution="pending", action_dispatch_status=DispatchStatus.pending),
     "pending with no operation id"),
    (dict(action_resolution="pending", action_operation_id="OP-1",
          action_dispatch_status=DispatchStatus.pending, resolves_record_id="rec-a"),
     "resolves_record_id on a non-resolved record"),
    (dict(action_operation_id="OP-1"),
     "an operation id with no resolution"),
    (dict(resolves_record_id="rec-a"),
     "a back-reference with no resolution"),
], ids=lambda v: v if isinstance(v, str) else "")
def test_illegal_combination_is_refused(kw: dict, why: str) -> None:
    with pytest.raises(ValidationError):
        _rec(**kw)


def test_provenance_on_a_non_finding_record_is_refused() -> None:
    """These fields describe the turn that produced a finding. On a chat turn or
    a dispatch commit they would be a claim about a record they do not belong to."""
    with pytest.raises(ValidationError):
        MemoryRecord(
            project_id="proj1", target="chat:sess-1", source_type=SourceType.tool_output,
            session_id="sess-1", payload_kind="chat_turn", payload={"text": "hi"},
            action_resolution="resolved", action_operation_id="OP-1",
            action_dispatch_status=DispatchStatus.ran,
        )


# ── T008: the fields never reach model context (FR-010) ──────────────────────


def test_for_llm_context_strips_the_provenance_fields() -> None:
    """Not signature material — and neither is `log_sequence`, which is stripped
    because a turn that can see its own bookkeeping can reason, and then argue,
    about it. These fields are the stronger case: they name exactly the state
    that makes a finding proof-eligible downstream, so a model that could see
    them could optimise for producing it.

    The pack still gets them, through `SnapshotItem` (FR-016). That is a
    projection for the pack, not model context.
    """
    r = _rec(action_resolution="resolved", action_operation_id="OP-1",
             action_dispatch_status=DispatchStatus.ran, resolves_record_id="rec-a")
    ctx = r.for_llm_context()
    for field in ("action_resolution", "action_operation_id",
                  "action_dispatch_status", "resolves_record_id"):
        assert field not in ctx
    # The existing strip set is untouched, and the finding itself still reaches
    # context — an assertion that only checked absence would pass against a
    # method that returned {}.
    for field in ("hmac", "seq", "chain_prev", "log_sequence"):
        assert field not in ctx
    assert ctx["finding"] == FINDING
