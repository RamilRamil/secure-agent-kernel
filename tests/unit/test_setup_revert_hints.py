"""Live-run fix (2026-07-14): an authoritative revert hint for `ERC20InsufficientAllowance`.

The local model wrote a correct same-block-padding exploit that COMPILED and ran on a fork,
reverting only on a missing token approval. The generic exploit-logic feedback didn't name
the specific fix; this deterministic hint does (the revert carries the spender + amount).
"""
from scripts.poc_queue_runner import _setup_revert_hints, revert_hints

_TASK = {"title": "cooldown bypass", "description": "same-block silo padding"}
_FORGE = ("Ran 1 test\n[FAIL: ERC20InsufficientAllowance(0xE58Afa9B6470D6846756Ea6e294ac8A0Fb2e3D22, "
          "0, 850000000000000000000)] test_SameBlockSiloPadding() (gas: 12345)")


def test_allowance_revert_yields_approve_hint():
    h = _setup_revert_hints(_FORGE)
    assert "ERC20InsufficientAllowance" in h
    assert "approve(" in h and "0xE58Afa9B6470D6846756Ea6e294ac8A0Fb2e3D22" in h
    assert "850000000000000000000" in h


def test_no_allowance_revert_yields_nothing():
    assert _setup_revert_hints("[FAIL: assertion failed] test_x()") == ""


def test_revert_hints_prepends_setup_hint_before_generic():
    out = revert_hints(_FORGE, "", _TASK)
    assert out.index("approve(") < out.index("EXPLOIT-LOGIC")   # authoritative fix comes first
    assert "same-block silo padding" in out                     # generic finding context still present


def test_revert_hints_generic_only_when_no_setup_revert():
    out = revert_hints("[FAIL: assertion failed: 1 != 2] test_x()", "", _TASK)
    assert "approve(" not in out and "EXPLOIT-LOGIC" in out
