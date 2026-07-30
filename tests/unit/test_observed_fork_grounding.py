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


# --- FR-001a (rev-5) assert-mismatch --------------------------------------------------

def test_assert_mismatch_inline_from_fail_line():
    # SC-001a: the captured formal_05 shape.
    blob = "[FAIL: Should be Fee mode at low coverage: 3 != 1] testSiloPaddingBypassesCooldown()"
    res = ofg.extract_assert_mismatch(blob)
    assert res is not None
    observed, assumed, label = res
    assert observed == "3"       # fork actual (left)
    assert assumed == "1"        # asserted literal (right)
    assert label == "Should be Fee mode at low coverage"


def test_assert_mismatch_block_names_observed_and_assumed():
    blob = "revert: coverage tier: 3 != 1"
    block = ofg.assert_mismatch_observation_block(blob)
    assert block.startswith("[DATA START observed_assert_mismatch]")
    assert "[DATA END]" in block
    # fork value framed as ground truth; asserted literal framed as the narrative assumption
    assert "actual / left operand) = 3" in block
    assert "right operand) = 1" in block
    assert "ground truth" in block
    # deterministic
    assert ofg.assert_mismatch_observation_block(blob) == block


def test_assert_mismatch_leftright_block_rendering():
    blob = "Error: a == b not satisfied [uint]\n      Left: 42\n     Right: 7\n"
    res = ofg.extract_assert_mismatch(blob)
    assert res is not None
    observed, assumed, _ = res
    assert observed == "42" and assumed == "7"


def test_assert_mismatch_degrades_when_operands_not_scalar():
    # A forge assertion mismatch IS present but operands are non-scalar (bytes/string) ->
    # truthful "unavailable" note, never a fabricated value (FR-001a degrade).
    blob = '[FAIL: names differ: "alice" != "bob"] testX()'
    block = ofg.assert_mismatch_observation_block(blob)
    assert block != ""
    assert "unavailable" in block.lower()
    assert "alice" not in block and "bob" not in block  # no fabricated/echoed operand


def test_assert_mismatch_byte_stable_when_no_assertion():
    # FR-006: no assertion mismatch at all -> nothing.
    assert ofg.assert_mismatch_observation_block("") == ""
    assert ofg.assert_mismatch_observation_block("[FAIL: DepositCapReached(0xabc)]") == ""
    assert ofg.assert_mismatch_observation_block("some prose that mentions a != b vars") == ""
    assert ofg.extract_assert_mismatch("next call did not revert as expected") is None


def test_observation_blocks_combines_matched_patterns():
    # FR-005: AccessControl + assert-mismatch in one revert -> one block per matched pattern.
    blob = (
        "[FAIL: AccessControlUnauthorizedAccount("
        "0x1111111111111111111111111111111111111111, "
        "0x2222222222222222222222222222222222222222222222222222222222222222)]\n"
        "tier check: 3 != 1"
    )
    out = ofg.observation_blocks(blob)
    assert "observed_access_control" in out
    assert "observed_assert_mismatch" in out
    # ordered: AccessControl first
    assert out.index("observed_access_control") < out.index("observed_assert_mismatch")


def test_observation_blocks_empty_is_byte_stable():
    # FR-006: unrecognized revert -> no observation at all.
    assert ofg.observation_blocks("just a plain compile error: Undeclared identifier.") == ""
    assert ofg.observation_blocks("") == ""
