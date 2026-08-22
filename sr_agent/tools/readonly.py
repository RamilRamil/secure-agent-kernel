"""Kernel-generic read-only tools (Constitution III / decision D6).

Pure stdlib, no network, no LLM. Task-agnostic, scope-bounded reads the kernel
provides to every capability pack: `read_file` and `search_code`. Path
containment is re-checked here as defense in depth even though `validate_action`
already gates the action before dispatch.

Feature 003 binds a second boundary on top of `scope_root`: the session's
`ContentScopePolicy` include set (D20). A path inside the root but outside the
set is refused with the same fail-closed shape as a traversal escape. Passing
no policy keeps the root-only guard, which is what pre-policy call sites and
tests exercise; production dispatch supplies the bound policy.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 1_000_000  # 1 MB guard
DEFAULT_MAX_HITS = 200

if TYPE_CHECKING:
    from sr_agent.orchestrator.scope import ContentScopePolicy


class ReadOnlyToolError(Exception):
    pass


def _contained(raw: str | Path, scope_root: Path) -> Path:
    """Resolve a path and ensure it stays within scope_root (path-traversal guard)."""
    resolved = Path(raw).resolve()
    root = Path(scope_root).resolve()
    if not resolved.is_relative_to(root):
        raise ReadOnlyToolError(f"Path {str(raw)!r} escapes scope root")
    return resolved


def _enforce_policy(resolved: Path, policy: "ContentScopePolicy") -> None:
    from sr_agent.orchestrator.scope import path_is_included

    policy_root = Path(policy.scope_root).resolve()
    if not resolved.is_relative_to(policy_root):
        raise ReadOnlyToolError(f"Path {str(resolved)!r} escapes scope root")
    for runtime in policy.runtime_state_roots:
        runtime_root = Path(runtime).resolve()
        if resolved == runtime_root or resolved.is_relative_to(runtime_root):
            raise ReadOnlyToolError(
                f"Path {str(resolved)!r} is under a runtime state root"
            )
    rel = PurePosixPath(*resolved.relative_to(policy_root).parts).as_posix()
    if not path_is_included(rel, policy.include):
        raise ReadOnlyToolError(
            f"Path {str(resolved)!r} is outside the bound include set"
        )


def read_file(
    path: str | Path,
    scope_root: Path,
    policy: "ContentScopePolicy | None" = None,
) -> str:
    """Return the text of a file inside the scope root (and include set, if bound)."""
    resolved = _contained(path, scope_root)
    if policy is not None:
        _enforce_policy(resolved, policy)
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
    file_ext: str,
    max_hits: int = DEFAULT_MAX_HITS,
    policy: "ContentScopePolicy | None" = None,
) -> list[SearchHit]:
    """Substring search across files under root, filtered by file_ext.

    `file_ext` is REQUIRED (no default): the file-extension filter is a caller
    (pack) decision, never a kernel-baked domain assumption (D6). A missed
    call-site fails loud with a TypeError rather than silently widening or
    narrowing the search scope.

    Substring (not regex) by design: the pattern is attacker-influenceable, so
    we avoid any ReDoS surface. Returns at most max_hits, paths relative to root.

    When a `ContentScopePolicy` is supplied, the walk is the include set -- not
    `rglob` over the passed root -- so a file inside the root but outside the
    bound set cannot appear in the hits.
    """
    root_resolved = Path(root).resolve()
    if not root_resolved.exists():
        raise ReadOnlyToolError(f"Search root does not exist: {str(root)!r}")

    if policy is not None:
        from sr_agent.orchestrator.scope import iter_included_files

        candidates = [
            path
            for path in iter_included_files(policy)
            if path.is_file() and not path.is_symlink() and path.name.endswith(file_ext)
            and path.resolve().is_relative_to(root_resolved)
        ]
    else:
        candidates = [
            path
            for path in sorted(root_resolved.rglob(f"*{file_ext}"))
            if path.is_file()
        ]

    hits: list[SearchHit] = []
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if pattern in line:
                hits.append(
                    SearchHit(
                        file=path.relative_to(root_resolved).as_posix(),
                        line=line_no,
                        text=line.strip()[:200],
                    )
                )
                if len(hits) >= max_hits:
                    return hits
    return hits
