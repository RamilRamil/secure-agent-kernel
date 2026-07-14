"""Spec 017 US2: the build wrapper degrades gracefully when graphify is absent.

No real graphify invocation, no network. The POSITIVE offline-build path (SC-002 —
graphify actually building a map with all provider creds unset) is verification-only
(hands-on this session + the quickstart smoke): it cannot run in offline CI because
graphify is intentionally not a dependency.
"""
import scripts.codegraph as cg
from scripts.codegraph import GraphifyMissing, build_graph, main


def test_build_graph_raises_typed_error_when_cli_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(cg.shutil, "which", lambda _name: None)
    try:
        build_graph(tmp_path)
    except GraphifyMissing as e:
        assert "uv tool install graphifyy" in str(e)
    else:
        raise AssertionError("expected GraphifyMissing when graphify is not on PATH")


def test_cli_build_reports_cleanly_and_exits_nonzero(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cg.shutil, "which", lambda _name: None)
    rc = main(["build", str(tmp_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "graphify" in err and "Traceback" not in err
