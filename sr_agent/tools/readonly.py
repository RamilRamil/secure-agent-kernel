"""Read-only analysis tools (US-domain, T049).

Pure stdlib, no network, no LLM. These are the most-used Stage 1 tools.
Path containment is re-checked here as defense in depth even though
validate_action already gates the action before dispatch.

Slither / Mythril wiring (T050/T051) runs via DockerSandbox and is added
in a later block.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 1_000_000  # 1 MB guard — contract files are small
DEFAULT_MAX_HITS = 200


class ReadOnlyToolError(Exception):
    pass


def _contained(raw: str | Path, audit_root: Path) -> Path:
    """Resolve a path and ensure it stays within audit_root (path-traversal guard)."""
    resolved = Path(raw).resolve()
    root = Path(audit_root).resolve()
    if not resolved.is_relative_to(root):
        raise ReadOnlyToolError(f"Path {str(raw)!r} escapes audit root")
    return resolved


def read_file(path: str | Path, audit_root: Path) -> str:
    """Return the text of a file inside the audit root."""
    resolved = _contained(path, audit_root)
    if not resolved.is_file():
        raise ReadOnlyToolError(f"Not a file: {str(path)!r}")
    if resolved.stat().st_size > MAX_FILE_BYTES:
        raise ReadOnlyToolError(
            f"File too large (> {MAX_FILE_BYTES} bytes): {str(path)!r}"
        )
    return resolved.read_text(encoding="utf-8", errors="replace")


@dataclass
class SearchHit:
    file: str
    line: int
    text: str


def search_code(
    pattern: str,
    root: str | Path,
    file_ext: str = ".sol",
    max_hits: int = DEFAULT_MAX_HITS,
) -> list[SearchHit]:
    """Substring search across files under root.

    Substring (not regex) by design: the pattern is attacker-influenceable, so
    we avoid any ReDoS surface. Returns at most max_hits, paths relative to root.
    """
    root_resolved = Path(root).resolve()
    if not root_resolved.exists():
        raise ReadOnlyToolError(f"Search root does not exist: {str(root)!r}")

    hits: list[SearchHit] = []
    for path in sorted(root_resolved.rglob(f"*{file_ext}")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if pattern in line:
                hits.append(
                    SearchHit(
                        file=str(path.relative_to(root_resolved)),
                        line=line_no,
                        text=line.strip()[:200],
                    )
                )
                if len(hits) >= max_hits:
                    return hits
    return hits
