"""Feature 016 (foundational): `SymbolIndex.nested_container` — the strict determinism gate.

Returns the container ONLY for an unambiguously-nested struct/enum (one container, no
top-level match); None otherwise — so the guard/grounding/hint never touch a top-level or
ambiguous name.
"""
from scripts.solidity_index import SymbolIndex


def test_nested_struct_returns_container():
    idx = SymbolIndex.build_from_source("interface I { struct S { uint32 a; } }")
    assert idx.nested_container("S") == "I"


def test_nested_enum_returns_container():
    idx = SymbolIndex.build_from_source("interface I { enum E { X, Y } }")
    assert idx.nested_container("E") == "I"


def test_top_level_struct_returns_none():
    idx = SymbolIndex.build_from_source("struct S { uint32 a; }")
    assert idx.nested_container("S") is None


def test_unknown_name_returns_none():
    idx = SymbolIndex.build_from_source("interface I { struct S { uint32 a; } }")
    assert idx.nested_container("Bogus") is None


def test_ambiguous_two_containers_returns_none():
    idx = SymbolIndex.build_from_source(
        "interface A { struct S { uint32 a; } } interface B { struct S { uint256 b; } }")
    assert idx.nested_container("S") is None  # two distinct containers → conservative None


# NOTE: file-level (top-level) structs/enums are not indexed by the parser (a pre-existing
# spec-007 limitation — `_index_file` walks only contract/interface bodies), so a
# top-level-vs-nested name collision cannot arise from the index today. `nested_container`
# still defensively excludes a top-level match (`any(not m.contract)`) in case that indexing
# is added later; the realistic ambiguity (two nested containers) is covered above.
