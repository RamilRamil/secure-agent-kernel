"""Feature 016 US3: `_targeted_hints` emits an authoritative fix for a nested-type Error 2904.

`Declaration "X" not found …` where the index knows X as nested → an authoritative
`Container.X` hint; the same for an unknown/invented name → no nested-type hint (no misleading
advice).
"""
from scripts.poc_queue_runner import _targeted_hints
from scripts.solidity_index import SymbolIndex

_IDX = SymbolIndex.build_from_source("interface I { struct S { uint32 a; } }")
_ERR = 'Error (2904): Declaration "S" not found in "I.sol" (referenced as "../../I.sol")'


def test_nested_declaration_gets_authoritative_hint():
    h = _targeted_hints(_ERR, callable_api="", file_map="", code="", symbol_index=_IDX)
    assert "nested" in h.lower() and "I.S" in h and "`I`" in h


def test_unknown_declaration_gets_no_nested_hint():
    h = _targeted_hints('Error (2904): Declaration "Bogus" not found', "", "", "", _IDX)
    assert "Bogus" not in h  # no nested-type hint for a name the index doesn't know as nested


def test_no_index_no_nested_hint():
    # without an index the 2904 rule is inert (back-compat with older callers)
    h = _targeted_hints(_ERR, "", "", "")
    assert "I.S" not in h
