"""Kernel `validate_action` — the task-agnostic gate (feature 001 rehost).

After the taxonomy opened (Constitution III), this suite proves only what the
KERNEL owns:

  * the kernel-generic reads (`read_file`, `search_code`) resolve and enforce
    path-containment with NO pack active;
  * the kernel derives "write_execute ⇒ OOB confirm" from `action_class`, on a
    DOMAIN id the kernel has never heard of (the fixture's `do_thing`);
  * an unknown id fails closed.

Domain param rules that used to live here (`analyze_transactions` block limits,
`deploy_test_contract` network allowlist, `write_poc`) have MOVED to the audit
pack and are proven against `AUDIT_PACK` in the araratsec repo — a kernel test
must not know that vocabulary.
"""
from pathlib import Path

import pytest

from sr_agent.models.action import Action, ValidationStatus
from sr_agent.orchestrator.action import validate_action

from tests.fixtures.pack import DO_THING, FIXTURE_PACK


@pytest.fixture
def scope_root(tmp_path: Path) -> Path:
    (tmp_path / "Vault.sol").write_text("// solidity")
    return tmp_path


def test_read_file_in_root_passes(scope_root):
    """A kernel-generic read resolves WITHOUT a pack (inherited id)."""
    target = scope_root / "Vault.sol"
    action = Action(action_type="read_file", params={"path": str(target)})
    result = validate_action(action, scope_root)
    assert result.status == ValidationStatus.approved
    assert action.action_class.value == "read_only"


def test_path_traversal_rejected(scope_root):
    action = Action(
        action_type="read_file",
        params={"path": str(scope_root / ".." / ".." / "etc" / "passwd")},
    )
    result = validate_action(action, scope_root)
    assert result.status == ValidationStatus.rejected
    assert "traversal" in result.rejection_reason.lower()


def test_search_code_requires_pattern(scope_root):
    """The other kernel-generic read enforces its own param, still no pack."""
    action = Action(action_type="search_code", params={"root": str(scope_root)})
    result = validate_action(action, scope_root)
    assert result.status == ValidationStatus.rejected
    assert "pattern" in result.rejection_reason.lower()


def test_write_execute_flagged_for_confirmation(scope_root):
    """Kernel derives the OOB gate from action_class on a pack DOMAIN id."""
    action = Action(action_type=DO_THING, params={"finding_id": "HIGH-001"})
    result = validate_action(action, scope_root, FIXTURE_PACK)
    assert result.status == ValidationStatus.approved
    assert action.action_class.value == "write_execute"
    assert action.human_confirmation is False  # pending confirmation


def test_unknown_action_fails_closed(scope_root):
    """An id in neither KERNEL_GENERIC_ACTIONS nor pack.actions is rejected."""
    action = Action(action_type="totally_unknown", params={})
    result = validate_action(action, scope_root, FIXTURE_PACK)
    assert result.status == ValidationStatus.rejected
    assert "unknown action" in result.rejection_reason.lower()
