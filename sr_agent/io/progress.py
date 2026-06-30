"""Human-readable progress reporting (T061).

A small sink the pipeline calls at each milestone. Decoupled from the CLI so it
can be silenced (enabled=False) or redirected to any stream in tests.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import TextIO


class ProgressEvent(str, Enum):
    stage1_start = "stage1_start"
    stage1_done = "stage1_done"
    stage2_emit = "stage2_emit"
    stage2_ingest = "stage2_ingest"
    paused = "paused"
    stage3 = "stage3"
    report = "report"


_LABELS: dict[ProgressEvent, str] = {
    ProgressEvent.stage1_start: "Stage 1: discovering targets",
    ProgressEvent.stage1_done: "Stage 1 complete",
    ProgressEvent.stage2_emit: "Stage 2: analysis requested",
    ProgressEvent.stage2_ingest: "Stage 2: response ingested",
    ProgressEvent.paused: "Paused — awaiting relay responses",
    ProgressEvent.stage3: "Stage 3: synthesis",
    ProgressEvent.report: "Report written",
}


@dataclass
class ProgressStream:
    stream: TextIO = field(default_factory=lambda: sys.stderr)
    enabled: bool = True

    def emit(
        self,
        event: ProgressEvent,
        detail: str = "",
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        if not self.enabled:
            return
        bar = f"[{current}/{total}] " if current is not None and total is not None else ""
        label = _LABELS.get(event, event.value)
        suffix = f" — {detail}" if detail else ""
        self.stream.write(f"  → {bar}{label}{suffix}\n")
        self.stream.flush()


def silent() -> ProgressStream:
    """A no-op progress stream for callers that don't want output."""
    return ProgressStream(enabled=False)
