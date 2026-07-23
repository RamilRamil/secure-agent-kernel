"""Feature 016 US1: `_fix_nested_type_imports` deterministically fixes a nested named-import.

Removes the nested named-import, ensures the container is imported, AND rewrites the type's
bare uses to `Container.Type` (required — the model uses these types bare, so fixing only the
import would leave an undefined identifier). Touches only unambiguously-nested names.
"""
from scripts.solidity_fixers import _fix_nested_type_imports
from scripts.solidity_index import SymbolIndex

_SRC = "interface I { struct S { uint32 a; } enum E { X } function f(S calldata s) external; }"
_FM = "I: ../../contracts/I.sol"


def _idx():
    return SymbolIndex.build_from_source(_SRC)


def test_nested_import_with_bare_use_is_rewritten():
    poc = ('import { I } from "../../contracts/I.sol";\n'
           'import { S } from "../../contracts/I.sol";\n'
           'contract P { function t() public { S memory x = S({a: 1}); } }')
    out, changed = _fix_nested_type_imports(poc, _idx(), _FM)
    assert changed
    assert "import { S }" not in out                 # nested named-import removed
    assert "import { I }" in out                     # container kept
    assert "I.S memory x = I.S({a: 1})" in out       # bare uses rewritten → compiles


def test_container_added_when_absent():
    poc = ('import { S } from "../../contracts/I.sol";\n'
           'contract P { S s; }')
    out, changed = _fix_nested_type_imports(poc, _idx(), _FM)
    assert changed
    assert 'import { I } from "../../contracts/I.sol";' in out  # container added
    assert "import { S }" not in out and "I.S s;" in out


def test_mixed_line_keeps_top_level_removes_nested():
    idx = SymbolIndex.build_from_source(
        "struct Top { uint a; } interface I { struct S { uint32 a; } }")
    poc = 'import { Top, S } from "x.sol";\ncontract P {}'
    out, changed = _fix_nested_type_imports(poc, idx, "I: i.sol")
    assert changed
    assert "Top" in out and "import { S }" not in out
    assert "{ Top }" in out or "{ Top," in out       # top-level name preserved in the import


def test_top_level_only_import_unchanged():
    idx = SymbolIndex.build_from_source("struct Top { uint a; }")
    poc = 'import { Top } from "x.sol";\ncontract P { Top t; }'
    assert _fix_nested_type_imports(poc, idx, "") == (poc, False)


def test_library_import_untouched():
    poc = 'import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";\ncontract P {}'
    assert _fix_nested_type_imports(poc, _idx(), _FM) == (poc, False)


def test_aliased_import_left_unchanged():
    poc = 'import { S as Shares } from "../../contracts/I.sol";\ncontract P {}'
    out, changed = _fix_nested_type_imports(poc, _idx(), _FM)
    assert changed is False and out == poc


def test_idempotent():
    poc = ('import { I } from "i.sol";\nimport { S } from "i.sol";\n'
           'contract P { S s; }')
    out, _ = _fix_nested_type_imports(poc, _idx(), _FM)
    out2, changed2 = _fix_nested_type_imports(out, _idx(), _FM)
    assert changed2 is False and out2 == out


def test_already_qualified_use_untouched():
    # a body that already writes I.S must not become I.I.S
    poc = 'import { S } from "i.sol";\nimport { I } from "i.sol";\ncontract P { I.S s; }'
    out, _ = _fix_nested_type_imports(poc, _idx(), _FM)
    assert "I.I.S" not in out and "I.S s;" in out


def test_none_index_is_noop():
    poc = 'import { S } from "i.sol";\ncontract P {}'
    assert _fix_nested_type_imports(poc, None, _FM) == (poc, False)
