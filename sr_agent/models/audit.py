from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class Principal(BaseModel):
    user_id: str
    platform: str   # "cli" | "api" | "webhook"
    project_id: str


class AuditInput(BaseModel):
    # Source — at least one must be provided
    path: Path | None = None
    address: str | None = None

    # Scope
    exclude_paths: list[Path] = Field(default_factory=list)
    focus_files: list[Path] = Field(default_factory=list)
    include_imports: bool = False

    principal: Principal
    resume_session_id: str | None = None

    @model_validator(mode="after")
    def _source_present(self) -> AuditInput:
        if self.path is None and self.address is None:
            raise ValueError("At least one of 'path' or 'address' must be provided")
        return self


class Stage1Report(BaseModel):
    priority_targets: list[str]         # ordered list of "file:function" strings
    skipped_targets: list[str]
    notes: str = ""


class Checkpoint(BaseModel):
    stage: int
    completed_at: datetime = Field(default_factory=datetime.utcnow)
    files_analyzed: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    high_priority_locations: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    # source_type for the MemoryRecord wrapping this checkpoint
    source_type: str = "orchestrator"


class AuditSession(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    principal: Principal
    audit_input: AuditInput
    started_at: datetime = Field(default_factory=datetime.utcnow)

    # Stage progress
    current_stage: int = 1
    stage1_report: Stage1Report | None = None
    stage2_completed: list[str] = Field(default_factory=list)
    stage2_remaining: list[str] = Field(default_factory=list)
    stage3_completed: list[str] = Field(default_factory=list)

    # Accumulated findings across all stages
    finding_ids: list[str] = Field(default_factory=list)

    # Resource tracking
    token_budget_used: int = 0
    iterations: int = 0
