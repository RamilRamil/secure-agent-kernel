"""Kernel-generic read-only tool tests (read_file, search_code)."""
import pytest
from pathlib import Path

from sr_agent.tools.readonly import (
    ReadOnlyToolError,
    SearchHit,
    read_file,
    search_code,
)


@pytest.fixture
def scope_root(tmp_path: Path) -> Path:
    (tmp_path / "Vault.sol").write_text(
        "function withdraw(uint256 amount) external {\n"
        "    msg.sender.call{value: amount}(\"\");\n"
        "}\n"
    )
    sub = tmp_path / "lib"
    sub.mkdir()
    (sub / "Token.sol").write_text("function transfer() public {}\n")
    return tmp_path


# ── read_file ────────────────────────────────────────────────────────────────

def test_read_file_returns_content(scope_root):
    content = read_file(scope_root / "Vault.sol", scope_root)
    assert "withdraw" in content


def test_read_file_rejects_traversal(scope_root):
    with pytest.raises(ReadOnlyToolError, match="escapes scope root"):
        read_file(scope_root / ".." / ".." / "etc" / "passwd", scope_root)


def test_read_file_rejects_directory(scope_root):
    with pytest.raises(ReadOnlyToolError, match="Not a file"):
        read_file(scope_root / "lib", scope_root)


def test_read_file_rejects_oversize(scope_root, monkeypatch):
    import sr_agent.tools.readonly as ro
    monkeypatch.setattr(ro, "MAX_FILE_BYTES", 10)
    with pytest.raises(ReadOnlyToolError, match="too large"):
        read_file(scope_root / "Vault.sol", scope_root)


# ── search_code ──────────────────────────────────────────────────────────────
# `file_ext` is a REQUIRED parameter (D6): the caller/pack chooses the filter; the
# kernel bakes in no domain default. The `.sol` filter below is synthetic TEST
# data, not a kernel assumption.

def test_search_finds_pattern_across_tree(scope_root):
    hits = search_code("function", scope_root, file_ext=".sol")
    files = {h.file for h in hits}
    assert "Vault.sol" in files
    assert str(Path("lib") / "Token.sol") in files


def test_search_returns_line_numbers(scope_root):
    hits = search_code("withdraw", scope_root, file_ext=".sol")
    assert len(hits) == 1
    assert hits[0].file == "Vault.sol"
    assert hits[0].line == 1


def test_search_no_match_returns_empty(scope_root):
    assert search_code("selfdestruct", scope_root, file_ext=".sol") == []


def test_search_respects_max_hits(scope_root):
    hits = search_code("function", scope_root, file_ext=".sol", max_hits=1)
    assert len(hits) == 1


def test_search_missing_root_raises(tmp_path):
    with pytest.raises(ReadOnlyToolError, match="does not exist"):
        search_code("x", tmp_path / "nope", file_ext=".sol")


def test_search_missing_file_ext_fails_loud(scope_root):
    """A call-site that forgets file_ext fails with TypeError, never a silent
    scope change (D6 — no default extension)."""
    with pytest.raises(TypeError):
        search_code("function", scope_root)  # type: ignore[call-arg]


# NOTE (feature 048): the real example-contract search test moved to
# tests/audit/unit/test_readonly_example.py — examples/vulnerable-vault is audit content
# (Repo B), not in the kernel carve. Kernel search_code coverage above uses synthetic
# fixtures only.
