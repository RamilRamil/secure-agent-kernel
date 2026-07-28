"""Unit tests for scripts/compiled_checkpoint.py (feature 043)."""
from __future__ import annotations

from pathlib import Path

from scripts import compiled_checkpoint as cc

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "compiled_checkpoint"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_is_compiled_real():
    assert cc.is_compiled_real(compiled=True, defects=[]) is True
    assert cc.is_compiled_real(compiled=True, defects=["no active assertion"]) is False
    assert cc.is_compiled_real(compiled=False, defects=[]) is False


def test_restore_target_empty_then_non_vacuous_then_vacuous_fallback():
    st = cc.CheckpointState()
    assert cc.restore_target(st) is None

    a = _load("non_vacuous_a.sol")
    st = cc.refresh_slots(
        st, source=a, attempt=1, compiled=True, compiled_real=True, outcome_summary="fail")
    slot = cc.restore_target(st)
    assert slot is not None and slot.source == a and slot.attempt == 1
    assert cc.restore_kind(st) == "non_vacuous"

    st2 = cc.CheckpointState()
    b = _load("vacuous_b.sol")
    st2 = cc.refresh_slots(
        st2, source=b, attempt=2, compiled=True, compiled_real=False, outcome_summary="vacuous")
    slot2 = cc.restore_target(st2)
    assert slot2 is not None and slot2.source == b
    assert cc.restore_kind(st2) == "any_compile"
    assert st2.non_vacuous is None


def test_should_refuse():
    assert cc.should_refuse(compiled=False, restore_target_present=True) is True
    assert cc.should_refuse(compiled=True, restore_target_present=True) is False
    assert cc.should_refuse(compiled=False, restore_target_present=False) is False


def test_sc001_refuse_restores_non_vacuous_a():
    a = _load("non_vacuous_a.sol")
    c = _load("noncompiling_c.sol")
    st = cc.refresh_slots(
        cc.CheckpointState(), source=a, attempt=1, compiled=True, compiled_real=True)
    assert cc.should_refuse(compiled=False, restore_target_present=True)
    assert cc.apply_refuse(c, st) == a


def test_build_adopt_refusal_fields():
    a = _load("non_vacuous_a.sol")
    st = cc.refresh_slots(
        cc.CheckpointState(), source=a, attempt=5, compiled=True, compiled_real=True)
    fields = cc.build_adopt_refusal_fields(
        st, attempt=7, artifact_path="audit/poc/_runs/r/poc_attempts/H-01/a7_post_det.sol.txt")
    assert fields["event"] == "compile_adopt_rejected"
    assert fields["attempt"] == 7
    assert fields["restore_attempt"] == 5
    assert fields["restore_kind"] == "non_vacuous"
    assert "a7_post_det.sol.txt" in fields["artifact_path"]


def test_no_restore_target_should_not_refuse():
    assert cc.should_refuse(compiled=False, restore_target_present=False) is False


def test_should_emit_adopt_refusal_sc007():
    assert cc.should_emit_adopt_refusal(
        forge_compile_verdict_available=False,
        compiled=False,
        restore_target_present=True,
    ) is False
    assert cc.should_emit_adopt_refusal(
        forge_compile_verdict_available=True,
        compiled=False,
        restore_target_present=True,
    ) is True
    assert cc.should_emit_adopt_refusal(
        forge_compile_verdict_available=True,
        compiled=True,
        restore_target_present=True,
    ) is False


def test_sc003_working_body_after_vacuous_adopt_then_refuse():
    a = _load("non_vacuous_a.sol")
    b = _load("vacuous_b.sol")
    c = _load("noncompiling_c.sol")
    st = cc.refresh_slots(
        cc.CheckpointState(), source=a, attempt=1, compiled=True, compiled_real=True)
    working = a
    st = cc.refresh_slots(
        st, source=b, attempt=2, compiled=True, compiled_real=False)
    working = b  # successful adopt keeps adopted body
    assert cc.restore_target(st).source == a
    assert working == b
    assert cc.should_refuse(compiled=False, restore_target_present=True)
    working = cc.apply_refuse(c, st)
    assert working == a


def test_sc004_vacuous_does_not_overwrite_non_vacuous():
    a = _load("non_vacuous_a.sol")
    b = _load("vacuous_b.sol")
    st = cc.refresh_slots(
        cc.CheckpointState(), source=a, attempt=1, compiled=True, compiled_real=True)
    st = cc.refresh_slots(
        st, source=b, attempt=2, compiled=True, compiled_real=False)
    assert st.non_vacuous is not None and st.non_vacuous.source == a
    assert st.any_compile is not None and st.any_compile.source == b
    assert cc.restore_target(st).source == a


def test_sc004_vacuous_only_fallback():
    b = _load("vacuous_b.sol")
    st = cc.refresh_slots(
        cc.CheckpointState(), source=b, attempt=1, compiled=True, compiled_real=False)
    assert cc.restore_target(st).source == b
    assert cc.apply_refuse("broken", st) == b


def test_sc004_most_recent_non_vacuous_wins():
    a = _load("non_vacuous_a.sol")
    b2 = _load("non_vacuous_b.sol")
    st = cc.refresh_slots(
        cc.CheckpointState(), source=a, attempt=1, compiled=True, compiled_real=True)
    st = cc.refresh_slots(
        st, source=b2, attempt=2, compiled=True, compiled_real=True)
    assert cc.restore_target(st).source == b2


def test_should_end_restore():
    assert cc.should_end_restore(compiled=False, restore_target_present=True) is True
    assert cc.should_end_restore(compiled=True, restore_target_present=True) is False
    assert cc.should_end_restore(compiled=False, restore_target_present=False) is False


def test_end_restore_does_not_take_outcome():
    """FR-008: end-restore decision has no outcome parameter (caller keeps outcome)."""
    import inspect
    sig = inspect.signature(cc.should_end_restore)
    assert "outcome" not in sig.parameters


def test_sc004_no_end_snapback_after_vacuous_adopt():
    a = _load("non_vacuous_a.sol")
    b = _load("vacuous_b.sol")
    st = cc.refresh_slots(
        cc.CheckpointState(), source=a, attempt=1, compiled=True, compiled_real=True)
    st = cc.refresh_slots(
        st, source=b, attempt=2, compiled=True, compiled_real=False)
    working = b
    # end with compiled active → no end restore
    assert cc.should_end_restore(compiled=True, restore_target_present=True) is False
    assert working == b
    assert cc.restore_target(st).source == a
