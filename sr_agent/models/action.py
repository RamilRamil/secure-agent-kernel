from __future__ import annotations

from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class ActionClass(str, Enum):
    read_only = "read_only"
    write_execute = "write_execute"
    memory = "memory"
    control = "control"


# ── Kernel-owned generic action ids (Constitution III) ───────────────────────
# The kernel's action taxonomy is OPEN: `Action.action_type` is a free string and
# the *domain* action ids (audit analyzers) live in the active capability pack.
# What the kernel still owns are the *generic*, non-domain ids it provides to
# EVERY pack, split by validation behavior (decision D4):
#
#   * KERNEL_GENERIC_ACTIONS — *resolvable* ids that reach `validate_action` and
#     carry a kernel-provided `ActionSpec`. Defined in `orchestrator/action.py`
#     (co-located with `validate_action` and the `_check_filepath` containment
#     primitive) to avoid a models→orchestrator import cycle, since `ActionSpec`
#     lives in `orchestrator/pack.py`. Two sub-roles share the set: the
#     control/memory machinery (`write_memory`, `request_human_confirmation`) and
#     the generic scope-bounded reads (`read_file`, `search_code`) — decision D6.
#   * LOOP_TERMINALS — loop *signals* intercepted BEFORE validation; they carry
#     no `ActionSpec` and never reach `validate_action`, so they are bare strings
#     defined here.
LOOP_TERMINALS: frozenset[str] = frozenset({"escalate", "complete"})


class ValidationStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ValidationResult(BaseModel):
    status: ValidationStatus
    rejection_reason: str | None = None


class Action(BaseModel):
    action_id: str = Field(default_factory=lambda: str(uuid4()))
    # Open string id — resolved against KERNEL_GENERIC_ACTIONS ∪ pack.actions by
    # `validate_action`; the kernel defines no closed domain enum here.
    action_type: str
    params: dict = Field(default_factory=dict)

    # Derived from action_type by the orchestrator, from the resolved ActionSpec
    action_class: ActionClass | None = None
    is_reversible: bool | None = None

    # Validation state
    validation_status: ValidationStatus = ValidationStatus.pending
    rejection_reason: str | None = None
    human_confirmation: bool | None = None   # None = not required
