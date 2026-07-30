"""Offline tests for feature 044 PART 3 - anti-mock grounding block (target-free).

All checks are offline/deterministic/target-free: invented prompt strings, no model call,
no sandbox, no network. Maps to spec 044 SC-001..003 and FR-001/FR-002/FR-006.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from scripts import anti_mock_grounding as amg


# ── SC-001: present when scaffold carried, absent + byte-stable when not, idempotent ──

def test_block_present_when_scaffold_carried():
    base = "PROMPT_BODY"
    out = amg.append_anti_mock_grounding(base, scaffold_carried=True)
    assert "[DATA START anti_mock_grounding]" in out
    assert "[DATA END]" in out
    assert out.startswith(base)  # appended, original body preserved


def test_byte_stable_when_no_scaffold():
    base = "PROMPT_BODY"
    # FR-002: byte-identical no-op on the false gate.
    assert amg.append_anti_mock_grounding(base, scaffold_carried=False) == base
    # empty prompt path (simulated infra path) is also byte-stable.
    assert amg.append_anti_mock_grounding("", scaffold_carried=False) == ""


def test_idempotent_when_already_present():
    base = "PROMPT_BODY"
    once = amg.append_anti_mock_grounding(base, scaffold_carried=True)
    twice = amg.append_anti_mock_grounding(once, scaffold_carried=True)
    assert twice == once  # marker guard prevents a second block


# ── SC-002 (N/A by construction) + FR-006 (ASCII) ──

def test_sc002_helper_has_no_040_layer_access():
    # The helper's ONLY inputs are (prompt, scaffold_carried); it exposes/touches no 040
    # cause/nature field, so it physically cannot misattribute an infra failure as a
    # model-side vacuity. This is a boundary/invariant assertion (cannot fail by design).
    sig = inspect.signature(amg.append_anti_mock_grounding)
    assert list(sig.parameters) == ["prompt", "scaffold_carried"]
    src = inspect.getsource(amg)
    # No reference to the 040 taxonomy / cause-nature layer from this module.
    for forbidden in ("scaffold_causes", "cause", "nature", "finding_class"):
        assert forbidden not in src, f"helper must not touch 040 layer: {forbidden!r}"


def test_fr006_block_is_ascii():
    assert amg.ANTI_MOCK_INSTRUCTION.isascii()
    assert amg.append_anti_mock_grounding("x", True).isascii()


# ── T010: the pairing invariant — catches the N+1th prompt-assembly site ──

def test_pairing_invariant_all_sites_wired():
    """Every site that appends the 045 discipline block MUST also append the 044 block.

    A future draft/fix path that adds `ofg.append_discipline_instruction(` without a
    matching `amg.append_anti_mock_grounding(` would silently omit the anti-mock block on
    that path (invisible to the behavioral tests above, which drive draft()/fix() only).
    This locks the counts equal (research Decision 2).
    """
    runner = Path(__file__).resolve().parents[2] / "scripts" / "poc_queue_runner.py"
    text = runner.read_text()
    ofg_sites = len(re.findall(r"ofg\.append_discipline_instruction\(", text))
    amg_sites = len(re.findall(r"amg\.append_anti_mock_grounding\(", text))
    assert ofg_sites >= 3, f"expected >=3 discipline sites, found {ofg_sites}"
    assert amg_sites == ofg_sites, (
        f"anti-mock append wired at {amg_sites} sites but discipline append at "
        f"{ofg_sites}; a draft/fix path is missing the 044 block"
    )
