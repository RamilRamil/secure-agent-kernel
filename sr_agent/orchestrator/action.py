from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from sr_agent.models.action import (
    Action, ActionClass, ValidationResult, ValidationStatus,
)
from sr_agent.orchestrator.pack import ActionSpec

if TYPE_CHECKING:
    from sr_agent.orchestrator.pack import CapabilityPack

logger = logging.getLogger(__name__)


# ── Kernel-generic reads' param validators (decision D6) ─────────────────────
# `read_file`/`search_code` are generic scope-bounded reads the kernel provides
# to every pack. Their param validation stays kernel-side (this is the partition
# of the old `_validate_params` ladder: the read branches remain here; the domain
# analyzer branches moved to the pack). The path-containment guard
# `_check_filepath` is a Principle I safety primitive the kernel owns; the pack's
# relocated domain ladder imports it rather than re-rolling a traversal check.

def _validate_read_file(action: Action, scope_root: Path) -> str | None:
    return _check_filepath(action.params.get("path"), scope_root)


def _validate_search_code(action: Action, scope_root: Path) -> str | None:
    if not action.params.get("pattern"):
        return "search_code requires 'pattern' param"
    return _check_filepath(action.params.get("root", str(scope_root)), scope_root)


def _noop_validate(action: Action, scope_root: Path) -> str | None:
    """Kernel-generic machinery ids carry no path/param schema of their own."""
    return None


# ── Kernel-owned resolvable generic ids (decisions D4 + D6) ──────────────────
# Resolvable ids that flow through `validate_action` and carry a kernel-provided
# `ActionSpec`. Two sub-roles: control/memory machinery (`write_memory`,
# `request_human_confirmation`) and the generic scope-bounded reads (`read_file`,
# `search_code`, D6). Loop *signals* (`escalate`, `complete`) are NOT here — they
# are `LOOP_TERMINALS`, intercepted before validation (see models/action.py).
KERNEL_GENERIC_ACTIONS: dict[str, ActionSpec] = {
    "read_file": ActionSpec(ActionClass.read_only, True, _validate_read_file),
    "search_code": ActionSpec(ActionClass.read_only, True, _validate_search_code),
    "write_memory": ActionSpec(ActionClass.memory, False, _noop_validate),
    "request_human_confirmation": ActionSpec(ActionClass.control, True, _noop_validate),
}


class ActionValidationError(Exception):
    pass


def validate_action(
    action: Action, scope_root: Path, pack: "CapabilityPack | None" = None
) -> ValidationResult:
    """Deterministic gate before any action is executed — the kernel MECHANISM.

    Checks in order:
    1. action_type resolves against KERNEL_GENERIC_ACTIONS ∪ pack.actions (fail-closed)
    2. action_class and reversibility come from the resolved ActionSpec
    3. params conform to the resolved validator (types, path containment)
    4. WRITE_EXECUTE actions flagged as requiring out-of-band confirmation

    The taxonomy is OPEN: `action.action_type` is a free string. Domain ids are
    supplied by the pack (`pack.actions`); the kernel-generic ids resolve from
    `KERNEL_GENERIC_ACTIONS` even when no pack is active. An id in neither is
    rejected (fail-closed) — never silently defaulted to `read_only`. The
    confirmation requirement is **kernel-derived** from `action_class ==
    write_execute` (a pack has no field to skip it).

    Returns ValidationResult — never raises. Caller decides how to handle rejection.
    """
    key = action.action_type  # free string id (open taxonomy) — no enum

    # ── 1. Resolve id vs KERNEL_GENERIC_ACTIONS ∪ pack.actions (fail-closed) ──
    resolution: dict[str, ActionSpec] = dict(KERNEL_GENERIC_ACTIONS)
    if pack is not None:
        resolution.update(pack.actions)

    spec = resolution.get(key)
    if spec is None:
        return ValidationResult(
            status=ValidationStatus.rejected,
            rejection_reason=f"Unknown action type: {key!r}",
        )

    # ── 2. Annotate class/reversibility from the resolved ActionSpec ──────────
    action.action_class = spec.action_class
    action.is_reversible = spec.is_reversible

    # ── 3. Per-action param validation (fail-closed: path-containment is applied
    #        by the resolved validator; kernel-generic reads keep their guard) ──
    reason = spec.validate_params(action, scope_root)
    if reason:
        return ValidationResult(status=ValidationStatus.rejected, rejection_reason=reason)

    # ── 4. Confirmation — KERNEL RULE, derived from class. A pack cannot mark a
    #        write_execute action skip-confirmation: there is no such field, and
    #        the requirement is computed here, not read. ──
    if action.action_class == ActionClass.write_execute:
        action.human_confirmation = False  # pending — must be set True before execution
        logger.info("Action %s requires out-of-band human confirmation", key)

    return ValidationResult(status=ValidationStatus.approved)


def _check_filepath(
    raw: str | None,
    root: Path | None,
    *,
    require_str: bool = False,
) -> str | None:
    if not raw:
        if require_str:
            return "Required path/id param is missing"
        return None
    if root is None:
        return None
    try:
        resolved = Path(raw).resolve()
        root_resolved = root.resolve()
    except Exception:
        return f"Cannot resolve path: {raw!r}"

    if not resolved.is_relative_to(root_resolved):
        return f"Path '{raw}' escapes scope root — possible path traversal"
    return None
