"""Progress stream tests (T061)."""
import io

from sr_agent.io.progress import ProgressEvent, ProgressStream, silent


def test_emit_writes_label_and_detail():
    buf = io.StringIO()
    ProgressStream(stream=buf).emit(ProgressEvent.stage1_done, "5 targets")
    out = buf.getvalue()
    assert "Stage 1 complete" in out
    assert "5 targets" in out


def test_emit_with_counter():
    buf = io.StringIO()
    ProgressStream(stream=buf).emit(ProgressEvent.stage2_emit, "Vault.sol", current=1, total=3)
    assert "[1/3]" in buf.getvalue()


def test_disabled_emits_nothing():
    buf = io.StringIO()
    ProgressStream(stream=buf, enabled=False).emit(ProgressEvent.report, "x")
    assert buf.getvalue() == ""


def test_silent_helper_is_disabled():
    assert silent().enabled is False
