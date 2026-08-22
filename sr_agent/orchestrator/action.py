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


# ── `write_memory` param policy (feature 002, FR-004 / D11 / D12) ────────────
# The EXPLICIT half of the defence. The structural half lives in
# `KernelActionExecutor`, which builds the record from a fixed shape and never
# reads a forbidden key at all — that is the half that actually holds, because a
# future caller can forget to validate but cannot make a fixed shape carry a
# forged tier.
#
# This half exists anyway because *reject* and *strip* are not equivalent: a
# stripped attempt is indistinguishable from an attempt never made, which makes
# the structural defence untestable and hands the model a silent failure it can
# probe. Same reasoning as `DispatchPayload`'s `extra="forbid"` in feature 003.

#: Identity and signature fields a model must never author. `target` is
#: deliberately absent: it is the one params key this path honours (D11), for the
#: same reason a `dispatch_commit` already takes its target from params.
_WRITE_MEMORY_FORBIDDEN_PARAMS: frozenset[str] = frozenset({
    "source_type", "hmac", "supersedes", "status_change", "status", "seq",
    "chain_prev", "log_sequence", "record_id", "project_id", "session_id", "tool",
})

#: A target becomes a filename. The bound is about error quality and about WHEN
#: the failure lands, not about containment — `project_id` comes from the
#: principal and `_target_stem` renders separators inert, so an unbounded target
#: is an `OSError` deep inside the append rather than an escape.
MAX_TARGET_BYTES = 200


def check_target(value: object, *, label: str) -> str | None:
    """Return why `value` is unusable as a memory-record target, or None.

    One rule, two callers (`write_memory` params and the dispatch-commit
    target), so the two cannot drift into disagreeing about what a target may
    be. `label` only names the offending field in the message.
    """
    if not isinstance(value, str) or not value.strip():
        return f"{label} must be a non-empty string"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return f"{label} may not contain control characters"
    size = len(value.encode("utf-8"))
    if size > MAX_TARGET_BYTES:
        return f"{label} is {size} bytes, over the limit of {MAX_TARGET_BYTES}"
    return None


def commit_target(action: Action) -> str:
    """The target a dispatch commit is filed under.

    The single reading of it, shared by `validate_commit_target` and by the
    executor's two `commit_if_absent` calls — validating one expression and
    committing another is how a bound gets checked against a value that is not
    the one used. An empty or absent `target` falls back to the action id, which
    is pre-existing behaviour: such a target never reached the filesystem, so the
    bound has no reason to start rejecting it.
    """
    return str(action.params.get("target") or action.action_type)


def validate_commit_target(action: Action) -> str | None:
    """Bound the dispatch-commit target BEFORE `pack.dispatch` runs.

    Deliberately not applied at the `commit_if_absent` call site, where the
    obvious fix would go. By then the pack has already acted on the outside
    world, and a refusal there would leave the effect done with no commit record
    — so `find_committed_bundle` finds nothing on a retry and the same action
    dispatches a second time. For a `write_execute` action that carries one
    out-of-band confirmation, that is one human approval covering two
    executions (Constitution II). Refusing up front makes the failure mode
    "nothing happened".
    """
    named = action.params.get("target")
    if named is not None and not isinstance(named, str):
        # `commit_target` would `str()` this into a plausible-looking filename.
        return "dispatch 'target' must be a string when supplied"
    return check_target(commit_target(action), label="dispatch target")


def validate_write_memory(action: Action, scope_root: Path) -> str | None:
    """Fail closed on anything but a plain note and an optional bounded target.

    Public because `KernelActionExecutor` calls it a second time, on purpose:
    `validate_action` lets `pack.actions` shadow a kernel-generic id, so the
    validator that ran there is not guaranteed to have been this one.
    """
    from sr_agent.models.dispatch import MAX_PAYLOAD_BODY_BYTES

    forged = sorted(_WRITE_MEMORY_FORBIDDEN_PARAMS & set(action.params))
    if forged:
        return (
            f"write_memory may not carry {', '.join(forged)}: provenance and "
            "signature fields are kernel-set, never model-supplied"
        )

    note = action.params.get("note")
    if not isinstance(note, str) or not note.strip():
        return "write_memory requires a non-empty 'note' string param"
    size = len(note.encode("utf-8"))
    if size > MAX_PAYLOAD_BODY_BYTES:
        return (
            f"write_memory note is {size} bytes, over the limit of "
            f"{MAX_PAYLOAD_BODY_BYTES}; nothing was persisted"
        )

    if "target" in action.params:
        reason = check_target(action.params["target"], label="write_memory 'target'")
        if reason:
            return reason

    return None


def write_memory_content(action: Action) -> tuple[str, str]:
    """Return `(note, target)` for a *validated* `write_memory`.

    One reading of the params, shared by the validator's contract and the
    executor's construction, so the two cannot drift into disagreeing about what
    the same action said. Falls back to the action id when no target is named,
    matching how a `dispatch_commit` resolves its own target.
    """
    note = str(action.params["note"])
    target = str(action.params.get("target") or action.action_type)
    return note, target


# ── Kernel-owned resolvable generic ids (decisions D4 + D6) ──────────────────
# Resolvable ids that flow through `validate_action` and carry a kernel-provided
# `ActionSpec`. Two sub-roles: control/memory machinery (`write_memory`,
# `request_human_confirmation`) and the generic scope-bounded reads (`read_file`,
# `search_code`, D6). Loop *signals* (`escalate`, `complete`) are NOT here — they
# are `LOOP_TERMINALS`, intercepted before validation (see models/action.py).
KERNEL_GENERIC_ACTIONS: dict[str, ActionSpec] = {
    "read_file": ActionSpec(ActionClass.read_only, True, _validate_read_file),
    "search_code": ActionSpec(ActionClass.read_only, True, _validate_search_code),
    # Class and reversibility are UNCHANGED by feature 002 — `ActionClass.memory`
    # still derives no out-of-band gate (FR-005). Only the param validator moved
    # off `_noop_validate`.
    "write_memory": ActionSpec(ActionClass.memory, False, validate_write_memory),
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
