"""Feature 040 - table tests for the shared scaffold cause taxonomy (US2 foundation).

Pins the two things every other 040 component leans on:
- classify_build_failure(blob) -> cause over target-free forge-output samples (T004) -
  the environment signatures (path/toolchain) win over the generic "it didn't compile"
  -> code, so an infra failure is never mislabelled model-authored;
- cause_nature completeness over BOTH closed sets (T006) - every cause maps to exactly
  one of the 3 natures, or is success (`ok`) / excluded, never unmapped.

All synthetic - invented names only, no target material.
"""
from __future__ import annotations

import scripts.scaffold_causes as sc

# ── T004: classify_build_failure table ───────────────────────────────────────
_PATH = 'Error: Source "_synth/SynthBase_1.sol" not found. Searched: /work'
_TOOLCHAIN = "Error: no compiler versions available for pragma ^0.8.28"
# Live forge (foundry) wording includes "are" - must not fall through to no_build:code.
_TOOLCHAIN_ARE = (
    "Error: Found Solidity sources, but no compiler versions are available for it\n")
_CODE = "Error (7576): Undeclared identifier. Did you mean 'foo'?\n  --> src/PoC.sol:12:9"


def test_classify_build_failure_path():
    assert sc.classify_build_failure(_PATH) == "no_build:path"


def test_classify_build_failure_toolchain():
    assert sc.classify_build_failure(_TOOLCHAIN) == "no_build:toolchain"
    assert sc.classify_build_failure(_TOOLCHAIN_ARE) == "no_build:toolchain"


def test_classify_build_failure_code():
    assert sc.classify_build_failure(_CODE) == "no_build:code"


def test_classify_build_failure_pure_deterministic():
    for s in (_PATH, _TOOLCHAIN, _CODE):
        assert sc.classify_build_failure(s) == sc.classify_build_failure(s)


def test_path_beats_code_when_both_present():
    # A path error often co-occurs with downstream 'undeclared identifier' noise; the
    # environment signature must win so infra is not mislabelled as model-authored code.
    assert sc.classify_build_failure(_PATH + "\n" + _CODE) == "no_build:path"


def test_returns_a_no_build_subcause_always():
    for s in (_PATH, _TOOLCHAIN, _CODE, "totally unrecognized blob"):
        assert sc.classify_build_failure(s) in {
            "no_build:path", "no_build:toolchain", "no_build:code"}


# ── T006: cause -> nature completeness (both closed sets) ─────────────────────
_THREE = {"harness-infra", "synth-model", "model"}
_NO_NATURE = {"not_attempted:budget", "unclassified", "not_triggered"}


def test_every_synthesis_cause_mapped():
    for c in sc.SYNTHESIS_CAUSES:
        if sc.is_ok(c):
            assert sc.cause_nature(c) is None
        else:
            assert sc.cause_nature(c) in _THREE


def test_every_finding_cause_mapped():
    for c in sc.FINDING_CAUSES:
        nat = sc.cause_nature(c)
        if sc.is_ok(c):
            assert nat is None
        elif c in _NO_NATURE:
            assert nat is None  # excluded / unclassified / fold-rule-promoted
        else:
            assert nat in _THREE


def test_exactly_three_natures_exist():
    natures = {sc.cause_nature(c) for c in (sc.SYNTHESIS_CAUSES | sc.FINDING_CAUSES)}
    natures.discard(None)
    assert natures == _THREE


def test_not_attempted_budget_excluded_from_denominator():
    assert sc.in_denominator("not_attempted:budget") is False
    assert sc.in_denominator("model") is True
    assert sc.in_denominator("not_triggered") is True
    assert sc.in_denominator("proved") is True


def test_not_triggered_has_no_default_nature():
    # An unverified miss is NOT charged to the model column by default; the classifier
    # promotes it to `model` only via the fold rule.
    assert sc.cause_nature("not_triggered") is None
