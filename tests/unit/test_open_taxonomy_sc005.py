"""SC-005 — the primary positive proof that the action taxonomy is task-agnostic.

The kernel resolves a validated action id from `KERNEL_GENERIC_ACTIONS ∪
pack.actions` with NO knowledge of the domain:

  (a) a DOMAIN id the kernel has never heard of (`do_thing`, supplied only by the
      pack) resolves to its `ActionSpec`, and the kernel DERIVES the OOB-confirm
      requirement from `action_class == write_execute` — the pack has no lever to
      skip it;
  (c) the kernel-generic ids (`write_memory`, `read_file`) resolve from
      `KERNEL_GENERIC_ACTIONS` even though the FIXTURE_PACK declares NONE of them —
      proving they are inherited, not pack-supplied.

See specs/001-task-agnostic-contract/tasks.md T004 (SC-005 a+c).
"""
from pathlib import Path

import pytest

from sr_agent.models.action import Action, ActionClass, ValidationStatus
from sr_agent.orchestrator.action import KERNEL_GENERIC_ACTIONS, validate_action

from tests.fixtures.pack import DO_THING, FIXTURE_PACK


@pytest.fixture
def scope_root(tmp_path: Path) -> Path:
    (tmp_path / "Vault.sol").write_text("// solidity")
    return tmp_path


def test_sc005a_domain_write_execute_resolves_and_kernel_derives_confirmation(scope_root):
    action = Action(action_type=DO_THING, params={"finding_id": "HIGH-001"})
    result = validate_action(action, scope_root, FIXTURE_PACK)
    assert result.status == ValidationStatus.approved
    # class came from the PACK's ActionSpec; the kernel never hardcoded `do_thing`.
    assert action.action_class == ActionClass.write_execute
    assert action.is_reversible is False
    # confirmation is KERNEL-derived from the class, not a pack field.
    assert action.human_confirmation is False


def test_sc005c_kernel_generic_ids_resolve_though_pack_declares_none(scope_root):
    # The fixture pack declares ONLY `do_thing` — no generics.
    assert set(FIXTURE_PACK.actions) == {DO_THING}

    # write_memory (memory machinery) resolves from KERNEL_GENERIC_ACTIONS.
    # Feature 002 gave the id a real param validator, so the payload here must be
    # a valid one — the subject of this test is RESOLUTION from the kernel set,
    # not param policy, and it must keep proving exactly that.
    mem = Action(action_type="write_memory", params={"note": "a note"})
    mem_result = validate_action(mem, scope_root, FIXTURE_PACK)
    assert mem_result.status == ValidationStatus.approved
    assert mem.action_class == ActionClass.memory
    # memory is not write_execute → no OOB gate flagged.
    assert mem.human_confirmation is None

    # Sibling assertion so the change above reads as a policy addition rather
    # than a quiet edit: empty params now reject, and reject *after* resolving
    # (the id is known; it is the params that fail). Feature 002, FR-004.
    empty = Action(action_type="write_memory", params={})
    empty_result = validate_action(empty, scope_root, FIXTURE_PACK)
    assert empty_result.status == ValidationStatus.rejected
    assert "note" in (empty_result.rejection_reason or "")
    assert empty.action_class == ActionClass.memory

    # read_file (generic scope-bounded read, D6) resolves and enforces containment.
    target = scope_root / "Vault.sol"
    rf = Action(action_type="read_file", params={"path": str(target)})
    rf_result = validate_action(rf, scope_root, FIXTURE_PACK)
    assert rf_result.status == ValidationStatus.approved
    assert rf.action_class == ActionClass.read_only

    # And both are genuinely kernel-owned, not inherited from the pack.
    assert {"write_memory", "read_file"} <= set(KERNEL_GENERIC_ACTIONS)


def test_sc005_kernel_generic_resolve_with_no_pack_at_all(scope_root):
    """The generics resolve even when no pack is active (pack=None)."""
    rf = Action(action_type="read_file", params={"path": str(scope_root / "Vault.sol")})
    assert validate_action(rf, scope_root).status == ValidationStatus.approved
