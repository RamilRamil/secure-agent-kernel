"""On-chain tool tests (T060). No web3, no network — injected fake fetcher."""
import pytest

from sr_agent.tools.onchain import (
    DecompilationResult,
    OnChainError,
    analyze_transactions,
    decompile_bytecode,
    make_alchemy_fetcher,
)

_ADDR = "0x" + "ab" * 20


def _fake_fetcher(txs):
    return lambda address, fb, tb: txs


# ── analyze_transactions ─────────────────────────────────────────────────────

def test_analyze_summarizes_txs():
    txs = [
        {"hash": "0x1", "from": "0xcafe", "to": _ADDR, "value": 5,
         "input": "0xdeadbeef", "method": "withdraw"},
    ]
    res = analyze_transactions(_ADDR, 100, 200, _fake_fetcher(txs))
    assert res.tx_count == 1
    assert any("value transfer" in n for n in res.notes)
    assert any("withdraw" in n for n in res.notes)


def test_invalid_address_rejected():
    with pytest.raises(OnChainError, match="invalid address"):
        analyze_transactions("0x123", 1, 2, _fake_fetcher([]))


def test_block_range_capped():
    with pytest.raises(OnChainError, match="exceeds limit"):
        analyze_transactions(_ADDR, 0, 10_001, _fake_fetcher([]))


def test_reversed_range_rejected():
    with pytest.raises(OnChainError, match="< from_block"):
        analyze_transactions(_ADDR, 200, 100, _fake_fetcher([]))


def test_focus_filters_methods():
    txs = [
        {"hash": "0x1", "to": _ADDR, "input": "0xaa", "method": "withdraw"},
        {"hash": "0x2", "to": _ADDR, "input": "0xbb", "method": "deposit"},
    ]
    res = analyze_transactions(_ADDR, 1, 2, _fake_fetcher(txs), focus=["withdraw"])
    assert all("deposit" not in n for n in res.notes)
    assert any("withdraw" in n for n in res.notes)


def test_empty_fetch_ok():
    res = analyze_transactions(_ADDR, 1, 2, _fake_fetcher([]))
    assert res.tx_count == 0 and res.notes == []


# ── make_alchemy_fetcher ─────────────────────────────────────────────────────

def test_alchemy_fetcher_requires_key():
    with pytest.raises(OnChainError, match="no ALCHEMY_API_KEY"):
        make_alchemy_fetcher("")


# ── decompile_bytecode ───────────────────────────────────────────────────────

def test_decompile_dry_run():
    res = decompile_bytecode(_ADDR)
    assert not res.success
    assert "dry run" in res.output


def test_decompile_unsupported_tool():
    with pytest.raises(OnChainError, match="unsupported decompiler"):
        decompile_bytecode(_ADDR, tool="magic")


def test_decompile_uses_runner():
    res = decompile_bytecode(
        _ADDR, tool="heimdall",
        runner=lambda a, t: DecompilationResult(a, t, True, "contract Foo {}"),
    )
    assert res.success and "contract Foo" in res.output


def test_decompile_invalid_address():
    with pytest.raises(OnChainError, match="invalid address"):
        decompile_bytecode("nope")
