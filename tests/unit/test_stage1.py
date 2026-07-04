"""Stage 1 deterministic planner tests (T054)."""
import pytest
from pathlib import Path

from sr_agent.packs.audit.planner.stage1 import (
    extract_functions,
    run_stage1,
    score_function,
)


# ── extract_functions ────────────────────────────────────────────────────────

def test_extract_skips_declarations():
    src = "contract A { function f() public { uint x = 1; } function g() external; }"
    names = [n for n, _, _ in extract_functions(src)]
    assert names == ["f"]


def test_extract_brace_matches_nested():
    src = "function f() public { if (true) { x = 1; } }"
    funcs = extract_functions(src)
    assert len(funcs) == 1
    assert "if (true)" in funcs[0][1]


# ── score_function ───────────────────────────────────────────────────────────

def test_score_detects_reentrancy_shape():
    body = 'msg.sender.call{value: amount}(""); balances[msg.sender] -= amount;'
    score, flags = score_function(body)
    assert "low_level_call_value" in flags
    assert "external_call_before_state_write" in flags
    assert score >= 10


def test_score_detects_delegatecall_and_selfdestruct():
    _, flags = score_function("target.delegatecall(data); selfdestruct(payable(x));")
    assert "delegatecall" in flags
    assert "selfdestruct" in flags


def test_score_clean_function_is_zero():
    score, flags = score_function("return a + b;")
    assert score == 0
    assert flags == []


# ── run_stage1 ───────────────────────────────────────────────────────────────

@pytest.fixture
def contracts(tmp_path: Path) -> Path:
    (tmp_path / "A.sol").write_text(
        "contract A {\n"
        "  function safe() public view returns (uint) { return 1; }\n"
        "  function risky() public { selfdestruct(payable(msg.sender)); }\n"
        "  function iface() external;\n"
        "}\n"
    )
    return tmp_path


def test_run_stage1_prioritizes_risky(contracts):
    report = run_stage1(contracts)
    assert "A.sol:risky" in report.priority_targets
    assert "A.sol:safe" in report.skipped_targets
    assert "A.sol:iface" not in report.priority_targets  # declaration


def test_run_stage1_ranks_by_score(tmp_path):
    (tmp_path / "C.sol").write_text(
        "contract C {\n"
        "  function low() public { uint t = block.timestamp; }\n"          # weight 1
        "  function high() public { target.delegatecall(data); }\n"        # weight 6
        "}\n"
    )
    report = run_stage1(tmp_path)
    assert report.priority_targets[0] == "C.sol:high"


def test_run_stage1_focus_limits_scope(contracts):
    (contracts / "B.sol").write_text("contract B { function d() public { selfdestruct(x); } }")
    report = run_stage1(contracts, focus=[Path("A.sol")])
    assert all(t.startswith("A.sol:") for t in report.priority_targets)
    assert all("B.sol" not in t for t in report.skipped_targets)


def test_run_stage1_on_example_vault():
    example = Path(__file__).resolve().parents[2] / "examples" / "vulnerable-vault"
    report = run_stage1(example)
    assert report.priority_targets[0] == "Vault.sol:withdraw"
    assert "Vault.sol:deposit" in report.skipped_targets
