"""The structured result a capability pack returns from `dispatch` (feature 003).

`dispatch` returned a bare `str` before this feature, which left a pack two bad
options for anything it computed: hide it in prose for the kernel to re-parse, or
write it to memory itself. The second is the one that had to go -- a pack holding
a memory handle can forge a `human_input`-tier record, and the whole trust model
is computed from that tier.

So the pack returns data and the kernel decides what becomes durable. That split
is only real if the pack cannot describe its own provenance, which is why nothing
here carries `project_id`, `session_id`, `source_type`, `tool`, `operation_id`, or
`record_id`: the kernel stamps all six (FR-004). The absence is the mechanism.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator

# Fixed kernel constants (FR-003). A pack cannot raise them, and oversize refuses
# the whole transition rather than the offending item -- a bundle that looks
# complete and silently is not gives the pack nothing to detect.
MAX_PAYLOAD_BODY_BYTES = 8192
MAX_PAYLOADS = 32


# Snapshot capacity (FR-009b). Fixed here so kernel and pack enforce identical
# values. Whichever is reached first binds. Compaction is out of scope: the limit
# is a stated product boundary with a fail-closed error, not a promise to cope.
MAX_SNAPSHOT_ITEMS = 10000
MAX_SNAPSHOT_BYTES = 33554432   # 32 MiB of canonical encoded bytes

# What a snapshot hands to the pack. `dispatch_payload` is what a pack SENDS;
# these are what it RECEIVES back, and the two sets are deliberately different.
#
# `model_note` (feature 002) is ABSENT ON PURPOSE, not by oversight -- do not
# "complete" this set. A note is `llm_inference`, the lowest tier in the source
# hierarchy, and a snapshot is the input a pack builds its whole domain
# projection from. Admitting a note here would let the model's own prose become
# a premise for what the agent does next: a self-reinforcing loop across turns,
# which is the retrospective-poisoning channel Principle IV closes for steering
# knowledge. The kernel refuses to promote model output across a source-type
# boundary; refusing to feed it into the pack's reducer is the same rule one
# layer out. `pause_checkpoint` is absent for a different reason -- it is kernel
# control state, not domain history.
SNAPSHOT_KINDS = frozenset({"finding", "dispatch_commit", "external_response"})


class DispatchStatus(str, Enum):
    """Outcome of the dispatch call itself.

    `ran` means the dispatch completed, NOT that an analyzer produced grounded
    output. Collapsing the two is how "the tool ran" quietly becomes "the tool
    found nothing" -- grounding is a separate claim the pack makes in its payload.
    """
    ran = "ran"
    did_not_run = "did_not_run"
    timeout = "timeout"
    unavailable = "unavailable"
    error = "error"
    pending = "pending"


class PendingKind(str, Enum):
    """What a paused transition is waiting for.

    Closed on purpose: a checkpoint written for a wait the kernel cannot interpret
    is a session that resumes into a state nobody knows how to leave. An unknown
    kind is refused at construction, before anything durable is written.
    """
    external_response = "external_response"
    human_confirmation = "human_confirmation"
    local_model_retry = "local_model_retry"


class PendingWait(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: PendingKind
    correlation_id: str


class DispatchPayload(BaseModel):
    """One thing the pack wants persisted. `body` is opaque to the kernel.

    A pack discriminates its own record kinds inside `body`; the kernel never
    learns a domain word. `extra="forbid"` is load-bearing rather than tidy: an
    ignored extra field would be a pack's claim about itself that the kernel
    silently accepted and then could not see.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    body: dict

    @model_validator(mode="after")
    def _body_is_encodable(self) -> DispatchPayload:
        """Reject an unencodable body here, not at commit time.

        Failing at the commit would abort a transition whose effect has already
        run, which is the worst moment to discover it: the analyzer executed and
        nothing durable records that it did.
        """
        from sr_agent.memory.canonical import canonical_bytes

        canonical_bytes(self.body)
        return self


class DispatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: DispatchStatus
    body: str                       # operator-facing; DATA-wrapped by the kernel
    payloads: list[DispatchPayload] = []
    pending: PendingWait | None = None

    @model_validator(mode="after")
    def _pending_is_consistent(self) -> DispatchResult:
        waiting = self.status is DispatchStatus.pending
        if waiting and self.pending is None:
            raise ValueError("status=pending requires a `pending` wait descriptor")
        if not waiting and self.pending is not None:
            raise ValueError("`pending` is only valid with status=pending")
        if waiting and self.payloads:
            raise ValueError(
                "status=pending must carry no payloads: nothing may be committed "
                "for a transition that has not happened yet"
            )
        return self


class SnapshotItem(BaseModel):
    """One prior record as the pack sees it: kernel envelope plus opaque body.

    The identity fields come off the kernel-authored envelope, never off the
    body. A pack that wrote `operation_id` into its own payload is making a claim
    about itself; the envelope is the kernel's own record of what it committed.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str
    log_sequence: int
    kind: str
    source_type: str
    timestamp: str
    operation_id: str | None = None
    body: dict


class MemorySnapshot(BaseModel):
    """Frozen, fully materialized prior state -- the pack's only read seam.

    An argument rather than a field on `PackContext`, and data rather than a
    callable: anything callable is a capability the pack could exercise at a
    moment the kernel is not supervising, and a lazy view could return different
    contents on a second read of the same snapshot.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    as_of_sequence: int
    measured_bytes: int
    items: tuple[SnapshotItem, ...] = ()


class ActionSnapshot(BaseModel):
    """Durable Action needed to re-dispatch after a pause (FR-010c / D23).

    `transition_key` contains a params digest, which cannot be inverted, so the
    checkpoint stores the validated params themselves. Resume rebuilds the Action
    from this record and re-derives the digest; it MUST NOT ask the model.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_type: str
    params: dict
    pack_id: str
    pack_contract_version: str
    scope_generation: int
    content_identity_digest: str | None = None
    transition_key: str
    operation_id: str
