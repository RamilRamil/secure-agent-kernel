"""Feature 040 - tests for the offline scaffold-failure taxonomy classifier (US2).

All fixtures are target-free synthetic event streams under tests/fixtures/scaffold_events/.
The classifier is a pure function of its input: no model, no network.
"""
from __future__ import annotations

import json
from pathlib import Path

import scripts.scaffold_taxonomy as tax

_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "scaffold_events"


def _load(name: str) -> list[dict]:
    return [json.loads(ln) for ln in (_FIX / name).read_text().splitlines() if ln.strip()]


# ── T018: completeness + determinism ─────────────────────────────────────────
def test_mixed_complete_and_deterministic():
    events = _load("mixed_terminal.jsonl")
    a = tax.classify(events)
    b = tax.classify(events)
    assert a == b  # pure
    assert a["queued"] == 3
    assert a["attempted"] == 3
    assert a["terminal_emitted"] == 3
    assert a["truncated"] is False
    # every finding terminal cause is in the closed set (no silent drop)
    assert set(a["finding_counts"]) <= tax.sc.FINDING_CAUSES | {"unclassified"}
    assert a["synthesis_counts"]["synthesized"] == 1
    assert a["finding_counts"]["proved"] == 1


def test_unknown_cause_becomes_unclassified_not_dropped():
    events = _load("mixed_terminal.jsonl")
    events.append({"run_id": "R1", "model": "m/test", "ts": 9.0, "terminal": True,
                   "level": "finding_attempt", "finding_id": "H-09", "cause": "gremlin"})
    out = tax.classify(events)
    assert out["finding_counts"]["unclassified"] == 1


# ── T019: FR-003 - refuse to fabricate attribution ───────────────────────────
def test_old_log_is_unattributed_no_by_model():
    events = _load("old_unattributed.jsonl")
    out = tax.classify(events)
    assert out["unattributed"] == 4
    assert out["by_model"] == {}          # no fabricated per-model rows
    assert out["terminal_emitted"] == 0   # legacy log carries no terminal events


# ── T020: denominator honesty on a budget-truncated run ──────────────────────
def test_budget_truncation_refuses_share():
    events = _load("budget_truncated.jsonl")
    out = tax.classify(events)
    assert out["queued"] == 3
    assert out["attempted"] == 1          # not_attempted:budget excluded
    assert out["truncated"] is True
    assert out["nature_share"] is None    # refused without --allow-truncated
    assert out["finding_counts"]["not_attempted:budget"] == 2


def test_budget_truncation_share_published_when_allowed():
    events = _load("budget_truncated.jsonl")
    out = tax.classify(events, allow_truncated=True)
    assert out["nature_share"] is not None
    assert set(out["nature_share"]) == {"harness-infra", "synth-model", "model"}


# ── T021: fold rule - synth-model surfaces at the finding level ───────────────
def test_fold_attributes_finding_to_upstream_synth_nature():
    events = _load("mixed_terminal.jsonl")
    out = tax.classify(events, allow_truncated=True)
    share = out["nature_share"]
    # H-01: synth failed no_build:toolchain (harness-infra) -> folded harness-infra
    #       (even though its finding terminal was `model`)
    # H-02: synthesized -> proved -> ok (no nature)
    # H-03: synth failed no_output:model (synth-model) -> folded synth-model
    #       (even though its finding terminal was not_triggered)
    assert share["harness-infra"] == 1 / 3
    assert share["synth-model"] == 1 / 3
    assert share["model"] == 0.0


def test_fold_keeps_genuine_model_when_synth_succeeded():
    # synthesis succeeded, finding failed with `model` -> genuinely model.
    events = [
        {"run_id": "R3", "model": "m/x", "ts": 1, "terminal": True,
         "level": "synthesis_attempt", "attempt_seq": 1, "finding_id": "F1",
         "cause": "synthesized", "ok": True},
        {"run_id": "R3", "model": "m/x", "ts": 2, "terminal": True,
         "level": "finding_attempt", "finding_id": "F1", "cause": "model", "nature": "model"},
    ]
    out = tax.classify(events, allow_truncated=True)
    assert out["nature_share"]["model"] == 1.0


# ── Feature 042 T056: new non-terminal events must not change 040 classification ─
_042_NONTERMINAL_FIXTURES = (
    "reachability_checks_single.jsonl",
    "reachability_checks_composite.jsonl",
    "repeat_hint_hypothesis_confirmed.jsonl",
    "repeat_hint_hypothesis_indeterminate.jsonl",
    "repeat_hint_corroborated.jsonl",
    "mechanism_regression_hint.jsonl",
    "no_042_events.jsonl",
)


def test_042_nonterminal_events_do_not_change_taxonomy():
    """SC-009 / FR-010: injecting 042 advisory events (reachability_checks,
    repeat_revert_hint, mechanism_regression_hint) into a known 040 stream must
    leave classify() byte-identical to the same stream with those events stripped.
    """
    base = _load("mixed_terminal.jsonl")
    baseline = tax.classify(base)

    injected: list[dict] = list(base)
    for name in _042_NONTERMINAL_FIXTURES:
        for e in _load(name):
            # Keep only non-terminal advisory shapes; drop any terminal/done noise
            # from the synthetic 042 fixtures so we isolate the FR-010 claim.
            if e.get("terminal"):
                continue
            if e.get("event") in (
                "scaffold_synthesized", "repeat_revert_hint",
                "mechanism_regression_hint", "provider", "done",
            ) or "reachability_checks" in e:
                injected.append(e)

    with_042 = tax.classify(injected)
    assert with_042 == baseline

    # Stripping reachability_checks / hint events from the injected stream recovers
    # the same classification as the pristine base (identity both ways).
    stripped = [
        {k: v for k, v in e.items() if k != "reachability_checks"}
        for e in injected
        if e.get("event") not in ("repeat_revert_hint", "mechanism_regression_hint")
    ]
    assert tax.classify(stripped) == baseline


def test_043_compile_adopt_rejected_does_not_change_taxonomy():
    """SC-005 / FR-010: compile_adopt_rejected is non-terminal; classify() unchanged."""
    base = _load("mixed_terminal.jsonl")
    baseline = tax.classify(base)
    path = (
        Path(__file__).resolve().parents[1]
        / "fixtures" / "compiled_checkpoint" / "events" / "compile_adopt_rejected.jsonl"
    )
    injected = list(base)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        e = json.loads(line)
        if e.get("event") == "compile_adopt_rejected":
            injected.append(e)
    assert tax.classify(injected) == baseline


# ── T038: Constitution IV guard - no auto-promotion into steering knowledge ───
def test_classifier_imports_are_allowlisted():
    """The taxonomy is a REPORT: it must not import the lesson store, prompt machinery,
    or the runner - so no observation can self-promote into pipeline-steering knowledge
    (Principle IV). Checked structurally via the AST (not a docstring grep)."""
    import ast

    tree = ast.parse(Path(tax.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            if node.module == "scripts":
                imported.update(f"scripts.{a.name}" for a in node.names)
    allowed = {"__future__", "argparse", "glob", "json", "collections",
               "scripts", "scripts.scaffold_causes"}
    assert imported <= allowed, f"unexpected imports (Principle IV): {imported - allowed}"
