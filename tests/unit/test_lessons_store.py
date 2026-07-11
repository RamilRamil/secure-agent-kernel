"""Feature 014 US1: the lesson store — dedup, human-gated promotion, tamper-evidence.

All offline; the store takes the HMAC key directly (no config needed here).
"""
from pathlib import Path

from sr_agent.memory.lessons import LessonCandidate, LessonStore, sig_id

SECRET = b"\x00" * 32
_SIG = ["Error (2904): Declaration \"TFooParams\" not found"]


def _store(tmp_path: Path) -> LessonStore:
    return LessonStore(tmp_path / "lessons", tmp_path / "knowledge", SECRET)


def _cand(sig=_SIG) -> LessonCandidate:
    return LessonCandidate.create(
        trigger_signature=list(sig), symptom="stuck importing a nested struct",
        fix="- import { TFooParams }\n+ import IFoo; IFoo.TFooParams",
        category="poc-compile", finding_id="H-01", attempt=2)


def test_sig_id_is_stable_and_order_independent():
    assert sig_id(["a", "b"]) == sig_id(["b", "a"])
    assert sig_id(["a"]) != sig_id(["b"])


def test_promote_signs_grants_authorization_preserves_origin(tmp_path):
    s = _store(tmp_path)
    c = _cand()
    s.add(c)
    lesson = s.promote(c.sig_id)
    # C1 reconciliation: promotion grants human_input authorization (Principle IV) but
    # keeps the immutable llm_inference origin (honest audit) — two distinct facts.
    assert lesson.authorization == "human_input"
    assert lesson.origin == "llm_inference"
    assert (tmp_path / "knowledge" / "lessons" / f"{c.sig_id}.md").exists()
    report = s.verify()
    assert report.per_lesson[c.sig_id] == "OK" and not report.has_invalid
    assert s.show(c.sig_id) is None  # pending removed on promote


def test_tamper_is_detected_and_dropped_at_retrieval(tmp_path):
    s = _store(tmp_path)
    c = _cand()
    s.add(c)
    s.promote(c.sig_id)
    md = tmp_path / "knowledge" / "lessons" / f"{c.sig_id}.md"
    md.write_text(md.read_text(encoding="utf-8") + "\ntampered", encoding="utf-8")
    report = s.verify()
    assert report.per_lesson[c.sig_id] == "INVALID" and report.has_invalid
    # retrieval drops a failing lesson silently (no tamper oracle)
    assert s.retrieve("TFooParams Declaration not found") == []


def test_dismiss_never_writes_the_corpus(tmp_path):
    s = _store(tmp_path)
    c = _cand()
    s.add(c)
    assert s.dismiss(c.sig_id) is True
    corpus = tmp_path / "knowledge" / "lessons"
    assert not corpus.exists() or not list(corpus.glob("*.md"))
    assert s.show(c.sig_id) is None


def test_capture_dedups_by_signature(tmp_path):
    s = _store(tmp_path)
    assert s.capture(_cand()) is True
    assert s.capture(_cand()) is False  # same signature → same sig_id → deduped
    assert len(s.list_pending()) == 1


def test_capture_skips_when_already_promoted(tmp_path):
    s = _store(tmp_path)
    c = _cand()
    s.add(c)
    s.promote(c.sig_id)
    assert s.capture(_cand()) is False  # already promoted → not re-queued
    assert s.list_pending() == []


def test_capture_is_non_blocking(tmp_path, monkeypatch):
    s = _store(tmp_path)

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _boom)
    # a write failure must be swallowed, never raised (FR-001)
    assert s.capture(_cand()) is False
