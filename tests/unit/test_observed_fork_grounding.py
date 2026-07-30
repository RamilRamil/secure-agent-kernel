"""Offline Phase A tests for feature 045 (target-free synthetics)."""
from __future__ import annotations

from scripts import observed_fork_grounding as ofg


def test_discipline_appended_only_for_state_heavy():
    base = "PROMPT_BODY"
    heavy = {"finding_class": "accounting_manipulation"}
    light = {"finding_class": "denial_of_service"}
    out_h = ofg.append_discipline_instruction(base, heavy)
    out_l = ofg.append_discipline_instruction(base, light)
    assert out_l == base
    assert "state_grounding_discipline" in out_h
    assert "console.log" in out_h
    assert "[DATA START" in out_h and "[DATA END]" in out_h
    # idempotent
    assert ofg.append_discipline_instruction(out_h, heavy) == out_h


def test_discipline_byte_stable_absent_class():
    base = "PROMPT_BODY"
    assert ofg.append_discipline_instruction(base, {}) == base
    assert ofg.append_discipline_instruction(base, {"class": ""}) == base


def test_access_control_operands_from_fail_line():
    blob = (
        "[FAIL: AccessControlUnauthorizedAccount("
        "0x925Bb1766485C4DdEd06a5e53Dc3721F0E3cc534, "
        "0x97d8f58e0f008a842799f9fb6a4e6212ac93db0249409467f149ba102924e880)] "
        "testFoo()"
    )
    ops = ofg.extract_access_control_operands(blob)
    assert ops is not None
    account, role = ops
    assert account == "0x925Bb1766485C4DdEd06a5e53Dc3721F0E3cc534"
    assert role.startswith("0x97d8f58e")


def test_access_control_observation_block_deterministic():
    blob = (
        "AccessControlUnauthorizedAccount("
        "0x1111111111111111111111111111111111111111, "
        "0x2222222222222222222222222222222222222222222222222222222222222222)"
    )
    a = ofg.access_control_observation_block(blob)
    b = ofg.access_control_observation_block(blob)
    assert a == b
    assert a.startswith("[DATA START observed_access_control]")
    assert "PoC caller" in a
    assert "0x1111111111111111111111111111111111111111" in a
    assert "requires role" in a


def test_fr006_unknown_shape_emits_nothing():
    assert ofg.access_control_observation_block("") == ""
    assert ofg.access_control_observation_block("[FAIL: DepositCapReached(0xabc)]") == ""
    assert ofg.access_control_observation_block("next call did not revert as expected") == ""
    assert ofg.extract_access_control_operands("ConfigManagerOnly()") is None


def test_discipline_instruction_is_author_guidance_not_harness():
    # FR-003: phrasing is guidance ("READ" / "console.log"), not a harness command.
    assert "console.log" in ofg.DISCIPLINE_INSTRUCTION
    assert "harness" not in ofg.DISCIPLINE_INSTRUCTION.lower()
    assert "cast call" not in ofg.DISCIPLINE_INSTRUCTION.lower()


def test_discipline_requires_positioning_before_reverting_call():
    # SC-002 (rev-4): the instruction must require the read-and-log to sit at the TOP,
    # BEFORE the first reverting call - not merely "before the assert". This is the concrete
    # SC-A run1 defect (console.log placed after the reverting deposit).
    text = ofg.DISCIPLINE_INSTRUCTION.lower()
    assert "top" in text
    assert "before the first call that can revert" in text
    # names the failure mode so the clause is not cosmetic
    assert "after the reverting call" in text
