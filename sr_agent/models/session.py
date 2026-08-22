"""Session — the kernel's structural view of any pack's session (feature 004, R4).

A `typing.Protocol` declaring only the fields the kernel actually reads from a
session: `session_id`, `principal`, `iterations`, `token_budget_used`. The audit
pack's `AuditSession` keeps its domain fields (stages, finding_ids, audit_input)
and structurally satisfies this — no base class, no import from the pack.

The kernel loop, escalation, checkpoint, and chat-session code type to this
instead of to `AuditSession`, so none of them names an audit type.

Feature 003 adds the durable binding a resume needs — `SessionStatus` values for
completed / abandoned / detached, and `ContentIdentity` — here rather than on
the chat model, so a non-chat session can carry the same contract.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

if TYPE_CHECKING:
    from sr_agent.models.principal import Principal


@runtime_checkable
class Session(Protocol):
    session_id: str
    principal: "Principal"
    iterations: int
    token_budget_used: int


# Pause/active set from interactive chat, plus the three terminal/released
# states feature 003 needs so a finished session cannot quietly write again.
SessionStatus = Literal[
    "active",
    "paused_confirmation",
    "paused_relay",
    "blocked_local_unavailable",
    "completed",
    "abandoned",
    "detached",
]

TERMINAL_SESSION_STATUSES: frozenset[str] = frozenset({"completed", "abandoned"})
IN_FLIGHT_SESSION_STATUSES: frozenset[str] = frozenset(
    {"paused_confirmation", "paused_relay", "blocked_local_unavailable"}
)


class ContentIdentity(BaseModel):
    """What a session is bound to, besides the path (FR-013a / D10).

    `digest` is a worktree hash of the bound include set. Git remote/HEAD are
    extra metadata: they do not detect dirty or untracked files, so resume
    compares the digest first and treats HEAD as a comment.
    """
    digest: str
    file_count: int
    total_bytes: int
    git_remote_url: str | None = None
    git_head_sha: str | None = None


class PauseContinuation(BaseModel):
    """In-turn loop state carried inside a `pause_checkpoint` (FR-010a).

    Deliberately has no `system_prompt_body`: prompt bytes come from the trusted
    registry on resume (D18). A body stored here would be untrusted DATA being
    executed as an instruction.
    """
    turn_id: str
    phase: str
    user_message: str
    system_prompt_id: str
    system_prompt_hash: str
    system_prompt_version: str | None = None
    last_dispatch_operation_id: str | None = None
    pending: dict | None = None
    last_tool_body_ref: str | None = None
    tool_calls_used: int = 0
    pending_relay_request_id: str | None = None
    expected_session_revision: int = 0
    session_status: SessionStatus
    action_snapshot: dict | None = None
    scope_generation: int = 0
    content_identity: ContentIdentity | None = None
