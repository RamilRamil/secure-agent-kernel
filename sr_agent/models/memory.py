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

# Status values that only human_input is allowed to set.
# Enforced in EpisodicMemory.write(), not here — models don't enforce policy.
REQUIRES_HUMAN_CONFIRMATION: frozenset[str] = frozenset({
    "verified_safe",
    "skip_analysis",
    "audit_complete",
})


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
        """Serialize for inclusion in LLM context — hmac stripped.

        The signature is an orchestrator-only integrity artifact; the model
        must never see it (avoids both leakage and any tamper-oracle signal).
        """
        return self.model_dump(exclude={"hmac"})
