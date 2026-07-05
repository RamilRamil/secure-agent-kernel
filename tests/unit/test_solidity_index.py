"""SymbolIndex: AST-grounded lookup for PoC drafting (feature 007).

Validates against REAL target fixtures under a synthetic Foundry-style project
(no network, no model). Mirrors the exact motivating incidents from this session:
the invented `TBalanceState.shares` field, and the regex-based `callable_api`
dedup collision that silently dropped a function's modifier annotation when
another function shared the identical modifier text.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.solidity_index import SymbolIndex

FIXTURE_SRC = """
pragma solidity ^0.8.28;

interface ICooldown {
    struct TBalanceState {
        uint256 pending;
        uint256 claimable;
        uint256 nextUnlockAt;
        uint256 nextUnlockAmount;
        uint256 totalRequests;
    }

    function cancel(address vault, address user, uint256 i) external;
}

contract Foo {
    function cancel(address vault, address user, uint256 i) external onlyUser(user) {}
    function finalize(address vault, address user) external onlyUser(user) {}
    function balanceOf(address vault, address user) external view returns (uint256) {}

    enum ExitMode { Instant, Cooldown, Blocked }
}
"""

BROKEN_SRC = """
pragma solidity ^0.8.28;
contract Broken {
    mapping(address vault => uint256 fee) public fees; // named mapping keys: unsupported syntax
    function cancel(address vault) external onlyUser(vault) { this is not valid solidity )
}
"""


@pytest.fixture
def fixture_project(tmp_path: Path) -> Path:
    (tmp_path / "Cooldown.sol").write_text(FIXTURE_SRC, encoding="utf-8")
    return tmp_path


def test_struct_fields_real(fixture_project):
    """The exact motivating case: a struct's REAL fields, no invented `shares`."""
    idx = SymbolIndex.build(fixture_project)
    matches = idx.lookup("TBalanceState")
    assert matches
    definition = matches[0].definition
    assert "shares" not in definition
    for real_field in ("pending", "claimable", "nextUnlockAt", "nextUnlockAmount", "totalRequests"):
        assert real_field in definition


def test_not_found_never_fabricated(fixture_project):
    idx = SymbolIndex.build(fixture_project)
    assert idx.lookup("TotallyMadeUpStructName") == []


def test_qualified_name_falls_back_to_bare(fixture_project):
    """Live H-01 run (2026-07-05): the model asked `IFoo.TFooGuard`
    and got resolved=False even though the bare symbol genuinely exists — the
    index is keyed on bare names, so a qualified query must fall back to the
    bare suffix rather than report a false not-found."""
    idx = SymbolIndex.build(fixture_project)
    bare = idx.lookup("TBalanceState")
    qualified = idx.lookup("ICooldown.TBalanceState")
    assert qualified == bare
    assert idx.lookup("ICooldown.TotallyMadeUpStructName") == []


def test_function_signature_and_modifiers(fixture_project):
    idx = SymbolIndex.build(fixture_project)
    matches = idx.lookup("cancel")
    # both the interface (no modifier) and the contract impl (onlyUser) must resolve —
    # never silently pick one (research.md R3).
    assert len(matches) == 2
    with_mod = [m for m in matches if m.modifiers]
    assert with_mod and with_mod[0].modifiers == ("onlyUser(user)",)


def test_enum_values(fixture_project):
    idx = SymbolIndex.build(fixture_project)
    matches = idx.lookup("ExitMode")
    assert matches
    for value in ("Instant", "Cooldown", "Blocked"):
        assert value in matches[0].definition


def test_shared_modifier_no_collision(fixture_project):
    """The exact regex-dedup bug class this feature closes at the root (spec 006/007):
    two functions sharing an identical modifier must BOTH resolve independently —
    neither's information lost because their rendered annotation text collided."""
    idx = SymbolIndex.build(fixture_project)
    cancel = idx.lookup("cancel")
    finalize = idx.lookup("finalize")
    cancel_impl = [m for m in cancel if m.contract == "Foo"][0]
    finalize_impl = [m for m in finalize if m.contract == "Foo"][0]
    assert cancel_impl.modifiers == finalize_impl.modifiers == ("onlyUser(user)",)
    assert cancel_impl.name != finalize_impl.name  # distinct symbols, not merged/dropped


def test_functions_in_file_matches_visibility(fixture_project):
    """Feature 007 T020: replatforms poc_queue_runner.py's callable_api regex scan
    onto the AST index — external/public functions must be distinguishable by real
    grammar, not a hand-rolled keyword regex."""
    idx = SymbolIndex.build(fixture_project)
    funcs = idx.functions_in_file(fixture_project / "Cooldown.sol")
    names = {f.name: f for f in funcs}
    assert names["cancel"].visibility == "external"
    assert names["cancel"].modifiers == ("onlyUser(user)",)
    assert names["balanceOf"].visibility == "external"


def test_top_level_symbols_gives_real_name_file_pairs(fixture_project):
    """Feature 007 T020: replatforms poc_queue_runner.py's file-map (previously
    `p.stem`, which silently assumes the filename matches the contract name)."""
    idx = SymbolIndex.build(fixture_project)
    names = {s.name for s in idx.top_level_symbols()}
    assert {"ICooldown", "Foo"} <= names


def test_unparseable_file_degrades_gracefully(tmp_path):
    (tmp_path / "Cooldown.sol").write_text(FIXTURE_SRC, encoding="utf-8")
    (tmp_path / "Broken.sol").write_text(BROKEN_SRC, encoding="utf-8")
    idx = SymbolIndex.build(tmp_path)
    # the good file's symbols are still indexed despite the broken one:
    assert idx.lookup("TBalanceState")
    assert idx.lookup("cancel")
    # the broken file is recorded, build() did not raise:
    assert any(p.name == "Broken.sol" for p in idx.unparsed_files)


def test_against_real_target_project():
    """Non-synthetic sanity check against this session's actual target project, if
    present on this machine — skipped elsewhere (e.g. CI) where it isn't."""
    real = Path("/Users/ramilmustafin/Projects/Contests/2026-06-foo-bb/contracts")
    if not real.is_dir():
        pytest.skip("external target project not present on this machine")
    idx = SymbolIndex.build(real)
    matches = idx.lookup("TBalanceState")
    assert matches
    assert "shares" not in matches[0].definition
    assert "pending" in matches[0].definition
