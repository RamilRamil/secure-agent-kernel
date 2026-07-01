"""On-chain analysis tools (T060).

analyze_transactions pulls recent transactions/calldata for a deployed contract
so the agent can reason about live behaviour. The heavy dependency (web3 +
Alchemy archive node) is lazy-imported behind an injectable fetcher, so this
module imports and unit-tests without web3, and the live path auto-skips when
no ALCHEMY_API_KEY is configured — the same best-effort pattern as Slither.

All fetched calldata is DATA: the caller wraps it in [DATA START]..[DATA END]
and stores it as source_type=tool_output. Nothing here is executed.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)

MAX_BLOCKS = 10_000
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# A fetcher takes (address, from_block, to_block) and returns raw tx dicts.
TxFetcher = Callable[[str, int, int], list[dict]]


class OnChainError(Exception):
    pass


@dataclass
class TransactionAnalysis:
    address: str
    from_block: int
    to_block: int
    tx_count: int
    notes: list[str] = field(default_factory=list)
    transactions: list[dict] = field(default_factory=list)


def _valid_address(address: str) -> bool:
    return bool(_ADDRESS_RE.match(address or ""))


def analyze_transactions(
    address: str,
    from_block: int,
    to_block: int,
    fetcher: TxFetcher,
    focus: list[str] | None = None,
) -> TransactionAnalysis:
    """Analyze transactions to a contract over a bounded block range.

    Enforces the same deterministic guards as validate_action: a well-formed
    address and a block span capped at MAX_BLOCKS (resource limit).
    """
    if not _valid_address(address):
        raise OnChainError(f"invalid address: {address!r}")
    if to_block < from_block:
        raise OnChainError(f"to_block {to_block} < from_block {from_block}")
    span = to_block - from_block
    if span > MAX_BLOCKS:
        raise OnChainError(f"block range {span} exceeds limit {MAX_BLOCKS}")

    txs = fetcher(address, from_block, to_block) or []
    focus_set = {f.lower() for f in (focus or [])}

    notes: list[str] = []
    for tx in txs:
        selector = str(tx.get("input", ""))[:10]
        method = tx.get("method", "")
        if focus_set and method and method.lower() not in focus_set:
            continue
        value = tx.get("value", 0)
        if value and int(value) > 0:
            notes.append(f"value transfer {value} in {tx.get('hash', '?')} ({method or selector})")
        if selector and selector != "0x":
            notes.append(f"call {method or selector} from {tx.get('from', '?')}")

    logger.info("analyze_transactions %s [%d-%d]: %d txs", address, from_block, to_block, len(txs))
    return TransactionAnalysis(
        address=address, from_block=from_block, to_block=to_block,
        tx_count=len(txs), notes=notes, transactions=txs,
    )


def make_alchemy_fetcher(api_key: str, network: str = "eth-mainnet") -> TxFetcher:
    """Build a live fetcher backed by an Alchemy archive node (lazy web3 import).

    Raises OnChainError if no key is configured or web3 is unavailable, so the
    caller can auto-skip.
    """
    if not api_key:
        raise OnChainError("no ALCHEMY_API_KEY configured")
    try:
        from web3 import Web3  # lazy — module stays importable without web3
    except Exception as e:  # pragma: no cover - depends on env
        raise OnChainError(f"web3 unavailable: {e}") from e

    url = f"https://{network}.g.alchemy.com/v2/{api_key}"
    w3 = Web3(Web3.HTTPProvider(url))

    def fetch(address: str, from_block: int, to_block: int) -> list[dict]:  # pragma: no cover
        out: list[dict] = []
        target = address.lower()
        for bn in range(from_block, to_block + 1):
            block = w3.eth.get_block(bn, full_transactions=True)
            for tx in block.transactions:
                to = (tx.get("to") or "").lower()
                if to == target:
                    out.append({
                        "hash": tx.get("hash").hex() if tx.get("hash") else "",
                        "from": tx.get("from", ""),
                        "to": tx.get("to", ""),
                        "value": int(tx.get("value", 0)),
                        "input": tx.get("input", ""),
                        "block": bn,
                    })
        return out

    return fetch


@dataclass
class DecompilationResult:
    address: str
    tool: str
    success: bool
    output: str = ""


def decompile_bytecode(
    address: str,
    tool: str = "heimdall",
    runner: Callable[[str, str], DecompilationResult] | None = None,
) -> DecompilationResult:
    """Decompile a deployed contract's bytecode via an external decompiler.

    The decompiler (Heimdall/Panoramix) runs through an injectable runner
    (Docker in production). Without a runner this is a dry run — structure only.
    """
    if not _valid_address(address):
        raise OnChainError(f"invalid address: {address!r}")
    if tool not in ("heimdall", "panoramix"):
        raise OnChainError(f"unsupported decompiler: {tool!r}")
    if runner is None:
        return DecompilationResult(address=address, tool=tool, success=False,
                                   output="no decompiler runner configured (dry run)")
    return runner(address, tool)
