"""build_file_manifest / build_callable_api: AST-backed grounding (feature 007 T020)
+ the per-name budget fairness / location-priority fix (2026-07-05).

Mirrors the exact motivating incident from the live H-01 run: with several
contract names in one finding's `location`, an earlier name's own budget-hungry
block previously starved a later name out of the prompt entirely, and even after
giving each name its own share, the actual finding-target function could still be
truncated out if declared after other functions in the same file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import scripts.poc_queue_runner as pqr
from scripts.solidity_index import SymbolIndex

FIRST_SRC = """
pragma solidity ^0.8.28;

contract FooCDO {
    function totalAssets(address tranche) external view returns (uint256) {}
    function totalStrategyAssets() external view returns (uint256) {}
    function pricePerShare(address tranche) external view returns (uint256) {}
    function maxDeposit(address tranche) external view returns (uint256) {}
    function coverage() external view returns (uint32) {}
}
"""

SECOND_SRC = """
pragma solidity ^0.8.28;

contract Foo {
    function requestRedeem(address vault, address token, address from, address to,
                            uint256 shares, uint256 fee, uint32 cooldownSeconds)
        external onlyRole(0) {}
    function finalize(address vault, address user) external returns (uint256) {}
    function cancel(address vault, address user, uint256 i) external onlyUser(user) {}
}
"""


@pytest.fixture
def two_contract_project(tmp_path: Path) -> Path:
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "FooCDO.sol").write_text(FIRST_SRC, encoding="utf-8")
    (tmp_path / "contracts" / "Foo.sol").write_text(SECOND_SRC, encoding="utf-8")
    return tmp_path


LOCATION = "FooCDO.coverage / calculateFooMode + Foo.cancel"


@pytest.mark.parametrize("use_ast", [True, False])
def test_each_name_gets_its_own_budget_share(two_contract_project, monkeypatch, use_ast):
    """The original bug: FooCDO (first name) exhausting a SHARED budget meant
    Foo (second name) never got a block at all. Each name must get its
    own share so both survive a tight budget."""
    monkeypatch.setattr(pqr, "CALLABLE_API_BUDGET", 250)
    symbol_index = SymbolIndex.build(two_contract_project) if use_ast else None
    capi = pqr.build_callable_api(two_contract_project, LOCATION, symbol_index)
    assert "// FooCDO" in capi
    assert "// Foo" in capi


@pytest.mark.parametrize("use_ast", [True, False])
def test_location_named_function_survives_truncation(two_contract_project, monkeypatch, use_ast):
    """`cancel` is declared LAST in Foo.sol, after two other external
    functions — under a tight per-file budget it would be the first one truncated
    out. Since `location` names it explicitly, it must be rendered first and
    survive, along with its onlyUser(user) CALLER REQUIREMENT annotation."""
    monkeypatch.setattr(pqr, "CALLABLE_API_BUDGET", 250)
    symbol_index = SymbolIndex.build(two_contract_project) if use_ast else None
    capi = pqr.build_callable_api(two_contract_project, LOCATION, symbol_index)
    assert "function cancel(" in capi
    assert "onlyUser(user)" in capi


def test_file_manifest_uses_real_contract_names(two_contract_project):
    """Feature 007 T020: file map names come from the parsed AST, not the
    filename — verified here on a project where they happen to match, and against
    the real target (see test_solidity_index.py) where they don't."""
    idx = SymbolIndex.build(two_contract_project)
    fm = pqr.build_file_manifest(two_contract_project, idx)
    assert "FooCDO:" in fm
    assert "Foo:" in fm
