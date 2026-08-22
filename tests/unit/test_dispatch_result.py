"""The structured dispatch contract (feature 003, FR-002 / D19 / SC-009).

`dispatch` used to return a bare `str`. Anything a pack computed therefore had to
be either re-parsed out of prose or written to memory by the pack itself, and the
second option is the one that had to go: a pack that can write memory can forge a
`human_input`-tier record. `DispatchResult` is the replacement -- the pack returns
structure, the kernel decides what becomes durable.
"""
import pytest
from pydantic import ValidationError

from sr_agent.models.dispatch import (
    DispatchPayload,
    DispatchResult,
    DispatchStatus,
    PendingKind,
    PendingWait,
)


def test_every_status_is_representable():
    assert {s.value for s in DispatchStatus} == {
        "ran", "did_not_run", "timeout", "unavailable", "error", "pending",
    }


def test_ran_is_dispatch_completion_not_grounding():
    """`ran` says the dispatch finished, not that an analyzer produced a finding.

    Collapsing the two is how "the tool ran" turns into "the tool found nothing".
    """
    result = DispatchResult(
        status=DispatchStatus.ran,
        body="slither exited 0",
        payloads=[DispatchPayload(body={"kind": "analyzer_execution", "findings": []})],
    )
    assert result.status is DispatchStatus.ran
    assert result.pending is None


# ── Pending ─────────────────────────────────────────────────────────────────


def test_pending_carries_a_wait_and_no_payloads():
    """Nothing may be committed for a transition that has not happened yet."""
    result = DispatchResult(
        status=DispatchStatus.pending,
        body="awaiting operator relay",
        pending=PendingWait(kind=PendingKind.external_response, correlation_id="c-1"),
    )
    assert result.payloads == []


def test_pending_with_payloads_is_rejected():
    with pytest.raises(ValidationError):
        DispatchResult(
            status=DispatchStatus.pending,
            body="awaiting",
            pending=PendingWait(kind=PendingKind.external_response, correlation_id="c-1"),
            payloads=[DispatchPayload(body={"k": "v"})],
        )


def test_pending_status_without_a_wait_is_rejected():
    with pytest.raises(ValidationError):
        DispatchResult(status=DispatchStatus.pending, body="awaiting")


def test_non_pending_status_with_a_wait_is_rejected():
    with pytest.raises(ValidationError):
        DispatchResult(
            status=DispatchStatus.ran,
            body="done",
            pending=PendingWait(kind=PendingKind.external_response, correlation_id="c-1"),
        )


def test_pending_kind_is_a_closed_enum():
    """An unknown wait kind is refused at construction, before any checkpoint.

    A checkpoint written for a wait the kernel cannot interpret is a session that
    resumes into a state nobody knows how to leave.
    """
    assert {k.value for k in PendingKind} == {
        "external_response", "human_confirmation", "local_model_retry",
    }
    with pytest.raises(ValidationError):
        PendingWait(kind="await_oracle", correlation_id="c-1")


def test_correlation_id_is_required_for_a_wait():
    with pytest.raises(ValidationError):
        PendingWait(kind=PendingKind.external_response)
