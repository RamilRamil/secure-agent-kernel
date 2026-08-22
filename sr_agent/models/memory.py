from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

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

    # Integrity — orchestrator signs at write time, verifies at load time.
    # Must be persisted to disk, so NO exclude=True here (that would strip the
    # signature from model_dump_json() and every record would load as unsigned).
    # When a record is surfaced to the LLM, strip hmac explicitly via
    # for_llm_context() — never let the signature into model context.
    hmac: str | None = Field(default=None)

    @model_validator(mode="after")
    def _content_present(self) -> MemoryRecord:
        if not any([self.finding, self.checkpoint, self.status_change, self.payload]):
            raise ValueError("MemoryRecord must have at least one content field set")
        return self

    def fields_for_hmac(self) -> dict:
        """Return the fields that are signed — everything except hmac itself."""
        return self.model_dump(exclude={"hmac"})

    def for_llm_context(self) -> dict:
        """Serialize for inclusion in LLM context — integrity fields stripped.

        The signature is an orchestrator-only integrity artifact; the model
        must never see it (avoids both leakage and any tamper-oracle signal).
        `chain_prev` is the *previous* record's signature and `seq` its position,
        so they are stripped for the same reason — surfacing them would put
        signature material back into model context through the side door.
        `log_sequence` is stripped as kernel bookkeeping: a turn that could see
        its own position in the log could reason, and then argue, about it.
        """
        return self.model_dump(exclude={"hmac", "seq", "chain_prev", "log_sequence"})
