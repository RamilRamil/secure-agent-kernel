"""The writer lease: one project, one writer (feature 003, FR-014 / D16).

A plain lock file would be wrong here, and the reason is worth stating because it
is the whole design. The CLI gives up its OS-level hold whenever a turn pauses
for an operator, so "no process is holding this" does not mean "no session is
using this". A lock that cannot tell those apart has to pick a failure: either it
lets a stranger take a project out from under a session that is merely waiting
for a human answer, or it strands that session forever.

The modes are that distinction made explicit:

* `active_process` is a live turn. A crashed process never announces itself, so
  this mode -- and only this mode -- becomes stealable once its heartbeat lapses.
* `paused_reserved` is a turn waiting on a human. It never times out at all. The
  wait can legitimately outlast any window worth choosing, so releasing it is an
  operator decision (`takeover`), never a clock's.
* `detached` is a session that stepped away but may come back. Someone else may
  start a new session; the original may still reacquire.
* `completed` / `abandoned` are terminal. History stays readable, but the writer
  role does not come back -- a session that declared itself finished must not
  quietly resume writing.

The state lives in one signed file per project, so an attacker with write access
can destroy it but cannot rewrite it into someone else's name.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from sr_agent.memory import hmac as hmac_module

# How long an `active_process` may go without a heartbeat before it is presumed
# crashed. Applies to that mode ONLY; see the module docstring.
HEARTBEAT_WINDOW_SECONDS = 90.0


class LeaseMode(str, Enum):
    active_process = "active_process"
    paused_reserved = "paused_reserved"
    detached = "detached"
    completed = "completed"
    abandoned = "abandoned"
    # Derived, never stored: an `active_process` whose heartbeat has lapsed.
    crashed_active = "crashed_active"


#: Modes in which the project belongs to its owner and nobody else may write.
_HELD = frozenset({LeaseMode.active_process, LeaseMode.paused_reserved})

#: Modes a different session may take over from.
_FREE = frozenset(
    {LeaseMode.detached, LeaseMode.completed, LeaseMode.abandoned, LeaseMode.crashed_active}
)

#: Terminal modes: the same session must not become writer again.
_TERMINAL = frozenset({LeaseMode.completed, LeaseMode.abandoned})


class LeaseUnavailable(Exception):
    """Raised when a session may not become (or resume as) the project's writer."""
    pass


class LeaseNotHeld(Exception):
    """Raised when a session tries to change a lease it does not own."""
    pass


@dataclass(frozen=True)
class LeaseState:
    session_id: str
    mode: LeaseMode
    heartbeat_at: float


class WriterLease:
    def __init__(
        self,
        memory_root: Path,
        secret_key: bytes,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._root = memory_root
        self._secret_key = secret_key
        self._clock = clock

    # ── Reading ─────────────────────────────────────────────────────────────

    def _path(self, project_id: str) -> Path:
        # Not "*.jsonl": the record globs must never pick this up as a target.
        return self._root / project_id / "_writer_lease.json"

    def state(self, project_id: str) -> LeaseState | None:
        """The stored lease, or None if absent or not authentic.

        An unverifiable lease reads as *no* lease rather than as an error: it is
        the same posture as an unsigned record, and it means damaging the file
        cannot lock a project up permanently. It also cannot hand the project to
        the attacker's chosen session, because a forged name never verifies.
        """
        path = self._path(project_id)
        if not path.exists():
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            signature = doc.pop("hmac", None)
        except Exception:
            return None
        if not isinstance(signature, str):
            return None
        if not hmac_module.verify(doc, signature, self._secret_key):
            return None
        try:
            return LeaseState(
                session_id=doc["session_id"],
                mode=LeaseMode(doc["mode"]),
                heartbeat_at=float(doc["heartbeat_at"]),
            )
        except Exception:
            return None

    def effective_mode(self, project_id: str) -> LeaseMode | None:
        """Stored mode, with a lapsed `active_process` reported as crashed."""
        current = self.state(project_id)
        if current is None:
            return None
        if (
            current.mode is LeaseMode.active_process
            and self._clock() - current.heartbeat_at > HEARTBEAT_WINDOW_SECONDS
        ):
            return LeaseMode.crashed_active
        return current.mode

    def holds(self, project_id: str, session_id: str) -> bool:
        """True when this session may append durable records for the project."""
        current = self.state(project_id)
        if current is None or current.session_id != session_id:
            return False
        return current.mode in _HELD

    def require_owner(self, project_id: str, session_id: str) -> None:
        if not self.holds(project_id, session_id):
            current = self.state(project_id)
            held_by = "nobody" if current is None else (
                f"{current.session_id!r} ({current.mode.value})"
            )
            raise LeaseNotHeld(
                f"Session {session_id!r} does not hold the writer lease for project "
                f"{project_id!r}; it is held by {held_by}."
            )

    # ── Acquiring ───────────────────────────────────────────────────────────

    def acquire(self, project_id: str, session_id: str) -> LeaseState:
        """Become the project's writer, or refuse."""
        mode = self.effective_mode(project_id)
        current = self.state(project_id)

        if mode is None or mode in _FREE:
            if current is not None and current.session_id == session_id and mode in _TERMINAL:
                raise LeaseUnavailable(
                    f"Session {session_id!r} is {mode.value} and cannot write again. "
                    "Its history stays readable; start a new session to continue."
                )
            return self._store(project_id, session_id, LeaseMode.active_process)

        if current is not None and current.session_id == session_id:
            return self._store(project_id, session_id, LeaseMode.active_process)

        raise LeaseUnavailable(
            f"Project {project_id!r} is held by session {current.session_id!r} "
            f"({mode.value}); session {session_id!r} cannot write. "
            + (
                "A paused session is waiting for a human answer and is never "
                "released on a timeout — use `takeover_lease` if that is intended."
                if mode is LeaseMode.paused_reserved
                else ""
            )
        )

    def reacquire(self, project_id: str, session_id: str) -> LeaseState:
        """Resume as writer after a pause or a detach.

        Falls through to `acquire` when the project is currently free, because a
        detached session may legitimately come back after somebody else has used
        and released the project in the meantime (FR-014, sequential writer).
        """
        current = self.state(project_id)
        if current is None or current.session_id != session_id:
            return self.acquire(project_id, session_id)
        if current.mode in _TERMINAL:
            raise LeaseUnavailable(
                f"Session {session_id!r} is {current.mode.value} and cannot write again."
            )
        return self._store(project_id, session_id, LeaseMode.active_process)

    # ── Transitions the owner makes ─────────────────────────────────────────

    def heartbeat(self, project_id: str, session_id: str) -> LeaseState:
        self._require_current_owner(project_id, session_id)
        return self._store(project_id, session_id, LeaseMode.active_process)

    def pause(self, project_id: str, session_id: str) -> LeaseState:
        """Set by `pause_checkpoint`. Reserved indefinitely; no timeout-steal."""
        self._require_current_owner(project_id, session_id)
        return self._store(project_id, session_id, LeaseMode.paused_reserved)

    def detach(self, project_id: str, session_id: str) -> LeaseState:
        self._require_current_owner(project_id, session_id)
        return self._store(project_id, session_id, LeaseMode.detached)

    def complete(self, project_id: str, session_id: str) -> LeaseState:
        self._require_current_owner(project_id, session_id)
        return self._store(project_id, session_id, LeaseMode.completed)

    def abandon(self, project_id: str, session_id: str) -> LeaseState:
        self._require_current_owner(project_id, session_id)
        return self._store(project_id, session_id, LeaseMode.abandoned)

    # ── The operator's lever ────────────────────────────────────────────────

    def takeover(self, project_id: str, approved_by: str) -> LeaseState:
        """Release a reservation by human decision (FR-014).

        The only way out of `paused_reserved` other than its owner returning.
        Deliberately not reachable from inside a turn: it discards a session that
        is waiting for an answer, which is a judgement about intent, not a
        scheduling problem.
        """
        current = self.state(project_id)
        if current is None:
            raise LeaseUnavailable(f"No lease on project {project_id!r} to take over.")
        return self._store(project_id, current.session_id, LeaseMode.abandoned)

    # ── Writing ─────────────────────────────────────────────────────────────

    def _require_current_owner(self, project_id: str, session_id: str) -> None:
        current = self.state(project_id)
        if current is None or current.session_id != session_id:
            raise LeaseNotHeld(
                f"Session {session_id!r} does not own the lease on project {project_id!r}."
            )

    def _store(self, project_id: str, session_id: str, mode: LeaseMode) -> LeaseState:
        doc = {
            "session_id": session_id,
            "mode": mode.value,
            "heartbeat_at": self._clock(),
        }
        payload = dict(doc, hmac=hmac_module.sign(doc, self._secret_key))
        path = self._path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        tmp.replace(path)   # atomic swap: never leave a half-written lease
        return LeaseState(session_id=session_id, mode=mode, heartbeat_at=doc["heartbeat_at"])
