from __future__ import annotations

import logging
from pathlib import Path

from sr_agent.models.action import (
    Action, ActionClass, ActionType, ValidationResult, ValidationStatus,
    ACTION_CLASS_MAP, REVERSIBLE,
)
from sr_agent.tools.registry import TOOL_REGISTRY

logger = logging.getLogger(__name__)

# Actions in this set pause execution and wait for out-of-band human confirmation
REQUIRES_OOB_CONFIRMATION: frozenset[ActionType] = frozenset({
    ActionType.write_poc,
    ActionType.run_tests,
    ActionType.deploy_test_contract,
})


class ActionValidationError(Exception):
    pass


def validate_action(action: Action, audit_root: Path) -> ValidationResult:
    """Deterministic gate before any action is executed.

    Checks in order:
    1. action_type is in TOOL_REGISTRY whitelist
    2. params conform to per-tool schema (types, path containment)
    3. action_class and reversibility are annotated
    4. WRITE_EXECUTE actions flagged as requiring out-of-band confirmation

    Returns ValidationResult — never raises. Caller decides how to handle rejection.
    """
    # ── 1. Whitelist check ───────────────────────────────────────────────
    if action.action_type.value not in TOOL_REGISTRY:
        return ValidationResult(
            status=ValidationStatus.rejected,
            rejection_reason=f"Unknown action type: {action.action_type.value!r}",
        )

    # ── 2. Annotate class and reversibility ──────────────────────────────
    action.action_class = ACTION_CLASS_MAP[action.action_type]
    action.is_reversible = REVERSIBLE[action.action_type]

    # ── 3. Per-tool param validation ─────────────────────────────────────
    reason = _validate_params(action, audit_root)
    if reason:
        return ValidationResult(status=ValidationStatus.rejected, rejection_reason=reason)

    # ── 4. Write/execute flag ─────────────────────────────────────────────
    if action.action_type in REQUIRES_OOB_CONFIRMATION:
        action.human_confirmation = False  # pending — must be set True before execution
        logger.info("Action %s requires out-of-band human confirmation", action.action_type)

    return ValidationResult(status=ValidationStatus.approved)


def _validate_params(action: Action, audit_root: Path) -> str | None:
    """Return rejection reason string, or None if params are valid."""
    params = action.params

    if action.action_type == ActionType.read_file:
        return _check_filepath(params.get("path"), audit_root)

    if action.action_type == ActionType.search_code:
        if not params.get("pattern"):
            return "search_code requires 'pattern' param"
        return _check_filepath(params.get("root", str(audit_root)), audit_root)

    if action.action_type == ActionType.run_slither:
        return _check_filepath(params.get("target"), audit_root)

    if action.action_type == ActionType.run_mythril:
        return _check_filepath(params.get("target"), audit_root)

    if action.action_type == ActionType.analyze_transactions:
        address = params.get("address")
        if not address:
            return "analyze_transactions requires 'address' param"
        blocks = params.get("max_blocks", 0)
        if int(blocks) > 10_000:
            return f"analyze_transactions max_blocks limit is 10000, got {blocks}"

    if action.action_type == ActionType.write_poc:
        return _check_filepath(params.get("finding_id"), None, require_str=True)

    if action.action_type == ActionType.deploy_test_contract:
        network = params.get("network", "")
        if network not in ("anvil", "localhost"):
            return f"deploy_test_contract only allowed on anvil/localhost, got {network!r}"

    return None


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
        return f"Path '{raw}' escapes audit root — possible path traversal"
    return None
