"""Feature 033 — the fixer call-site inventory as an ENFORCED invariant (FR-009 + SC-002).

The recurring import-path bug class returned 3× partly because the deterministic fixers were
scattered and the transform-application sites were hand-inlined in two near-duplicate loops. This
test makes the structure a declared, checkable invariant, not a comment:

- The KNOWN SET of named sequence-functions is asserted BY NAME (keyed on the name-set, not line
  numbers — stable across unrelated refactors). Adding a SIXTH site means adding a named function +
  updating this set: a conscious, declared change, not a silent one that re-opens the bug class
  (specs 031/032 each added a site within days).
- Every individual `_fix_*` is called ONLY from inside a named sequence-function — no stray fixer
  call escapes into a new unpinned site.
- SC-002: `scripts/poc_queue_runner.py` holds NO `def _fix_*` body (only transitional re-exports) —
  "no fixer logic in pqr" enforced structurally, not satisfiable by "the file got shorter".
"""
from __future__ import annotations

import ast
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_FIXERS_SRC = (_SCRIPTS / "solidity_fixers.py").read_text()
_PQR_SRC = (_SCRIPTS / "poc_queue_runner.py").read_text()

# The four named sequence-functions (one per site's sequence; _seq_postmodel serves BOTH post-model
# sites — draft & fix — whose sequences are byte-identical, the site-specific `stage` is caller-side).
_NAMED_SEQUENCES = {
    "_seq_synth_prewrite", "_seq_synth_repair", "_seq_draft_inplace", "_seq_postmodel",
}
_FIXERS = {
    "_fix_setup_override", "_fix_import_paths", "_fix_address_interface",
    "_fix_undeclared_import", "_fix_nested_type_imports", "_fix_scaffold_base",
}


def _defs(src: str) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}


def _fixer_calls(node: ast.AST) -> list[str]:
    return [c.func.id for c in ast.walk(node)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in _FIXERS]


def test_named_sequence_set_is_exactly_the_declared_four():
    """FR-009: the set of named sequence-functions in solidity_fixers is EXACTLY the declared set.
    A new site → a new _seq_ function fails this until the set is consciously updated."""
    seqs = {name for name in _defs(_FIXERS_SRC) if name.startswith("_seq_")}
    assert seqs == _NAMED_SEQUENCES


def test_every_fixer_is_defined_in_the_fixer_module():
    defs = _defs(_FIXERS_SRC)
    assert _FIXERS <= set(defs), f"missing fixer defs: {_FIXERS - set(defs)}"


def test_fixers_are_called_only_from_inside_named_sequences():
    """FR-009: no stray fixer call escapes into an un-pinned site. Every `_fix_*` call in
    solidity_fixers is lexically inside a `_seq_*` function; no fixer calls another fixer."""
    tree = ast.parse(_FIXERS_SRC)   # parse ONCE — node identity must be shared across both walks
    defs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    # calls inside the named sequences (allowed)
    allowed = {id(c) for seq in _NAMED_SEQUENCES
               for c in ast.walk(defs[seq])
               if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in _FIXERS}
    # every fixer call anywhere in the module must be one of the allowed (inside a _seq_)
    all_calls = [c for c in ast.walk(tree)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in _FIXERS]
    stray = [c.func.id for c in all_calls if id(c) not in allowed]
    assert stray == [], f"fixer(s) called outside a named sequence-function: {stray}"


def test_pqr_has_no_fixer_logic_and_no_fixer_calls():
    """SC-002 + FR-009: poc_queue_runner.py defines NO fixer/sequence body (only re-exports) and
    calls NO `_fix_*` directly — the loops go through the `_seq_*` functions."""
    pqr_defs = set(_defs(_PQR_SRC))
    assert not (pqr_defs & _FIXERS), f"fixer body still defined in pqr: {pqr_defs & _FIXERS}"
    assert not {n for n in pqr_defs if n.startswith("_seq_")}, "sequence body still defined in pqr"
    assert _fixer_calls(ast.parse(_PQR_SRC)) == [], "pqr calls a fixer directly (should call _seq_*)"
