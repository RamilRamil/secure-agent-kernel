"""SC-001/002/003: tool descriptor integrity (supply-chain) — no live LLM."""
from __future__ import annotations

import pytest

from sr_agent.orchestrator.action import KERNEL_GENERIC_ACTIONS, validate_action
from sr_agent.models.action import Action, ValidationStatus
from sr_agent.tools.registry import TOOL_REGISTRY, ToolDefinition, ToolTampered, verify_all_hashes


def test_SC001_verify_all_hashes_passes_on_clean_registry() -> None:
    verify_all_hashes()


def test_SC002_tampered_description_raises_tool_tampered(monkeypatch: pytest.MonkeyPatch) -> None:
    original = TOOL_REGISTRY["read_file"]
    tampered = ToolDefinition(
        name=original.name,
        description=original.description + " TAMPERED",
        action_class=original.action_class,
        description_hash=original.description_hash,  # stale hash
    )
    monkeypatch.setitem(TOOL_REGISTRY, "read_file", tampered)
    with pytest.raises(ToolTampered, match="read_file"):
        verify_all_hashes()
    monkeypatch.setitem(TOOL_REGISTRY, "read_file", original)
    verify_all_hashes()


def test_SC003_unknown_tool_id_not_in_kernel_generic_resolution(tmp_path) -> None:
    """Pack/tool descriptors cannot invent a resolvable action without ActionSpec."""
    action = Action(action_type="totally_unknown_exfil", params={"dest": "x"})
    result = validate_action(action, tmp_path, pack=None)
    assert result.status is ValidationStatus.rejected
    assert "totally_unknown_exfil" not in KERNEL_GENERIC_ACTIONS
