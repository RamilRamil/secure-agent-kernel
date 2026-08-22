from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from sr_agent.models.dispatch import DispatchStatus

if TYPE_CHECKING:
    pass


class SourceType(str, Enum):
    human_input = "human_input"
    tool_output = "tool_output"
    external_llm_output = "external_llm_output"
    human_relayed_tool = "human_relayed_tool"
    llm_inference = "llm_inference"


# Numeric trust level per source type — used by guardrails for severity gating.
# Higher = more trusted. These are constants, not runtime state.
TRUST_LEVELS: dict[SourceType, int] = {
    SourceType.human_input: 4,
    SourceType.tool_output: 3,
    SourceType.external_llm_output: 2,
    SourceType.human_relayed_tool: 2,
    SourceType.llm_inference: 1,
}

# Kernel default: EMPTY. The effective privileged-status set is composed by the
# kernel from the active pack's `privileged_statuses` and bound into
# EpisodicMemory at session construction (feature 001, decision D5). The kernel
# hardcodes no domain status; membership is entirely pack-supplied and enforced
# in EpisodicMemory.write() against the bound set — not here (models don't
# enforce policy). Kept as an empty kernel default for any external reference.
REQUIRES_HUMAN_CONFIRMATION: frozenset[str] = frozenset()


class StatusChange(BaseModel):
    finding_id: str
    old_status: str
    new_status: str
    reason: str


class MemoryRecord(BaseModel):
    # Identity
    record_id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str
    target: str  # "Vault.sol" or "Vault.sol:withdraw"

    # Provenance — orchestrator sets these, LLM never overrides
    source_type: SourceType
    tool: str | None = None      # populated when source_type == tool_output
    session_id: str

    # Timing
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Content — exactly one of these should be set per record
    finding: dict | None = None          # serialised Finding (avoids circular import)
    checkpoint: dict | None = None       # serialised Checkpoint
    status_change: StatusChange | None = None

    # Generic, capability-pack-extensible content. The kernel stays pack-agnostic:
    # a pack (or chat mode) persists its own record kinds here rather than adding a
    # named field per type. `payload_kind` discriminates (e.g. "chat_turn",
    # "poc_status"). Signed like every other field (fields_for_hmac includes it).
    # NOTE: adding these fields changes the signed shape — records written by an
    # older schema will fail verification. Acceptable here (memory is ephemeral);
    # a real migration would re-sign on read.
    payload: dict | None = None
    payload_kind: str | None = None

    # Append-only correction chain
    supersedes: str | None = None        # record_id of the record this overrides

    # Composition integrity (see EpisodicMemory._read_authenticated).
    # `seq` is this record's 0-based position in its target file; `chain_prev`
    # is the hmac of the record written immediately before it (None for the
    # first). Both are inside fields_for_hmac, so a record cannot be moved,
    # renumbered, or re-parented without the orchestrator key. Per-record HMAC
    # authenticates a record's CONTENT; these two authenticate the file's
    # COMPOSITION, which is what makes a removed record detectable rather than
    # indistinguishable from a record that never existed.
    # Set by EpisodicMemory.write — never by a caller, never by the model.
    seq: int | None = None
    chain_prev: str | None = None

    # Project-wide append order (feature 003, D37). A SECOND order, not a rename
    # of `seq`: `seq` numbers a record within its own target file and restarts at
    # 0 for every file, so it cannot say which of two records in different files
    # was appended first. A snapshot watermark needs exactly that, because a
    # session's findings and its dispatch commits live under different targets.
    # 1-based, contiguous across the whole project, kernel-set at write time, and
    # inside fields_for_hmac so it cannot be renumbered without the key.
    log_sequence: int | None = None

    # Finding provenance (feature 005). What happened to the action the model
    # proposed in the SAME AgentAction as the finding. Kernel-set from the
    # executor's result; no pack and no model params reach them.
    #
    # This is co-occurrence within one model turn, NOT derivation: the model may
    # attach a finding to an unrelated action. There is deliberately no field
    # asserting the finding is GROUNDED in the tool's output, under that or any
    # other name -- `DispatchStatus`'s own docstring already names collapsing
    # "the dispatch ran" into "the analyzer produced grounded output" as an
    # error, and a kernel flag saying the stronger thing would manufacture an
    # evidence tier nothing verified. Grounding is the consumer's policy over
    # these facts. Do not "complete" this set with one.
    #
    # All four are outside fields_for_hmac WHEN UNSET (see fields_for_hmac):
    # signing them unconditionally would change the signed shape of every record
    # already on disk and blank the whole store.
    action_resolution: str | None = None          # "resolved" | "unresolved" | "pending"
    action_operation_id: str | None = None
    action_dispatch_status: DispatchStatus | None = None
    # On the record written when a paused turn resumes: the record_id of the
    # paused record it reports the outcome for. NOT `supersedes` -- that requires
    # human_input and would delete the paused record from every load, taking with
    # it the id a consumer captured before the out-of-band confirmation.
    resolves_record_id: str | None = None

    # Integrity — orchestrator signs at write time, verifies at load time.
    # Must be persisted to disk, so NO exclude=True here (that would strip the
    # signature from model_dump_json() and every record would load as unsigned).
    # When a record is surfaced to the LLM, strip hmac explicitly via
    # for_llm_context() — never let the signature into model context.
    hmac: str | None = Field(default=None)

    #: The statuses that mean the dispatch is over, one way or another. `pending`
    #: is the only one that is not an outcome, which is why it gets its own
    #: `action_resolution` value rather than being folded in here.
    _TERMINAL_STATUSES = frozenset(
        s for s in DispatchStatus if s is not DispatchStatus.pending
    )

    @model_validator(mode="after")
    def _content_present(self) -> MemoryRecord:
        if not any([self.finding, self.checkpoint, self.status_change, self.payload]):
            raise ValueError("MemoryRecord must have at least one content field set")
        return self

    @model_validator(mode="after")
    def _provenance_is_coherent(self) -> MemoryRecord:
        """Refuse a contradictory stamp at construction (005 FR-012).

        A contradiction that reaches disk is a contradiction that gets read back
        and believed, and on an append-only store it cannot be corrected in
        place. The combinations here are the table in `data-model.md`; anything
        outside it is a bug in the kernel, not a case to interpret later.
        """
        res = self.action_resolution
        op = self.action_operation_id
        status = self.action_dispatch_status
        back = self.resolves_record_id

        if res is None:
            if op is not None or status is not None or back is not None:
                raise ValueError(
                    "action_operation_id / action_dispatch_status / "
                    "resolves_record_id require action_resolution"
                )
            return self

        if res not in {"resolved", "unresolved", "pending"}:
            raise ValueError(f"action_resolution must be resolved/unresolved/pending, got {res!r}")

        # These fields describe the turn that produced a FINDING. On a chat turn
        # or a dispatch commit they would be a claim about a record they do not
        # belong to.
        if self.finding is None:
            raise ValueError("finding provenance is only valid on a record carrying a finding")

        if res == "resolved":
            if op is None or status is None:
                raise ValueError("action_resolution='resolved' requires an operation id and a status")
            if status not in self._TERMINAL_STATUSES:
                raise ValueError(f"action_resolution='resolved' requires a terminal status, got {status}")
        elif res == "unresolved":
            if op is not None or status is not None:
                raise ValueError(
                    "action_resolution='unresolved' means no action ran: it carries "
                    "neither an operation id nor a status"
                )
            if back is not None:
                raise ValueError("resolves_record_id is only valid on a resolved record")
        else:  # pending
            if op is None or status is not DispatchStatus.pending:
                raise ValueError(
                    "action_resolution='pending' requires the operation id it paused "
                    "with and action_dispatch_status='pending'"
                )
            if back is not None:
                raise ValueError("resolves_record_id is only valid on a resolved record")
        return self

    #: Excluded from the signed dict when unset. Signing them unconditionally
    #: would change the signed shape of every record already written: each would
    #: dump with these keys as `None`, fail verification, and be silently
    #: dropped -- the whole store would read as empty, and with it the "written
    #: before 005 = unknown" case the field design depends on.
    _PROVENANCE_FIELDS = (
        "action_resolution", "action_operation_id",
        "action_dispatch_status", "resolves_record_id",
    )

    def fields_for_hmac(self) -> dict:
        """Return the fields that are signed — everything except hmac itself.

        Provenance fields are omitted when unset, so a record that carries none
        of them signs exactly as it did before feature 005.

        The reachable manipulation this permits is one-directional and stated
        rather than assumed: an attacker with file-write access and no key can
        STRIP the fields from a stored line and have it still verify, moving the
        record from `resolved` to unknown -- strictly less than it said. Writing
        `resolved` + `ran` ONTO a record requires signing the enlarged dict, and
        is not available. See tests/security/test_finding_provenance_integrity.py.
        """
        unset = {f for f in self._PROVENANCE_FIELDS if getattr(self, f) is None}
        return self.model_dump(exclude={"hmac", *unset})

    def for_llm_context(self) -> dict:
        """Serialize for inclusion in LLM context — integrity fields stripped.

        The signature is an orchestrator-only integrity artifact; the model
        must never see it (avoids both leakage and any tamper-oracle signal).
        `chain_prev` is the *previous* record's signature and `seq` its position,
        so they are stripped for the same reason — surfacing them would put
        signature material back into model context through the side door.
        `log_sequence` is stripped as kernel bookkeeping: a turn that could see
        its own position in the log could reason, and then argue, about it.

        The 005 provenance fields go with it, for that reason applied harder:
        they name exactly the state that makes a finding proof-eligible
        downstream (`resolved` + `ran`), so a model that could see which of its
        earlier findings earned that stamp could optimise for producing it.
        Closing an evidence gap by opening a manipulation surface is not a trade
        this makes. The pack reads them off `SnapshotItem`, which is a
        projection for the pack, not model context.
        """
        return self.model_dump(
            exclude={"hmac", "seq", "chain_prev", "log_sequence", *self._PROVENANCE_FIELDS}
        )
