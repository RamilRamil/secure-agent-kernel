"""Feature 015 US2: struct/enum fields are grounded up front, before the model guesses.

`expand_referenced_types` surfaces the definitions of struct/enum types referenced by the
`callable_api` (one level of nesting) so the model constructs them with the right fields
instead of inventing them. The on-demand lookup (which already returns fields, research R2)
is unchanged.
"""
from scripts.solidity_index import SymbolIndex, expand_referenced_types

_SRC = ("interface I { enum E { X, Y } struct S { uint32 a; E e; } "
        "struct Other { uint256 z; } function f(S calldata s) external; }")


def _idx():
    return SymbolIndex.build_from_source(_SRC)


def test_referenced_struct_fields_expanded():
    g = expand_referenced_types("function f(S calldata s) external;", _idx())
    assert "struct S" in g and "uint32 a" in g          # full field list, names + types


def test_nested_type_expanded_one_level():
    g = expand_referenced_types("function f(S calldata s) external;", _idx())
    assert "enum E" in g and "X" in g                    # E is a field of S → expanded


def test_unreferenced_type_not_included():
    g = expand_referenced_types("function f(S calldata s) external;", _idx())
    assert "Other" not in g                              # not referenced by the callable_api


def test_no_index_or_empty_api_returns_empty():
    assert expand_referenced_types("function f(S s) external;", None) == ""
    assert expand_referenced_types("", _idx()) == ""


def test_budget_bounds_output():
    g = expand_referenced_types("function f(S calldata s) external;", _idx(), budget=10)
    assert len(g) <= 200  # a tiny budget stops after the first block (bounded, not unbounded)


def test_on_demand_lookup_still_returns_fields():
    # regression guard for FR-005: the lookup path is unchanged and still carries the fields.
    ms = _idx().lookup("S")
    assert ms and "uint32 a" in ms[0].definition


def test_nested_type_grounding_includes_reference_note():
    # feature 016 US2: a nested struct's grounding carries the "use Container.Type" note
    g = expand_referenced_types("function f(S calldata s) external;", _idx())
    assert "nested inside" in g and "I.S" in g
    assert "do NOT write" in g or "do not write" in g.lower()


def test_note_only_attaches_to_nested_types():
    # the note is conditional on a non-empty container: a struct in interface A gets it as
    # "A.S", never a different container's note (guards the per-type conditional wiring).
    idx = SymbolIndex.build_from_source(
        "interface A { struct SA { uint a; } function f(SA s) external; }")
    g = expand_referenced_types("function f(SA s) external;", idx)
    assert "nested inside A" in g and "A.SA" in g and "B." not in g
