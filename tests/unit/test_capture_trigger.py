"""Feature 015 US3: lesson capture fires only on a genuinely-better verdict.

`_maybe_capture_lesson` must capture on a stuck→compiled (or →real_pass) transition, and
must NOT capture when the model merely regresses into a different error (which also makes the
prior signature disappear) or produces a vacuous pass. Prevents the false-positive lesson the
first live H-01 run recorded.
"""
from pathlib import Path

import scripts.poc_queue_runner as pqr
from sr_agent.memory.lessons import LessonStore

SECRET = b"\x00" * 32


def _store(tmp_path: Path) -> LessonStore:
    return LessonStore(tmp_path / "lessons", tmp_path / "knowledge", SECRET)


def _capture(store, **kw):
    events: list[dict] = []
    defaults = dict(prev_error_sig=None, error_sig=(), prev_fail_sig=None,
                    real_pass=False, compiled=False, prev_symptom="err",
                    prev_code="contract A {}", code="contract B {}")
    defaults.update(kw)
    pqr._maybe_capture_lesson(store, events.append, "H-01", 2, **defaults)
    return store.list_pending(), [e["event"] for e in events]


def test_stuck_then_compiled_captures_one(tmp_path):
    # prev attempt stuck on a compile error; this attempt COMPILED it away
    pending, names = _capture(_store(tmp_path),
                              prev_error_sig=("Undeclared identifier.",), error_sig=(),
                              compiled=True)
    assert len(pending) == 1 and "lesson_captured" in names
    assert pending[0].category == "poc-compile"


def test_stuck_then_different_error_captures_none(tmp_path):
    # regression/lateral: prev signature gone, but a NEW error appeared and it did NOT compile
    pending, names = _capture(_store(tmp_path),
                              prev_error_sig=("Undeclared identifier.",),
                              error_sig=("Expected ';' but got identifier",),
                              compiled=False)
    assert pending == [] and "lesson_captured" not in names


def test_stuck_then_vacuous_pass_captures_none(tmp_path):
    # signature cleared but the attempt did not actually compile (vacuous) → no capture
    pending, _ = _capture(_store(tmp_path),
                          prev_error_sig=("Undeclared identifier.",), error_sig=(),
                          compiled=False, real_pass=False)
    assert pending == []


def test_runtime_failure_resolved_captures_one(tmp_path):
    pending, _ = _capture(_store(tmp_path),
                          prev_fail_sig=("revert: cooldown active",), real_pass=True,
                          compiled=True)
    assert len(pending) == 1 and pending[0].category == "poc-runtime"


def test_dedup_holds_on_recurring_signature(tmp_path):
    store = _store(tmp_path)
    kw = dict(prev_error_sig=("Undeclared identifier.",), error_sig=(), compiled=True)
    _capture(store, **kw)
    _capture(store, **kw)  # same signature resolved again
    assert len(store.list_pending()) == 1
