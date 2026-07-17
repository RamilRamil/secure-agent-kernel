"""Invariant: no audited-target identifier lives in the harness — above all, not in a PROMPT.

Standing project rule: bug-bounty/audited-target material (contract names, findings, paths)
NEVER enters this repo. Beyond hygiene, the PROMPT case is a correctness bug: every string in
`*_PROMPT` is sent to the model on EVERY audit, so a name borrowed from one engagement becomes
a worked example while auditing an unrelated project — irrelevant at best, and priming the model
toward an API that does not exist there at worst.

This actually happened. Live-run findings were diagnosed as "the model invented an API" when the
prompts themselves had been teaching it that project's vocabulary (`IFooCooldown`, `cdo`,
`_deployFooStack()`, `setUpFooBase()`). Both prompts and comments were scrubbed to
neutral placeholders (`IFoo`, `_deployFooStack()`); this test keeps them scrubbed — the names get
re-introduced naturally, one paste from a run log at a time.

Scope: `scripts/` (the harness + its helpers). `tests/` is deliberately EXCLUDED — several test
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
# denylist of every possible target name — no such list can exist — but a ratchet: once a
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
    """Comments, docstrings and code alike — a target name has no business in `scripts/`."""
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
    """A guard that cannot fail is not a guard — pin that the matcher really matches."""
    assert _LEAK_RE.search("use the real ICooldown, never an invented IFooCooldown")
    assert not _LEAK_RE.search("use the real IFoo, never an invented IFooManager")
