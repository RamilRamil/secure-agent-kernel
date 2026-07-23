"""Feature 033 — characterization tests for the five named transform-application
sequence-functions (FR-005). Each pins ONE site's exact sequence output over a fixed
SYNTHETIC fixture (invented names only — no target material), so a future unification
(spec 034) cannot silently change a sequence.

These are the LASTING guardrail: green on the pre-extraction tree (the functions are
extracted in commit 1), and still green after the fixers move to `solidity_fixers`
(commit 4). The temporary differential test (test_fixer_extraction_diff.py) additionally
proves each function equals the REAL loop's inline output; it is removed in commit 2.
"""
from __future__ import annotations

import scripts.poc_queue_runner as pqr

# A bare SPDX line (missing its `//`) — `_fix_import_paths` repairs it independently of
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


def test_seq_synth_prewrite_noop_returns_empty(tmp_path):
    synth_dir = tmp_path / "audit" / "poc" / "_synth"
    synth_dir.mkdir(parents=True)
    # No trailing newline: _fix_import_paths re-joins with "\n".join even on a no-op, so a
    # trailing "\n" would be dropped (existing behavior — pinned here as identity input).
    code = "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\ncontract C {}"
    out, applied = pqr._seq_synth_prewrite(code, tmp_path, synth_dir)
    assert out == code and applied == []


# ── synthesis repair: import_paths(base_dir) → nested(NO file_map) → address ──

def test_seq_synth_repair_order_and_applied(tmp_path):
    """FR-005/SC-003: the repair sequence; with symbol_index=None and no 9553 in the
    forge output, only import_paths fires (bare-SPDX), pinning it runs first."""
    synth_dir = tmp_path / "audit" / "poc" / "_synth"
    synth_dir.mkdir(parents=True)
    code = _BARE_SPDX + "contract SynthBase {}\n"
    out, applied = pqr._seq_synth_repair(code, "Compiler run failed:\n", tmp_path, synth_dir, None)
    assert out.startswith("// SPDX-License-Identifier: MIT")
    assert applied == ["import_paths"]   # nested/address no-op on this input


# ── drafting in-place: undeclared → address (NOTABLY no import_paths) ─────────

def test_seq_draft_inplace_auto_imports_known_symbol():
    """FR-005/SC-003: the in-place sequence auto-imports a file-map-known undeclared symbol."""
    code = ("// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"
            "contract PoC { function t() public { uint256 z = Widget; } }")
    out, applied = pqr._seq_draft_inplace(code, _undeclared_block("Widget"), None,
                                          "Widget: contracts/Widget.sol")
    assert 'import { Widget } from "contracts/Widget.sol";' in out
    assert "undeclared_import" in applied


def test_seq_draft_inplace_does_not_run_import_paths():
    """SC-003 (the pinned GAP): the in-place sequence deliberately does NOT run
    import_paths — a bare SPDX that import_paths WOULD repair is left UNCHANGED here."""
    code = _BARE_SPDX + "contract PoC { function t() public {} }"
    out, applied = pqr._seq_draft_inplace(code, "Compiler run failed:\n", None, "")
    assert out == code                       # untouched — no import_paths in this sequence
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
    """With guard=False, setup_override is never consulted — it cannot appear in applied."""
    code = ("// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"
            "contract PoC is FooDeploy {\n"
            "    function setUp() public {}\n"
            "    function test_x() public {}\n}\n")
    _out, applied = pqr._seq_postmodel(code, tmp_path, None, "", _SCAFFOLD, guard=False)
    assert "setup_override" not in applied
