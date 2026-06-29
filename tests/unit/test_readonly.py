"""Read-only tool tests (T049)."""
import pytest
from pathlib import Path

from sr_agent.tools.readonly import (
    ReadOnlyToolError,
    SearchHit,
    read_file,
    search_code,
)


@pytest.fixture
def audit_root(tmp_path: Path) -> Path:
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

def test_read_file_returns_content(audit_root):
    content = read_file(audit_root / "Vault.sol", audit_root)
    assert "withdraw" in content


def test_read_file_rejects_traversal(audit_root):
    with pytest.raises(ReadOnlyToolError, match="escapes audit root"):
        read_file(audit_root / ".." / ".." / "etc" / "passwd", audit_root)


def test_read_file_rejects_directory(audit_root):
    with pytest.raises(ReadOnlyToolError, match="Not a file"):
        read_file(audit_root / "lib", audit_root)


def test_read_file_rejects_oversize(audit_root, monkeypatch):
    import sr_agent.tools.readonly as ro
    monkeypatch.setattr(ro, "MAX_FILE_BYTES", 10)
    with pytest.raises(ReadOnlyToolError, match="too large"):
        read_file(audit_root / "Vault.sol", audit_root)


# ── search_code ──────────────────────────────────────────────────────────────

def test_search_finds_pattern_across_tree(audit_root):
    hits = search_code("function", audit_root)
    files = {h.file for h in hits}
    assert "Vault.sol" in files
    assert str(Path("lib") / "Token.sol") in files


def test_search_returns_line_numbers(audit_root):
    hits = search_code("withdraw", audit_root)
    assert len(hits) == 1
    assert hits[0].file == "Vault.sol"
    assert hits[0].line == 1


def test_search_no_match_returns_empty(audit_root):
    assert search_code("selfdestruct", audit_root) == []


def test_search_respects_max_hits(audit_root):
    hits = search_code("function", audit_root, max_hits=1)
    assert len(hits) == 1


def test_search_missing_root_raises(tmp_path):
    with pytest.raises(ReadOnlyToolError, match="does not exist"):
        search_code("x", tmp_path / "nope")


# ── example contract is searchable ───────────────────────────────────────────

def test_example_vault_has_reentrancy_shape():
    example = Path(__file__).resolve().parents[2] / "examples" / "vulnerable-vault"
    hits = search_code("call{value:", example)
    assert any(h.file == "Vault.sol" for h in hits)
