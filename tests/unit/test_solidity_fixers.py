"""Feature 033 - characterization tests for the five named transform-application
sequence-functions (FR-005). Each pins ONE site's exact sequence output over a fixed
SYNTHETIC fixture (invented names only - no target material), so a future unification
(spec 034) cannot silently change a sequence.

These are the LASTING guardrail: green on the pre-extraction tree (the functions are
extracted in commit 1), and still green after the fixers move to `solidity_fixers`
(commit 4). The temporary differential test (test_fixer_extraction_diff.py) additionally
proves each function equals the REAL loop's inline output; it is removed in commit 2.
"""
from __future__ import annotations

import scripts.poc_queue_runner as pqr
import scripts.solidity_fixers as sf
from scripts.solidity_index import SymbolIndex

# A bare SPDX line (missing its `//`) - `_fix_import_paths` repairs it independently of
# base_dir, so it deterministically pins that import_paths RAN in a sequence.
_BARE_SPDX = "SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"


def _undeclared_block(name: str, code: str = "7576") -> str:
    """A SYNTHETIC forge 7576 block with `name` under the caret (real forge shape)."""
    prefix = "        uint256 z = "
    col = len(prefix)
    return (f"Error ({code}): Undeclared identifier.\n  --> audit/poc/p.t.sol:9:{col + 1}:\n   |\n"
            f"9 | {prefix}{name};\n  | {' ' * col}{'^' * len(name)}\n")


# ── synthesis pre-write: import_paths(base_dir=synth_dir) ────────────────────

def test_seq_synth_prewrite_runs_import_paths(tmp_path):
    """FR-005/SC-003: pre-write applies ONLY import_paths (here: the bare-SPDX repair)."""
    synth_dir = tmp_path / "audit" / "poc" / "_synth"
    synth_dir.mkdir(parents=True)
    code = _BARE_SPDX + "contract SynthBase {}\n"
    out, applied = pqr._seq_synth_prewrite(code, tmp_path, synth_dir)
    assert out.startswith("// SPDX-License-Identifier: MIT")   # import_paths repaired the SPDX
    assert applied == ["import_paths"]


def test_seq_synth_prewrite_uses_synth_dir_depth(tmp_path):
    """FR-005/SC-003 (permanent guard for the base_dir divergence FR-014 named): the pre-write
    rewrites an off-by-one import relative to the SYNTH dir (audit/poc/_synth), one level deeper
    than audit/poc. A regression to base_dir=project would yield `../../…` and fail this."""
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "DemoBase.sol").write_text("// x\ncontract DemoBase {}\n")
    synth_dir = tmp_path / "audit" / "poc" / "_synth"
    synth_dir.mkdir(parents=True)
    code = ('// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n'
            'import { DemoBase } from "./DemoBase.sol";\n'
            "abstract contract SynthBase is DemoBase {}")
    out, applied = pqr._seq_synth_prewrite(code, tmp_path, synth_dir)
    assert 'from "../../../contracts/DemoBase.sol"' in out   # synth-dir depth, NOT poc's ../../
    assert applied == ["import_paths"]


def test_fix_import_paths_picks_shallowest_of_same_named(tmp_path):
    """Feature 033 F2 (determinism): with two same-named files (a real src contract + a deeper
    mock), the rewrite resolves to the SHALLOWEST path deterministically - not rglob's unstable
    os.scandir order, which could silently import the mock (a PoC compiling against the wrong type)."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "IFoo.sol").write_text("// x\ninterface IFoo {}\n")
    (tmp_path / "test" / "mocks").mkdir(parents=True)
    (tmp_path / "test" / "mocks" / "IFoo.sol").write_text("// x\ncontract IFoo {}\n")
    poc_dir = tmp_path / "audit" / "poc"
    poc_dir.mkdir(parents=True)
    code = ('// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n'
            'import { IFoo } from "./IFoo.sol";\ncontract PoC {}')
    out, _matched, changed = sf._fix_import_paths(code, tmp_path)
    assert changed
    assert 'from "../../src/IFoo.sol"' in out          # the shallow real one, deterministically
    assert "test/mocks" not in out                      # never the deeper mock
    # stable across repeated runs (no scandir-order dependence)
    assert sf._fix_import_paths(code, tmp_path)[0] == out


def test_seq_synth_prewrite_noop_returns_empty(tmp_path):
    synth_dir = tmp_path / "audit" / "poc" / "_synth"
    synth_dir.mkdir(parents=True)
    # No trailing newline: _fix_import_paths re-joins with "\n".join even on a no-op, so a
    # trailing "\n" would be dropped (existing behavior - pinned here as identity input).
    code = "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\ncontract C {}"
    out, applied = pqr._seq_synth_prewrite(code, tmp_path, synth_dir)
    assert out == code and applied == []


# ── synthesis repair: import_paths → nested → undeclared → address ──

def test_seq_synth_repair_order_and_applied(tmp_path):
    """FR-005/SC-003 + 040 FR-001c: the repair sequence returns per-fixer FixerResult rows;
    with symbol_index=None and no 9553, only import_paths matches+applies (bare-SPDX)."""
    synth_dir = tmp_path / "audit" / "poc" / "_synth"
    synth_dir.mkdir(parents=True)
    code = _BARE_SPDX + "contract SynthBase {}\n"
    out, results = pqr._seq_synth_repair(code, "Compiler run failed:\n", tmp_path, synth_dir, None)
    assert out.startswith("// SPDX-License-Identifier: MIT")
    assert [r["name"] for r in results] == [
        "import_paths", "nested_imports", "undeclared_import", "address_interface"]
    assert sf._applied_names(results) == ["import_paths"]   # nested/undeclared/address no-op
    by_name = {r["name"]: r for r in results}
    assert by_name["import_paths"]["matched"] and by_name["import_paths"]["applied"]
    assert not by_name["nested_imports"]["matched"] and not by_name["address_interface"]["matched"]
    assert not by_name["undeclared_import"]["matched"]


def test_seq_synth_repair_undeclared_from_scaffold(tmp_path):
    """Synth repair applies undeclared_import via parent-scaffold named import when file_map empty."""
    synth_dir = tmp_path / "audit" / "poc" / "_synth"
    synth_dir.mkdir(parents=True)
    code = ("// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"
            "abstract contract SynthBase { function t() public { Widget w; } }\n")
    existing = 'import { Widget } from "@deps/Widget.sol";\n'
    forge = _undeclared_block("Widget", "7920")
    out, results = pqr._seq_synth_repair(
        code, forge, tmp_path, synth_dir, None, file_map="", existing=existing)
    by_name = {r["name"]: r for r in results}
    assert by_name["undeclared_import"]["matched"] and by_name["undeclared_import"]["applied"]
    assert 'import { Widget } from "@deps/Widget.sol";' in out
    assert "undeclared_import" in sf._applied_names(results)


def test_seq_synth_repair_matched_not_applied_is_distinct(tmp_path):
    """T030/FR-001c: `matched && !applied` is representable and distinct from `!matched`
    (9553 present but flagged line out of range → address_interface matched, nothing applied)."""
    synth_dir = tmp_path / "audit" / "poc" / "_synth"
    synth_dir.mkdir(parents=True)
    code = "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\ncontract SynthBase {}\n"
    forge = ("Compiler run failed:\n"
             "Error (9553): Invalid type for argument in function call. "
             "Invalid implicit conversion from address to contract IThing requested.\n"
             "  --> audit/poc/_synth/SynthBase.sol:99:9:\n")
    _out, results = pqr._seq_synth_repair(code, forge, tmp_path, synth_dir, None)
    by_name = {r["name"]: r for r in results}
    assert by_name["address_interface"]["matched"] is True
    assert by_name["address_interface"]["applied"] is False
    assert sf._applied_names(results) == []
    assert any(r["matched"] and not r["applied"] for r in results)
    assert not by_name["import_paths"]["matched"]   # !matched ≠ matched&&!applied


def test_fix_nested_resolvable_proxy_library_chain(tmp_path):
    """T041/FR-013: a resolvable nested type inside a project library, imported from a WRONG
    path, resolves via the symbol index (base_dir) - previously exhausted because synth repair
    had no file_map and reused the wrong orig_path for the container import."""
    lib = tmp_path / "contracts" / "ProxyLib.sol"
    lib.parent.mkdir(parents=True)
    lib.write_text(
        "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"
        "library ProxyLib {\n"
        "    struct InitParams { uint256 x; }\n"
        "}\n",
        encoding="utf-8",
    )
    idx = SymbolIndex.build(tmp_path)
    synth_dir = tmp_path / "audit" / "poc" / "_synth"
    synth_dir.mkdir(parents=True)
    # Wrong relative path (as a model often emits); without index resolution the fixer would
    # re-import ProxyLib from the same wrong path and the chain would still not compile.
    code = ('// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n'
            'import { InitParams } from "./Missing.sol";\n'
            "abstract contract SynthBase {\n"
            "    function _init(InitParams memory p) internal pure {}\n"
            "}\n")
    # Old contract (no base_dir): container keeps "./Missing.sol" → still broken.
    old, _m_old, a_old = sf._fix_nested_type_imports(code, idx, "")
    assert a_old
    assert 'from "./Missing.sol"' in old
    # New contract (base_dir=synth_dir): container resolves to the real library path.
    out, matched, applied = sf._fix_nested_type_imports(code, idx, "", base_dir=synth_dir)
    assert matched and applied
    assert "import { InitParams }" not in out
    assert "ProxyLib.InitParams" in out
    assert 'import { ProxyLib } from' in out
    assert "Missing.sol" not in out
    assert "contracts/ProxyLib.sol" in out


# ── drafting in-place: undeclared → address (NOTABLY no import_paths) ─────────

def test_seq_draft_inplace_auto_imports_known_symbol():
    """FR-005/SC-003: the in-place sequence auto-imports a file-map-known undeclared symbol."""
    code = ("// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"
            "contract PoC { function t() public { uint256 z = Widget; } }")
    out, applied = pqr._seq_draft_inplace(code, _undeclared_block("Widget"),
                                          "Widget: contracts/Widget.sol")
    assert 'import { Widget } from "contracts/Widget.sol";' in out
    assert "undeclared_import" in applied


def test_seq_draft_inplace_does_not_run_import_paths():
    """SC-003 (the pinned GAP): the in-place sequence deliberately does NOT run
    import_paths - a bare SPDX that import_paths WOULD repair is left UNCHANGED here."""
    code = _BARE_SPDX + "contract PoC { function t() public {} }"
    out, applied = pqr._seq_draft_inplace(code, "Compiler run failed:\n", "")
    assert out == code                       # untouched - no import_paths in this sequence
    assert out.startswith("SPDX-License-Identifier")   # SPDX still bare (would be fixed if it ran)
    assert applied == []


# ── drafting post-model: setup_override(guard) → import_paths → nested → scaffold_base ──

_SCAFFOLD = (
    'import {FooDeploy} from "./FooDeploy.t.sol";\n'
    'contract FooTest is FooDeploy {\n'
    '    function setUp() public override {}\n'
    '}\n'
)


def test_seq_postmodel_order_import_paths_then_scaffold_base(tmp_path):
    """FR-005/SC-003: the post-model sequence applies import_paths then scaffold_base in
    order; the applied list preserves that order (the loops emit one event per entry)."""
    code = _BARE_SPDX + "contract PoC is FooDeploy { function test_x() public {} }"
    out, applied = pqr._seq_postmodel(code, tmp_path, None, "", _SCAFFOLD, guard=True)
    assert out.startswith("// SPDX-License-Identifier: MIT")   # import_paths ran
    assert "is FooTest" in out                                # scaffold_base forced the leaf
    assert applied == ["import_paths", "scaffold_base"]        # order preserved; setup/nested no-op


def test_seq_postmodel_guard_false_skips_setup_override(tmp_path):
    """With guard=False, setup_override is never consulted - it cannot appear in applied."""
    code = ("// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"
            "contract PoC is FooDeploy {\n"
            "    function setUp() public {}\n"
            "    function test_x() public {}\n}\n")
    _out, applied = pqr._seq_postmodel(code, tmp_path, None, "", _SCAFFOLD, guard=False)
    assert "setup_override" not in applied


# ── E1 (feature 033 follow-up): _tracked_sol surfaces a git failure ───────────
def test_tracked_sol_warns_on_git_failure(tmp_path, monkeypatch, caplog):
    """A git failure used to silently disable the tracked-source preference. Now it logs a
    warning (behavior still degrades to an empty set - visible, not silent)."""
    import logging
    import scripts.solidity_utils as su

    def _boom(*a, **k):
        raise OSError("git not found")
    monkeypatch.setattr(su.subprocess, "run", _boom)
    with caplog.at_level(logging.WARNING, logger="scripts.solidity_utils"):
        out = su._tracked_sol(tmp_path)
    assert out == set()
    assert any("tracked-source preference disabled" in r.message for r in caplog.records)
