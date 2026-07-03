"""State Interference Graph tests (T053)."""
import pytest
from pathlib import Path

from sr_agent.packs.audit.planner.sig import (
    build_sig,
    extract_state_vars,
    get_filtered_pairs,
)

_BANK = """
contract Bank {
    mapping(address => uint256) public balances;
    uint256 public total;
    address owner;

    function deposit() public { balances[msg.sender] += msg.value; total += msg.value; }
    function withdraw(uint256 a) public { msg.sender.call{value: a}(""); balances[msg.sender] -= a; }
    function setOwner(address o) public { owner = o; }
    function getTotal() public view returns (uint256) { return total; }
}
"""


def test_extract_state_vars():
    assert extract_state_vars(_BANK) == {"balances", "total", "owner"}


def test_function_read_write_sets():
    sig = build_sig(_BANK)
    assert sig.functions["deposit"].writes == {"balances", "total"}
    assert sig.functions["withdraw"].writes == {"balances"}
    assert sig.functions["withdraw"].has_external_call
    assert sig.functions["getTotal"].reads == {"total"}
    assert sig.functions["getTotal"].writes == set()


def test_interferes_on_shared_state():
    sig = build_sig(_BANK)
    assert sig.interferes("deposit", "withdraw")   # share balances
    assert sig.interferes("deposit", "getTotal")   # share total
    assert not sig.interferes("setOwner", "getTotal")  # owner vs total


def test_can_reenter_requires_external_call():
    sig = build_sig(_BANK)
    assert sig.can_reenter("withdraw", "deposit")   # withdraw calls out + shares state
    assert not sig.can_reenter("deposit", "withdraw")  # deposit makes no external call


def test_get_filtered_pairs():
    sig = build_sig(_BANK)
    locs = ["Bank.sol:deposit", "Bank.sol:withdraw", "Bank.sol:setOwner"]
    pairs = get_filtered_pairs(locs, sig)
    assert ("Bank.sol:deposit", "Bank.sol:withdraw") in pairs
    assert all("setOwner" not in a and "setOwner" not in b for a, b in pairs)


def test_unknown_function_does_not_interfere():
    sig = build_sig(_BANK)
    assert not sig.interferes("ghost", "deposit")


def test_on_example_vault():
    root = Path(__file__).resolve().parents[2] / "examples" / "vulnerable-vault"
    sig = build_sig((root / "Vault.sol").read_text())
    assert sig.can_reenter("withdraw", "deposit")
    pairs = get_filtered_pairs(
        ["Vault.sol:withdraw", "Vault.sol:deposit", "Vault.sol:totalBalance"], sig
    )
    assert ("Vault.sol:withdraw", "Vault.sol:deposit") in pairs


# ── build_sig_from_smartgraphical (feature 002, US2) ─────────────────────────

def _sg_graph():
    import json
    from pathlib import Path
    fx = Path(__file__).resolve().parents[1] / "fixtures" / "smartgraphical" / "sample_graph.json"
    return json.load(fx.open())


def test_sg_sig_reads_state_write_edge():
    from sr_agent.packs.audit.planner.sig import build_sig_from_smartgraphical
    sig = build_sig_from_smartgraphical(_sg_graph())
    assert "balances" in sig.functions["_credit"].writes


def test_sg_sig_propagates_state_through_call():
    from sr_agent.packs.audit.planner.sig import build_sig_from_smartgraphical
    sig = build_sig_from_smartgraphical(_sg_graph())
    # deposit calls _credit (cross_type_call) -> inherits its write to balances
    assert "balances" in sig.functions["deposit"].writes


def test_sg_sig_detects_cross_inheritance_interference():
    from sr_agent.packs.audit.planner.sig import build_sig_from_smartgraphical
    sig = build_sig_from_smartgraphical(_sg_graph())
    # deposit (Vault) and _credit (Base) share balances via the inherited call
    assert sig.interferes("deposit", "_credit")


def test_regex_sig_misses_what_sg_catches():
    """Contrast: the single-file regex SIG does NOT link deposit and _credit."""
    from pathlib import Path
    from sr_agent.packs.audit.planner.sig import build_sig
    root = Path(__file__).resolve().parents[2] / "examples" / "inheritance-vault"
    regex_sig = build_sig((root / "Vault.sol").read_text())
    # deposit's body is just `_credit(...)` — no `balances` token, so regex sees
    # no shared state with _credit.
    assert not regex_sig.interferes("deposit", "_credit")
