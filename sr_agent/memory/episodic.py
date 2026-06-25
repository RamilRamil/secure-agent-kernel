from __future__ import annotations

import json
import logging
from pathlib import Path

from sr_agent.memory import hmac as hmac_module
from sr_agent.models.memory import MemoryRecord, REQUIRES_HUMAN_CONFIRMATION, SourceType

logger = logging.getLogger(__name__)


class MemoryWriteError(Exception):
    pass


class EpisodicMemory:
    def __init__(self, memory_root: Path, secret_key: bytes) -> None:
        self._root = memory_root
        self._secret_key = secret_key

    def _path(self, project_id: str, target: str) -> Path:
        safe_target = target.replace("/", "_").replace(":", "__")
        return self._root / project_id / f"{safe_target}.jsonl"

    def write(
        self,
        record: MemoryRecord,
    ) -> MemoryRecord:
        """Validate, sign, and append a record to the episodic store.

        Policy checks happen here — the model layer does not enforce policy.
        """
        self._enforce_status_rules(record)

        # Orchestrator computes the HMAC — LLM never touches this field
        fields = record.fields_for_hmac()
        signature = hmac_module.sign(fields, self._secret_key)
        record = record.model_copy(update={"hmac": signature})

        path = self._path(record.project_id, record.target)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")

        return record

    def load(self, project_id: str, target: str) -> list[MemoryRecord]:
        """Load records, verify each HMAC, apply supersedes chain.

        Records with invalid HMAC are silently dropped — no exception, no log
        at WARNING+ level. This avoids giving an attacker a tamper oracle.
        """
        path = self._path(project_id, target)
        if not path.exists():
            return []

        valid: dict[str, MemoryRecord] = {}  # record_id → record

        with path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    record = MemoryRecord.model_validate(data)
                except Exception:
                    logger.debug("Skipping unparseable record at line %d", line_no)
                    continue

                if record.hmac is None:
                    logger.debug("Dropping unsigned record %s", record.record_id)
                    continue

                fields = record.fields_for_hmac()
                if not hmac_module.verify(fields, record.hmac, self._secret_key):
                    # Silent drop — do not log at WARNING to avoid tamper oracle
                    logger.debug("Dropping record %s: HMAC mismatch", record.record_id)
                    continue

                valid[record.record_id] = record

        return self._apply_supersedes(valid)

    @staticmethod
    def _apply_supersedes(records: dict[str, MemoryRecord]) -> list[MemoryRecord]:
        """Remove records that have been superseded by a newer correction."""
        superseded_ids: set[str] = set()
        for record in records.values():
            if record.supersedes:
                superseded_ids.add(record.supersedes)

        return [r for r in records.values() if r.record_id not in superseded_ids]

    @staticmethod
    def _enforce_status_rules(record: MemoryRecord) -> None:
        """Raise if privileged status is set by an untrusted source type."""
        if record.status_change is None:
            return

        new_status = record.status_change.new_status
        if new_status in REQUIRES_HUMAN_CONFIRMATION:
            if record.source_type != SourceType.human_input:
                raise MemoryWriteError(
                    f"Status '{new_status}' requires source_type=human_input, "
                    f"got {record.source_type.value!r}. "
                    "This is a security gate — only human operators may set this status."
                )

        if record.supersedes and record.source_type != SourceType.human_input:
            raise MemoryWriteError(
                f"'supersedes' field requires source_type=human_input, "
                f"got {record.source_type.value!r}. "
                "Corrections to existing records require human authority."
            )
