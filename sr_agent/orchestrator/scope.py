"""Content-scope policy: one set hashed, read, and mounted (feature 003, D20).

The include set is a security boundary, not a hashing optimisation. A file
inside `scope_root` but outside the bound set is refused the same way a
path-traversal escape is refused. Widening it is an operator `rebind_scope`,
never something a turn infers from a newly discovered import.

The composition root supplies `runtime_state_roots` (relay/report directories
and anything else that must not be treated as target code). The kernel does
not hardcode those names, because a hardcoded skip-list would be a domain
assumption and would miss a pack that stores runtime state somewhere else.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Iterable

from sr_agent.models.session import ContentIdentity, IN_FLIGHT_SESSION_STATUSES

if TYPE_CHECKING:
    from sr_agent.memory.episodic import EpisodicMemory
    from sr_agent.models.chat import ChatSession

MAX_SCOPE_FILES = 10000
MAX_SCOPE_BYTES = 100 * 1024 * 1024
MAX_SCOPE_FILE_BYTES = 8 * 1024 * 1024

_SKIP_DIR_NAMES = frozenset({".git", "__pycache__"})


class ScopeError(Exception):
    pass


class ScopeUnboundError(ScopeError):
    """Resume asked for a root that was never recorded. Not Path('.')."""
    pass


class ScopeVerificationError(ScopeError):
    """The live tree does not match the recorded content identity."""
    pass


class ScopeBudgetExceeded(ScopeError):
    """The include set is larger than the kernel will hash or read."""
    pass


class RebindRefused(ScopeError):
    """rebind_scope was asked while an operation is in flight."""
    pass


class ScopeGenerationMismatch(ScopeError):
    """A checkpoint was minted against a previous scope generation."""
    pass


@dataclass(frozen=True)
class ContentScopePolicy:
    scope_root: Path
    include: tuple[str, ...]
    runtime_state_roots: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_root", Path(self.scope_root).resolve())
        object.__setattr__(
            self,
            "runtime_state_roots",
            tuple(Path(p).resolve() for p in self.runtime_state_roots),
        )
        if not self.include:
            raise ScopeError(
                "ContentScopePolicy requires a non-empty include set: an empty "
                "set would hash nothing and refuse every read, which is not a "
                "bind, and a missing set would silently hash the whole tree."
            )

    @classmethod
    def from_session(cls, session: "ChatSession") -> "ContentScopePolicy":
        if not session.scope_root:
            raise ScopeUnboundError(
                "Session has no scope_root; resume is refused rather than "
                "defaulting to '.'."
            )
        return cls(
            scope_root=Path(session.scope_root),
            include=tuple(session.include),
            runtime_state_roots=tuple(Path(p) for p in session.runtime_state_roots),
        )


def restore_scope_root(session: "ChatSession") -> Path:
    """Canonical root recorded at bind. Never invented from cwd."""
    if not session.scope_root:
        raise ScopeUnboundError(
            "Session has no scope_root; resume is refused rather than "
            "defaulting to '.'."
        )
    return Path(session.scope_root)


def _under(path: Path, roots: Iterable[Path]) -> bool:
    resolved = path.resolve() if path.exists() else path
    for root in roots:
        try:
            if resolved == root or resolved.is_relative_to(root):
                return True
        except (OSError, ValueError):
            continue
    return False


def _glob_re(pattern: str) -> re.Pattern[str]:
    """Translate a POSIX glob (including `**`) to a full-match regex."""
    body: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            body.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            body.append(".*")
            i += 2
        elif pattern[i] == "*":
            body.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            body.append("[^/]")
            i += 1
        else:
            body.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(body) + "$")


def path_is_included(relative: str, include: Iterable[str]) -> bool:
    """True when `relative` (POSIX, relative to scope_root) is in the bound set."""
    rel = relative.replace("\\", "/").lstrip("/")
    for raw in include:
        pattern = raw.replace("\\", "/").lstrip("/")
        if not pattern:
            continue
        if any(ch in pattern for ch in "*?"):
            if _glob_re(pattern).match(rel):
                return True
            continue
        prefix = pattern.rstrip("/")
        if rel == prefix or rel.startswith(prefix + "/"):
            return True
    return False


def iter_included_files(policy: ContentScopePolicy) -> list[Path]:
    """Walk the include set. Never follows symlinks; never enters skip dirs."""
    root = policy.scope_root
    if not root.exists():
        raise ScopeVerificationError(f"scope_root does not exist: {str(root)!r}")

    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = [
            name
            for name in dirnames
            if name not in _SKIP_DIR_NAMES
            and not (current / name).is_symlink()
            and not _under(current / name, policy.runtime_state_roots)
        ]
        if _under(current, policy.runtime_state_roots):
            continue
        for name in filenames:
            path = current / name
            rel = PurePosixPath(*path.relative_to(root).parts).as_posix()
            if path_is_included(rel, policy.include):
                files.append(path)
    files.sort(key=lambda p: PurePosixPath(*p.relative_to(root).parts).as_posix())
    return files


def digest_worktree(policy: ContentScopePolicy) -> ContentIdentity:
    """SHA-256 over path + NUL + size + NUL + bytes, sorted by relative POSIX path.

    Symlinks are hashed as path + NUL + 'symlink' + NUL + target, never followed.
    mtime and mode are excluded: they change without the content changing, and
    would make a resume fail for a chmod.
    """
    root = policy.scope_root
    hasher = hashlib.sha256()
    file_count = 0
    total_bytes = 0

    for path in iter_included_files(policy):
        rel = PurePosixPath(*path.relative_to(root).parts).as_posix()
        file_count += 1
        if file_count > MAX_SCOPE_FILES:
            raise ScopeBudgetExceeded(
                f"Include set exceeds the file-count limit: {file_count} files "
                f"against a limit of {MAX_SCOPE_FILES}. Nothing was truncated."
            )
        if path.is_symlink():
            target = os.readlink(path)
            payload = rel.encode("utf-8") + b"\0symlink\0" + target.encode("utf-8")
            total_bytes += len(target.encode("utf-8"))
        else:
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise ScopeVerificationError(
                    f"Cannot stat included file {rel!r}: {exc}"
                ) from exc
            if size > MAX_SCOPE_FILE_BYTES:
                raise ScopeBudgetExceeded(
                    f"Included file {rel!r} is {size} bytes, over the per-file "
                    f"limit of {MAX_SCOPE_FILE_BYTES}."
                )
            total_bytes += size
            if total_bytes > MAX_SCOPE_BYTES:
                raise ScopeBudgetExceeded(
                    f"Include set exceeds the byte limit: {total_bytes} bytes "
                    f"against a limit of {MAX_SCOPE_BYTES}."
                )
            payload = (
                rel.encode("utf-8")
                + b"\0"
                + str(size).encode("ascii")
                + b"\0"
                + path.read_bytes()
            )
        hasher.update(payload)

    remote, head = _git_metadata(root)
    return ContentIdentity(
        digest=hasher.hexdigest(),
        file_count=file_count,
        total_bytes=total_bytes,
        git_remote_url=remote,
        git_head_sha=head,
    )


def _git_metadata(root: Path) -> tuple[str | None, str | None]:
    git = root / ".git"
    if not git.is_dir():
        return None, None
    head = None
    remote = None
    head_file = git / "HEAD"
    if head_file.is_file():
        text = head_file.read_text(encoding="utf-8").strip()
        if text.startswith("ref:"):
            ref = text.split(":", 1)[1].strip()
            ref_file = git / ref
            if ref_file.is_file():
                head = ref_file.read_text(encoding="utf-8").strip()
        elif text:
            head = text
    config = git / "config"
    if config.is_file():
        for line in config.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("url"):
                _, _, value = stripped.partition("=")
                remote = value.strip() or None
                break
    return remote, head


def sandbox_manifest(policy: ContentScopePolicy) -> tuple[str, ...]:
    """Relative POSIX paths the analyzer sandbox may mount -- the include set."""
    root = policy.scope_root
    return tuple(
        PurePosixPath(*path.relative_to(root).parts).as_posix()
        for path in iter_included_files(policy)
        if not path.is_symlink()
    )


def bind_scope(session: "ChatSession", policy: ContentScopePolicy) -> "ChatSession":
    """First bind. Sets generation to 1. Does not write a control event -- there
    is no previous generation to record, and the session snapshot carries the
    binding from this point on.
    """
    if session.scope_generation != 0:
        raise RebindRefused(
            "Session is already bound; use rebind_scope to change the include set."
        )
    identity = digest_worktree(policy)
    session.scope_root = str(policy.scope_root)
    session.include = list(policy.include)
    session.runtime_state_roots = [str(p) for p in policy.runtime_state_roots]
    session.content_identity = identity
    session.scope_generation = 1
    return session


def verify_content_identity(session: "ChatSession") -> ContentIdentity:
    """Recompute the digest and refuse on mismatch or a missing root."""
    root = restore_scope_root(session)
    if not root.exists():
        raise ScopeVerificationError(f"scope_root does not exist: {str(root)!r}")
    live = digest_worktree(ContentScopePolicy.from_session(session))
    recorded = session.content_identity
    if recorded is None or live.digest != recorded.digest:
        raise ScopeVerificationError(
            "Worktree digest does not match the recorded content identity. "
            "Resume is refused; rebind explicitly if the change is intended."
        )
    return live


def in_flight_operation(session: "ChatSession") -> str | None:
    """Name of the pending operation, or None at a completed-turn boundary."""
    if session.pending_relay_request_id:
        return session.pending_relay_request_id
    if session.pending_confirmation_id:
        return session.pending_confirmation_id
    if session.status in IN_FLIGHT_SESSION_STATUSES:
        return session.status
    if session.continuation is not None:
        return session.continuation.last_dispatch_operation_id or session.continuation.turn_id
    return None


def check_resume_scope(
    *,
    checkpoint_generation: int,
    checkpoint_identity: ContentIdentity | None,
    session: "ChatSession",
) -> None:
    """Fail closed if the checkpoint was minted against a different binding."""
    if checkpoint_generation != session.scope_generation:
        raise ScopeGenerationMismatch(
            f"Checkpoint scope_generation={checkpoint_generation} does not match "
            f"the session's current generation {session.scope_generation}. "
            "The Action it carries is not valid against this binding."
        )
    recorded = session.content_identity
    if (
        checkpoint_identity is None
        or recorded is None
        or checkpoint_identity.digest != recorded.digest
    ):
        raise ScopeVerificationError(
            "Checkpoint content identity does not match the current binding."
        )
    verify_content_identity(session)


def rebind_scope(
    session: "ChatSession",
    policy: ContentScopePolicy,
    *,
    memory: "EpisodicMemory",
    approved_by: str,
) -> "ChatSession":
    """Operator-only widening/narrowing of the include set (FR-013c).

    Legal only at a completed-turn boundary. Refused, not converted into a
    pause, while an operation is in flight: a pause would leave the in-flight
    identity and the new generation both current.
    """
    pending = in_flight_operation(session)
    if pending is not None:
        raise RebindRefused(
            f"rebind_scope refused: operation {pending!r} is in flight. "
            "Let it finish or abandon_session; rebind is not converted into a pause."
        )
    if session.scope_generation == 0:
        raise RebindRefused("Session is not bound; call bind_scope first.")

    previous = session.scope_generation
    previous_include = list(session.include)
    previous_identity = (
        session.content_identity.model_dump(mode="json") if session.content_identity else None
    )
    identity = digest_worktree(policy)

    session.scope_root = str(policy.scope_root)
    session.include = list(policy.include)
    session.runtime_state_roots = [str(p) for p in policy.runtime_state_roots]
    session.content_identity = identity
    session.scope_generation = previous + 1

    from sr_agent.models.memory import MemoryRecord, SourceType

    memory.write(
        MemoryRecord(
            project_id=session.principal.project_id,
            target=f"chat:{session.session_id}",
            source_type=SourceType.human_input,
            session_id=session.session_id,
            payload_kind="control_event",
            payload={
                "event": "rebind_scope",
                "approved_by": approved_by,
                "previous_scope_generation": previous,
                "new_scope_generation": session.scope_generation,
                "previous_include": previous_include,
                "include": list(policy.include),
                "previous_content_identity": previous_identity,
                "content_identity": identity.model_dump(mode="json"),
            },
        )
    )
    return session
