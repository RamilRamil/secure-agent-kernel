import pytest
from pathlib import Path

from sr_agent.models.action import Action, ActionType, ValidationStatus
from sr_agent.orchestrator.action import validate_action


@pytest.fixture
def audit_root(tmp_path: Path) -> Path:
    (tmp_path / "Vault.sol").write_text("// solidity")
    return tmp_path


def test_read_file_in_root_passes(audit_root):
    target = audit_root / "Vault.sol"
    action = Action(
        action_type=ActionType.read_file,
        params={"path": str(target)},
    )
    result = validate_action(action, audit_root)
    assert result.status == ValidationStatus.approved


def test_path_traversal_rejected(audit_root):
    action = Action(
        action_type=ActionType.read_file,
        params={"path": str(audit_root / ".." / ".." / "etc" / "passwd")},
    )
    result = validate_action(action, audit_root)
    assert result.status == ValidationStatus.rejected
    assert "path traversal" in result.rejection_reason.lower()


def test_write_execute_flagged_for_confirmation(audit_root):
    action = Action(
        action_type=ActionType.write_poc,
        params={"finding_id": "HIGH-001"},
    )
    result = validate_action(action, audit_root)
    assert result.status == ValidationStatus.approved
    assert action.human_confirmation is False  # pending confirmation


def test_analyze_transactions_block_limit(audit_root):
    action = Action(
        action_type=ActionType.analyze_transactions,
        params={"address": "0xAbCd" * 10, "max_blocks": 99999},
    )
    result = validate_action(action, audit_root)
    assert result.status == ValidationStatus.rejected
    assert "10000" in result.rejection_reason


def test_deploy_non_anvil_rejected(audit_root):
    action = Action(
        action_type=ActionType.deploy_test_contract,
        params={"network": "mainnet"},
    )
    result = validate_action(action, audit_root)
    assert result.status == ValidationStatus.rejected
    assert "mainnet" in result.rejection_reason
