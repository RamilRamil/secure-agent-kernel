"""Invariant: no audited-target identifier lives in the harness - above all, not in a PROMPT.

Standing project rule: bug-bounty/audited-target material (contract names, findings, paths)
NEVER enters this repo. Beyond hygiene, the PROMPT case is a correctness bug: every string in
`*_PROMPT` is sent to the model on EVERY audit, so a name borrowed from one engagement becomes
a worked example while auditing an unrelated project - irrelevant at best, and priming the model
toward an API that does not exist there at worst.

This actually happened. Live-run findings were diagnosed as "the model invented an API" when the
prompts themselves had been teaching it that project's vocabulary (`IFooCooldown`, `cdo`,
`_deployFooStack()`, `setUpFooBase()`). Both prompts and comments were scrubbed to
neutral placeholders (`IFoo`, `_deployFooStack()`); this test keeps them scrubbed - the names get
re-introduced naturally, one paste from a run log at a time.

Scope: `scripts/` (the harness + its helpers). `tests/` is deliberately EXCLUDED - several test
fixtures still carry these names as test data; that is a separate, no-runtime-impact cleanup.
Docs are excluded too: `docs/roadmap.md`'s live-run history needs the real names to stay
intelligible as a record of what happened.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

# Identifiers from audited targets that have previously leaked in. Not an exhaustive
# denylist of every possible target name - no such list can exist - but a ratchet: once a
# name is known to have leaked, it must never come back.
_TARGET_IDENTIFIERS = [
    "FooCooldown", "Foo", "FooCDO", "Foo", "FooDeploy", "Foo",
    "FooTest", "sFOO", "FooPairProvider", "ERC20Foo", "TFooParams", "TFooGuard",
    "TFooUpperBounds", "calculateFooMode", "setVaultFooBounds", "Reviewer",
]
_LEAK_RE = re.compile("|".join(re.escape(n) for n in _TARGET_IDENTIFIERS))

_PROMPT_RE = re.compile(r'^([A-Z_]+_PROMPT)\s*=\s*"""(.*?)"""', re.S | re.M)


def _py_files():
    return sorted(_SCRIPTS.rglob("*.py"))


@pytest.mark.parametrize("path", _py_files(), ids=lambda p: p.name)
def test_no_target_identifier_in_harness_source(path: Path):
    """Comments, docstrings and code alike - a target name has no business in `scripts/`."""
    offenders = sorted({m.group(0) for m in _LEAK_RE.finditer(path.read_text(encoding="utf-8"))})
    assert not offenders, (
        f"{path.relative_to(_SCRIPTS.parent)} names audited-target identifier(s) {offenders}. "
        f"Use a neutral placeholder (e.g. `IFoo`, `DemoVault`, `_deployFooStack()`); target "
        f"material must not enter this repo."
    )


def test_no_target_identifier_in_any_prompt():
    """The load-bearing half: prompts ship to the model on every run, for every project."""
    leaks = []
    for path in _py_files():
        for m in _PROMPT_RE.finditer(path.read_text(encoding="utf-8")):
            for hit in _LEAK_RE.finditer(m.group(2)):
                leaks.append(f"{path.name}:{m.group(1)} → {hit.group(0)}")
    assert not leaks, (
        "audited-target identifiers found inside prompt text sent to the model on EVERY audit "
        f"(they would prime it toward another project's API): {leaks}"
    )


def test_the_guard_can_actually_fail():
    """A guard that cannot fail is not a guard - pin that the matcher really matches."""
    assert _LEAK_RE.search("use the real ICooldown, never an invented IFooCooldown")
    assert not _LEAK_RE.search("use the real IFoo, never an invented IFooManager")


# ── Feature 040 SC-010 / T043 - cross-target generality of shipped US3/US4 fixes ──
# Each shipped offline fix carries an explicit general-vs-target-specific verdict, and the
# fix sites are greppably free of target path / dependency-name literals (mechanical, not
# just a judgement). Live-gated US3 (T024/T025/T028) are excluded until they ship.

_040_GENERALITY_VERDICTS = {
    # US3 offline
    "T029_no_output_crash_vs_model": "general",
    "T027_synth_base_relative_import": "general",
    # US4
    "T033_matched_applied_fixer_contract": "general",
    "T034_repair_exhausted_resolvable_split": "general",
    "T035_option_c_insufficiency_ladder": "general",
    "T041_nested_import_base_dir_resolution": "general",
}

_040_FIX_FILES = [
    "scripts/solidity_fixers.py",
    "scripts/poc_queue_runner.py",
    "scripts/scaffold_causes.py",
    "scripts/scaffold_taxonomy.py",
    "scripts/capability_screen.py",
]

# Target-specific path/dependency literals that must not appear in shipped 040 fix sites
# (SC-010). Complements the identifier denylist above with filesystem/dep-layout fingerprints
# from the measured heavy target (solmate 0.8.15 pins under a dep's test tree, etc.).
_TARGET_PATH_RE = re.compile(
    r"lib/properties|lib/solmate|/solmate/src/test|"
    r"node_modules/@openzeppelin"
)


def test_t043_040_fixes_carry_generality_verdicts():
    """SC-010: every shipped US3/US4 fix records general vs target-specific."""
    assert _040_GENERALITY_VERDICTS, "at least one shipped fix must be recorded"
    for name, verdict in _040_GENERALITY_VERDICTS.items():
        assert verdict in ("general", "target-specific"), f"{name}: bad verdict {verdict!r}"
    # All offline 040 fixes shipped so far are general (no target-specific patch landed).
    assert all(v == "general" for v in _040_GENERALITY_VERDICTS.values())


@pytest.mark.parametrize("rel", _040_FIX_FILES, ids=lambda p: Path(p).name)
def test_t043_040_fix_sites_free_of_target_path_literals(rel: str):
    """SC-010 mechanical half: fix diffs are greppably free of target path/dep literals."""
    path = Path(__file__).resolve().parents[2] / rel
    text = path.read_text(encoding="utf-8")
    hits = sorted({m.group(0) for m in _TARGET_PATH_RE.finditer(text)})
    assert not hits, (
        f"{rel} embeds target-specific path/dependency literal(s) {hits}. "
        f"A general fix must not hard-code a target's layout (SC-010)."
    )