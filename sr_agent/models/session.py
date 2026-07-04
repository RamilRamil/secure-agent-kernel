"""Session — the kernel's structural view of any pack's session (feature 004, R4).

A `typing.Protocol` declaring only the fields the kernel actually reads from a
session: `session_id`, `principal`, `iterations`, `token_budget_used`. The audit
pack's `AuditSession` keeps its domain fields (stages, finding_ids, audit_input)
and structurally satisfies this — no base class, no import from the pack.

The kernel loop, escalation, checkpoint, and chat-session code type to this
instead of to `AuditSession`, so none of them names an audit type.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sr_agent.models.principal import Principal


@runtime_checkable
class Session(Protocol):
    session_id: str
    principal: "Principal"
    iterations: int
    token_budget_used: int
