from __future__ import annotations

import logging
from datetime import datetime

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.audit import AuditSession, Checkpoint
from sr_agent.models.memory import MemoryRecord, SourceType

logger = logging.getLogger(__name__)


def save_checkpoint(
    session: AuditSession,
    stage: int,
    memory: EpisodicMemory,
    files_analyzed: list[str] | None = None,
) -> MemoryRecord:
    """Persist a stage checkpoint as an orchestrator-authored MemoryRecord.

    Checkpoints are always source_type=tool_output with tool=orchestrator.
    They cannot be superseded by LLM inference — only human_input can do that.
    """
    checkpoint = Checkpoint(
        stage=stage,
        completed_at=datetime.utcnow(),
        files_analyzed=files_analyzed or [],
        finding_ids=list(session.finding_ids),
    )

    record = MemoryRecord(
        project_id=session.principal.project_id,
        target=f"session:{session.session_id}",
        source_type=SourceType.tool_output,
        tool="orchestrator",
        session_id=session.session_id,
        checkpoint=checkpoint.model_dump(),
    )

    saved = memory.write(record, principal=session.principal)
    logger.info("Checkpoint saved for stage %d, session %s", stage, session.session_id)
    return saved


def load_checkpoint(
    session_id: str,
    project_id: str,
    memory: EpisodicMemory,
) -> Checkpoint | None:
    """Load the most recent checkpoint for a session, if it exists."""
    records = memory.load(project_id, f"session:{session_id}")
    checkpoints = [r for r in records if r.checkpoint is not None]
    if not checkpoints:
        return None

    latest = max(checkpoints, key=lambda r: r.timestamp)
    return Checkpoint.model_validate(latest.checkpoint)
