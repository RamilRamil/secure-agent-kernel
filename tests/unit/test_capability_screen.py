"""Feature 038 - the prover capability screen: cascade contract (A1..A7 + FR-015), cost/latency and
3-D Pareto (B5/B6/B8), and the Decision-8 single-item scoping.

OFFLINE / DETERMINISTIC / TARGET-FREE. The cascade's sole expensive seam (`attempt_fn`) is the
scripted fake from conftest; no model, no network, no target material. The byte-unchanged oracle is
never edited here - only its verdict hash is pinned (A6).
"""
from __future__ import annotations

import os

os.environ.setdefault("SR_SECRET_KEY", "00" * 32)

from pathlib import Path

import pytest

import scripts.capability_screen as cs
from scripts.capability_screen import (
    CascadeStageResult, Candidate, build_map, build_pairs, run_cascade,
)
from scripts.proof_bench import credible_interval


def _cheap(i: str, **k) -> Candidate:
    return Candidate(id=i, cost_class="cheap", **k)


# ── A1 / SC-002 - no Bayes@N call on a not_triggered cell ─────────────────────

def test_a1_no_expensive_call_on_not_triggered(placeholder_battery, prereg_record, scripted):
    cands = [_cheap("m-triggers"), _cheap("m-flat")]
    attempts = scripted({
        "m-triggers": {"triggered": True, "verified": 3},
        "m-flat": {"triggered": False},          # clears smoke, never triggers
    })
    rec = prereg_record(cands, n=4)
    run_cascade(record=rec, battery=placeholder_battery, candidates=cands, attempt_fn=attempts)
    assert attempts.count(stage="bayes", cand="m-flat") == 0        # zero expensive calls (SC-002)
    assert attempts.count(stage="bayes", cand="m-triggers") > 0


# ── A2 / FR-018 - smoke is a plumbing check with one retry, not a capability filter ──

def test_a2_smoke_retry_then_proceed(placeholder_battery, prereg_record, scripted):
    cands = [_cheap("m")]
    attempts = scripted({"m": {"produced": [False, True], "triggered": True, "verified": 2}})
    rec = prereg_record(cands, n=3)
    results, _ = run_cascade(record=rec, battery=placeholder_battery, candidates=cands,
                             attempt_fn=attempts)
    cell = next(r for r in results if r.finding_class == "rounding_low")
    assert cell.smoke == "pass" and cell.triggered            # retry rescued it - proceeds


def test_a2_smoke_double_fail_is_plumbing_not_capability(placeholder_battery, prereg_record, scripted):
    cands = [_cheap("m")]
    attempts = scripted({"m": {"produced": [False, False], "triggered": True, "verified": 3}})
    rec = prereg_record(cands, n=3)
    results, _ = run_cascade(record=rec, battery=placeholder_battery, candidates=cands,
                             attempt_fn=attempts)
    cell = next(r for r in results if r.finding_class == "rounding_low")
    assert cell.smoke == "plumbing_fail"
    assert cell.triggered is False and cell.interval is None    # NOT a capability 0
    assert attempts.count(stage="bayes", cand="m") == 0


# ── A3 / SC-007 - triggered ≠ verified ────────────────────────────────────────

def test_a3_triggered_not_verified(placeholder_battery, prereg_record, scripted):
    cands = [_cheap("m")]
    attempts = scripted({"m": {"triggered": True, "verified": 0}})   # triggers, verifies 0/N
    rec = prereg_record(cands, n=5)
    results, _ = run_cascade(record=rec, battery=placeholder_battery, candidates=cands,
                             attempt_fn=attempts)
    cell = next(r for r in results if r.finding_class == "rounding_low")
    assert cell.triggered is True and cell.verified_k == 0 and cell.n == 5


# ── A4 / FR-014 - transient distinct from a capability miss ────────────────────

def test_a4_transient_not_a_verified_miss(placeholder_battery, prereg_record, scripted):
    cands = [_cheap("m")]
    # first Bayes attempt is a transient 503; it is retried, and every real attempt verifies.
    attempts = scripted({"m": {"triggered": True, "transient": [True],
                               "verified": [False, True, True, True, True, True, True]}})
    rec = prereg_record(cands, n=5)
    results, _ = run_cascade(record=rec, battery=placeholder_battery, candidates=cands,
                             attempt_fn=attempts)
    cell = next(r for r in results if r.finding_class == "rounding_low")
    assert cell.transient_failures >= 1
    assert cell.n == 5 and cell.verified_k == 5           # transient did NOT depress the rate


# ── A5 - cheap-first ordering ─────────────────────────────────────────────────

def test_a5_cheap_first_ordering(placeholder_battery, prereg_record, scripted):
    cands = [Candidate(id="m-mid", cost_class="mid"), Candidate(id="m-cheap", cost_class="cheap")]
    attempts = scripted({"m-mid": {"triggered": True, "verified": 1},
                         "m-cheap": {"triggered": True, "verified": 1}})
    rec = prereg_record(cands, n=2)
    run_cascade(record=rec, battery=placeholder_battery, candidates=cands, attempt_fn=attempts)
    assert attempts.order("smoke")[0] == "m-cheap"        # cheap before mid


# ── A6 / SC-005 - oracle byte-unchanged, no redefinition ──────────────────────

# Feature 040 SC-008 / T036: pin the 037/038 oracle source hash. 040 touches grounding/
# emission only - any drift here means mutation_verify/_poc_defects were edited (FR-014).
_ORACLE_SOURCE_HASH_037_038 = (
    "353d09483a3f9e98d489fddcde66f96cc8211f841f430305566c8c04d205edc1"
)


def test_a6_oracle_hash_stable_and_not_redefined():
    import inspect

    import scripts.poc_queue_runner as runner
    import hashlib
    want = hashlib.sha256(
        (inspect.getsource(runner.mutation_verify) + "\n"
         + inspect.getsource(runner._poc_defects)).encode("utf-8")).hexdigest()
    assert cs.oracle_verdict_hash() == want               # screen references the same verdict source
    import re
    src = Path(cs.__file__).read_text(encoding="utf-8")
    # no TOP-LEVEL redefinition (a docstring mention of the name is fine; a real `def` is not) (FR-009)
    assert not re.search(r"^def (mutation_verify|_poc_defects)\b", src, re.M)


def test_t036_oracle_byte_identical_to_037_038_state():
    """SC-008: the deterministic oracle is byte-identical to its 037/038 state (pinned hash)."""
    assert cs.oracle_verdict_hash() == _ORACLE_SOURCE_HASH_037_038


# ── A7 / FR-011/020 - reference-only candidate runs no stage ───────────────────

def test_a7_reference_only_runs_no_stage(placeholder_battery, prereg_record, scripted):
    cands = [_cheap("m"), Candidate(id="frontier", cost_class="expensive",
                                    callable_via_harness=False)]
    attempts = scripted({"m": {"triggered": True, "verified": 1},
                         "frontier": {"triggered": True, "verified": 1}})
    rec = prereg_record(cands, n=2)
    run_cascade(record=rec, battery=placeholder_battery, candidates=cands, attempt_fn=attempts)
    assert attempts.count(cand="frontier") == 0           # no smoke/screen/bayes for the frontier


# ── FR-015 - no cascade stage takes a privileged/write action ─────────────────

def test_fr015_no_privileged_action_seam(placeholder_battery, prereg_record, scripted):
    """The cascade only ever calls `attempt_fn` (run/inspect/draft). It has no write/privileged path:
    every stage a candidate hits is one of smoke/screen/bayes - never a write action."""
    cands = [_cheap("m")]
    attempts = scripted({"m": {"triggered": True, "verified": 1}})
    rec = prereg_record(cands, n=2)
    run_cascade(record=rec, battery=placeholder_battery, candidates=cands, attempt_fn=attempts)
    assert {st for (_, _, st) in attempts.calls} <= {"smoke", "screen", "bayes"}


# ── Decision 8 - single-item scoping + axis label ─────────────────────────────

def test_decision8_single_item_flag_and_axis_label(placeholder_battery, prereg_record, scripted):
    cands = [_cheap("m")]
    attempts = scripted({"m": {"triggered": True, "verified": 1}})
    rec = prereg_record(cands, n=2, cases_per_class=1)
    results, costs = run_cascade(record=rec, battery=placeholder_battery, candidates=cands,
                                 attempt_fn=attempts)
    assert all(r.single_item for r in results if r.interval is not None)
    m = build_map(stage_results=results, cost_records=costs, record=rec)
    assert m["axis_label"] == "representative-finding capability"
    assert "best_model" not in m                          # SC-004 / B6


def test_decision8_multi_item_axis_label(placeholder_battery, prereg_record, scripted):
    cands = [_cheap("m")]
    attempts = scripted({"m": {"triggered": True, "verified": 1}})
    rec = prereg_record(cands, n=2, cases_per_class=2)    # ≥2 findings/class ⇒ class capability
    results, costs = run_cascade(record=rec, battery=placeholder_battery, candidates=cands,
                                 attempt_fn=attempts)
    m = build_map(stage_results=results, cost_records=costs, record=rec)
    assert m["axis_label"] == "class capability"


# ── Cost/latency + 3-D Pareto (T016 - $/case, $/pass, B5/B6/B8) ────────────────

def test_cost_per_case_and_per_pass_and_undefined_at_zero(placeholder_battery, prereg_record, scripted):
    cands = [_cheap("m-pass"), _cheap("m-zero")]
    attempts = scripted({
        "m-pass": {"triggered": True, "verified": 4, "tokens": 1000, "seconds": 2.0},
        "m-zero": {"triggered": True, "verified": 0, "tokens": 1000, "seconds": 2.0},
    })
    rec = prereg_record(cands, n=4)                       # price_table = 0.001 usd / 1k tokens
    _, costs = run_cascade(record=rec, battery=placeholder_battery, candidates=cands,
                           attempt_fn=attempts)
    passc = next(c for c in costs if c.candidate == "m-pass" and c.finding_class == "rounding_low")
    zeroc = next(c for c in costs if c.candidate == "m-zero" and c.finding_class == "rounding_low")
    assert passc.usd_per_case > 0 and passc.usd_per_pass is not None
    assert zeroc.usd_per_pass is None                     # honest undefined at zero passes


def test_b5_overlapping_capability_different_cost_both_nondominated(placeholder_battery,
                                                                    prereg_record, scripted):
    # two candidates, identical verified rate (overlapping intervals) but different cost/latency →
    # capability-incomparable ⇒ BOTH non-dominated (Decision 6 / B5).
    cands = [_cheap("m-cheapfast"), _cheap("m-dearslow")]
    attempts = scripted({
        "m-cheapfast": {"triggered": True, "verified": 3, "tokens": 500, "seconds": 1.0},
        "m-dearslow": {"triggered": True, "verified": 3, "tokens": 2000, "seconds": 5.0},
    })
    rec = prereg_record(cands, n=6)
    results, costs = run_cascade(record=rec, battery=placeholder_battery, candidates=cands,
                                 attempt_fn=attempts)
    m = build_map(stage_results=results, cost_records=costs, record=rec)
    nd = {(x["candidate"], x["finding_class"]) for x in m["pareto_nondominated"]}
    assert ("m-cheapfast", "rounding_low") in nd
    assert ("m-dearslow", "rounding_low") in nd           # overlapping capability protects it


def test_b8_frontier_ceiling_excluded_from_pareto_and_pairs(placeholder_battery, prereg_record,
                                                            scripted):
    cands = [_cheap("m")]
    attempts = scripted({"m": {"triggered": True, "verified": 3}})
    rec = prereg_record(cands, n=6)
    results, costs = run_cascade(record=rec, battery=placeholder_battery, candidates=cands,
                                 attempt_fn=attempts)
    ceiling = {"model": "frontier-ref", "note": "relay wall - visual ceiling, no interval"}
    m = build_map(stage_results=results, cost_records=costs, record=rec, ceiling=ceiling)
    assert "interval" not in m["ceiling"]
    assert all(x["candidate"] != "frontier-ref" for x in m["pareto_nondominated"])
    assert all("frontier-ref" not in (p["a"], p["b"]) for p in m["pairs"])


# ── B1 / SC-008 - pairwise resolution; a single separated pair ≠ class resolved ──

def _cell(cand, cls, k, n, level=0.95, single=True) -> CascadeStageResult:
    r = CascadeStageResult(candidate=cand, finding_class=cls, triggered=True,
                           verified_k=k, n=n, single_item=single)
    r.interval = credible_interval(k, n, mass=level)
    return r


def test_b1_pairwise_single_separated_does_not_resolve_class():
    # class with A vs B separated but A vs C (and B vs C) overlapping ⇒ some resolved, class NOT
    # globally resolved, and it is NOT underpowered (it has a separated pair).
    # A=30/30 vs B=0/30 separate at 0.95; A vs C=27/30 overlap (high, near A) → one unresolved pair.
    cells = [_cell("A", "rounding_low", 30, 30), _cell("B", "rounding_low", 0, 30),
             _cell("C", "rounding_low", 27, 30)]
    pairs, underpowered = build_pairs(cells, 0.95)
    by = {(p.a, p.b): p for p in pairs}
    assert by[("A", "B")].status == "resolved" and by[("A", "B")].dominant == "A"
    assert any(p.status == "unresolved@N" for p in pairs)     # not every pair separated
    assert "rounding_low" not in underpowered                 # has ≥1 separated pair


def test_b2_underpowered_when_no_pair_separates():
    cells = [_cell("A", "narrow_precondition", 3, 6), _cell("B", "narrow_precondition", 4, 6)]
    _, underpowered = build_pairs(cells, 0.95)
    assert "narrow_precondition" in underpowered               # overlapping ⇒ underpowered@N


def test_b1_single_item_flag_propagates_to_pairs():
    cells = [_cell("A", "rounding_low", 30, 30, single=True),
             _cell("B", "rounding_low", 0, 30, single=True)]
    pairs, _ = build_pairs(cells, 0.95)
    assert all(p.single_item for p in pairs)                   # Decision 8


# ── B7 / FR-010 - the global headline is kept alongside, never merged into by_class ──

def test_b7_headline_kept_unmerged(placeholder_battery, prereg_record, scripted):
    cands = [_cheap("m")]
    attempts = scripted({"m": {"triggered": True, "verified": 3}})
    rec = prereg_record(cands, n=6)
    results, costs = run_cascade(record=rec, battery=placeholder_battery, candidates=cands,
                                 attempt_fn=attempts)
    headline = credible_interval(9, 18)                       # a distinct global interval
    m = build_map(stage_results=results, cost_records=costs, record=rec, headline=headline)
    assert "headline" in m and "by_class" in m
    assert "headline" not in m["by_class"]                    # never merged into the per-class map


# ── T008 live wire (pure parts): runner stdout → AttemptResult ────────────────
# The subprocess seam itself needs a live target; these cover the OFFLINE-testable folding of the
# runner's JSONL stdout into signals + the outcome→AttemptResult mapping (the honesty-load-bearing
# part: transient ≠ verified 0; triggered only on a real PASS; verified only on passed_verified).

from scripts.capability_screen import parse_runner_events, persist_case_log, signals_to_result


def _events(*evs) -> str:
    import json as _j
    return "\n".join(_j.dumps(e) for e in evs)


# ── Feature 040 US1 (T011): each spawned per-case runner log is persisted ─────
def test_parse_extracts_run_id_from_first_event():
    out = _events(
        {"event": "provider", "run_id": "20260727T140312Z-a3f1", "model": "m/x"},
        {"event": "task_done", "finding_id": "H-01", "run_id": "20260727T140312Z-a3f1",
         "outcome": "exhausted", "elapsed_s": 3.0},
    )
    assert parse_runner_events(out, "H-01")["run_id"] == "20260727T140312Z-a3f1"


def test_persist_case_log_writes_run_scoped_file(tmp_path):
    """A completed run's captured stdout is persisted verbatim under _runs/<run_id>.jsonl - the
    log-events.md scheme - so a post-hoc analysis can read exactly where the model stumbled."""
    runs = tmp_path / "audit" / "poc" / "_runs"
    stdout = _events(
        {"event": "provider", "run_id": "R-123", "model": "m/x"},
        {"event": "task_done", "finding_id": "H-01", "run_id": "R-123", "outcome": "exhausted"},
    )
    path = persist_case_log(runs, stdout, "", run_id="R-123",
                            model="m/x", case_id="H-01", stage="bayes")
    assert path == runs / "R-123.jsonl"
    assert path.read_text() == stdout + "\n"                 # verbatim, newline-terminated


def test_persist_case_log_opaque_exit_is_still_traceable(tmp_path):
    """An opaque no-start exit (no run_id on stdout) is STILL persisted - under a deterministic
    fallback id - with its stderr tail, so the empty rc=0 exit observed in 038 is diagnosable."""
    runs = tmp_path / "_runs"
    path = persist_case_log(runs, "", "ModuleNotFoundError: scripts", run_id="",
                            model="m/x", case_id="H-07", stage="smoke")
    assert path.parent == runs and path.name.startswith("nostart-")
    body = path.read_text()
    assert "ModuleNotFoundError" in body and '"no_run_id": true' in body
    # deterministic: same inputs → same fallback path
    again = persist_case_log(runs, "", "ModuleNotFoundError: scripts", run_id="",
                             model="m/x", case_id="H-07", stage="smoke")
    assert again == path


def test_t042_persist_case_log_leaves_prereg_byte_unmodified(tmp_path):
    """SC-009 / T042: after capability_screen's T017 persist_case_log path, 038's frozen
    prereg JSONL must stay byte-identical (mirrors C3 - prior entry never rewritten)."""
    from scripts.capability_screen import Candidate, make_prereg, write_prereg

    prereg = tmp_path / "audit" / "prereg.jsonl"
    rec = make_prereg(
        candidates=[Candidate(id="m-a"), Candidate(id="m-b")],
        class_enum=["rounding_low"],
        n_by_class={"rounding_low": 6},
        cases_per_class=1,
        credible_level=0.95,
        pinned_bench_id="placeholder-battery",
        price_table={"m-a": 0.001, "m-b": 0.002},
    )
    write_prereg(prereg, rec)
    before = prereg.read_bytes()
    active_hash = __import__("json").loads(before.decode().strip())["content_hash"]
    assert len(active_hash) >= 16                         # pin a real hash, not an empty stub

    runs = tmp_path / "audit" / "poc" / "_runs"
    stdout = _events(
        {"event": "provider", "run_id": "R-sc009", "model": "m/x"},
        {"event": "task_done", "finding_id": "H-01", "run_id": "R-sc009", "outcome": "exhausted"},
    )
    persist_case_log(runs, stdout, "", run_id="R-sc009",
                     model="m/x", case_id="H-01", stage="bayes")

    assert prereg.read_bytes() == before                  # byte-unmodified
    assert __import__("json").loads(prereg.read_text().strip())["content_hash"] == active_hash
    assert (runs / "R-sc009.jsonl").exists()              # retention wrote elsewhere, not into prereg


def test_parse_task_done_verified():
    out = _events(
        {"event": "tested", "finding_id": "H-01", "passed": True},
        {"event": "task_done", "finding_id": "H-01", "outcome": "passed_verified", "elapsed_s": 42.0},
    )
    p = parse_runner_events(out, "H-01")
    assert p["has_task_done"] and p["saw_tested"] and p["outcome"] == "passed_verified"
    r = signals_to_result(p)
    assert r.produced and r.triggered and r.verified and not r.transient and r.seconds == 42.0


def test_triggered_but_not_verified():
    for oc in ("unverified_pass", "passed_unchecked"):
        p = parse_runner_events(_events(
            {"event": "tested", "finding_id": "H-02"},
            {"event": "task_done", "finding_id": "H-02", "outcome": oc, "elapsed_s": 1.0}), "H-02")
        r = signals_to_result(p)
        assert r.triggered and not r.verified, oc


def test_produced_but_not_triggered():
    p = parse_runner_events(_events(
        {"event": "tested", "finding_id": "H-03"},
        {"event": "task_done", "finding_id": "H-03", "outcome": "exhausted", "elapsed_s": 1.0}), "H-03")
    r = signals_to_result(p)
    assert r.produced and not r.triggered and not r.verified


def test_no_task_done_is_transient_not_a_miss():
    # crash / provider error / upstream timeout: no task_done → transient, NEVER a verified 0 (FR-014)
    p = parse_runner_events(_events({"event": "tested", "finding_id": "H-01"}), "H-01")
    r = signals_to_result(p)
    assert r.transient and not r.verified and not r.triggered


def test_only_the_requested_finding_id_sets_outcome():
    # a task_done for a DIFFERENT finding must not leak into this case's verdict
    out = _events(
        {"event": "task_done", "finding_id": "H-99", "outcome": "passed_verified", "elapsed_s": 9.0},
        {"event": "task_done", "finding_id": "H-01", "outcome": "exhausted", "elapsed_s": 3.0})
    r = signals_to_result(parse_runner_events(out, "H-01"))
    assert not r.verified and not r.triggered and r.produced and r.seconds == 3.0


def test_infra_outcomes_are_transient_not_a_miss():
    # sandbox_unavailable / run_error are the runner's infra outcomes - transient, excluded from the
    # denominator, NEVER a verified 0 (FR-014). They DO emit a task_done, so guard on the outcome.
    for oc in ("sandbox_unavailable", "run_error", "draft_failed"):
        p = parse_runner_events(_events(
            {"event": "task_done", "finding_id": "H-01", "outcome": oc, "elapsed_s": 2.0}), "H-01")
        r = signals_to_result(p)
        assert r.transient and not r.verified and not r.triggered, oc


# ── T025/T026 / FR-014 - a transient at the TRIGGER-SCREEN is not a capability verdict ──
#
# Regression guard for a bug that shipped into a live map (2026-07-27 fast-tier run): the
# trigger-screen was the only cascade stage that never inspected `transient`, so a timeout/503 left
# `triggered=False` and logged `not_triggered` - publishing an infra failure as a measured capability
# fact. Smoke (produced=False → retry → plumbing_fail) and Bayes (`continue`) already handled it.

def _events_log():
    """Collect cascade log events so the emitted verdict word can be asserted, not just the cell."""
    out = []
    return out, out.append


def test_t025_persistent_screen_transient_is_unavailable_not_not_triggered(
        placeholder_battery, prereg_record, scripted):
    # every screen attempt times out → the cell must NOT claim the model failed to trigger
    attempts = scripted({"m": {"screen_transient": [True] * 20, "triggered": True, "verified": 5}})
    cands = [_cheap("m")]
    rec = prereg_record(cands, n=4)
    events, log = _events_log()
    results, _ = run_cascade(record=rec, battery=placeholder_battery, candidates=cands,
                             attempt_fn=attempts, log=log)
    cell = next(r for r in results if r.finding_class == "rounding_low")
    assert cell.screen == "unavailable"
    assert cell.transient_failures >= 1
    assert cell.interval is None                              # no interval invented
    kinds = {e.get("event") for e in events}
    assert "screen_unavailable" in kinds
    assert "not_triggered" not in kinds                       # the core of the bug
    assert attempts.count(stage="bayes", cand="m") == 0       # still no expensive call (SC-002)


def test_t025_flaky_screen_is_retried_then_scored_normally(
        placeholder_battery, prereg_record, scripted):
    # one transient then a real answer: the retry must rescue it and the verdict be the REAL one
    attempts = scripted({"m": {"screen_transient": [True], "triggered": True, "verified": 3}})
    cands = [_cheap("m")]
    rec = prereg_record(cands, n=3)
    events, log = _events_log()
    results, _ = run_cascade(record=rec, battery=placeholder_battery, candidates=cands,
                             attempt_fn=attempts, log=log)
    cell = next(r for r in results if r.finding_class == "rounding_low")
    assert cell.screen == "pass" and cell.triggered is True
    assert cell.transient_failures >= 1                       # the flake was recorded, not hidden
    assert "screen_unavailable" not in {e.get("event") for e in events}


def test_t025_genuine_not_triggered_is_unchanged(placeholder_battery, prereg_record, scripted):
    # no transient anywhere → behaviour must be byte-identical to before the fix
    attempts = scripted({"m": {"triggered": False}})
    cands = [_cheap("m")]
    rec = prereg_record(cands, n=4)
    events, log = _events_log()
    results, _ = run_cascade(record=rec, battery=placeholder_battery, candidates=cands,
                             attempt_fn=attempts, log=log)
    cell = next(r for r in results if r.finding_class == "rounding_low")
    assert cell.screen == "pass" and cell.triggered is False  # a REAL capability miss still lands
    kinds = {e.get("event") for e in events}
    assert "not_triggered" in kinds and "screen_unavailable" not in kinds


def test_t025_unavailable_cell_publishes_triggered_null_not_false(
        placeholder_battery, prereg_record, scripted):
    # the map must not let a reader mistake "we never got an answer" for "the model could not do it"
    attempts = scripted({"m": {"screen_transient": [True] * 20}})
    cands = [_cheap("m")]
    rec = prereg_record(cands, n=4)
    results, costs = run_cascade(record=rec, battery=placeholder_battery, candidates=cands,
                                 attempt_fn=attempts)
    m = build_map(stage_results=results, cost_records=costs, record=rec)
    cell = m["by_class"]["rounding_low"]["m"]
    assert cell["screen"] == "unavailable"
    assert cell["triggered"] is None                          # null, never False
    assert m["pareto_nondominated"] == [] and m["pairs"] == []


def test_t025_real_trigger_outranks_a_sibling_transient(prereg_record):
    # two cases in one class: one always times out, the other genuinely triggers → the class DID
    # trigger. Scripted inline (not via the `scripted` fixture) because that fixture keys its screen
    # counter by (candidate, case), so a per-candidate list cannot say "case-a1 flaky, case-a2 fine".
    from scripts.capability_screen import AttemptResult, BatteryCase
    battery = [BatteryCase(case_id="case-a1", finding_class="rounding_low"),
               BatteryCase(case_id="case-a2", finding_class="rounding_low")]

    def attempt_fn(cand: str, case: str, stage: str) -> AttemptResult:
        if stage == "smoke":
            return AttemptResult(produced=True, triggered=False, verified=False, seconds=1.0)
        if stage == "screen" and case == "case-a1":
            return AttemptResult(produced=False, triggered=False, verified=False,
                                 seconds=1.0, transient=True)
        if stage == "screen":
            return AttemptResult(produced=True, triggered=True, verified=False, seconds=1.0)
        return AttemptResult(produced=True, triggered=True, verified=True, seconds=1.0)

    cands = [_cheap("m")]
    rec = prereg_record(cands, n=2, cases_per_class=2)
    results, _ = run_cascade(record=rec, battery=battery, candidates=cands, attempt_fn=attempt_fn)
    cell = next(r for r in results if r.finding_class == "rounding_low")
    assert cell.triggered is True and cell.screen == "pass"   # a real PASS beats a sibling transient
    assert cell.transient_failures >= 1                       # the sibling flake is still recorded


# ── T022/T023 / FR-021 - a swallowed host OOM is infra, never a capability miss ──
#
# The runner swallows a container OOM-kill into a NON-COMPILING build and reports a capability-looking
# outcome (`exhausted`/`compile_only_defective`). Guarding on the outcome string alone would score that
# as produced/not-triggered. The infra cause (from the `tested` event's exit_code/stderr) MUST take
# priority over the outcome and force transient.

def test_t022_oom_exit137_outranks_capability_outcome():
    # tested shows exit_code 137, then the runner reports a capability-looking `exhausted`
    out = _events(
        {"event": "tested", "finding_id": "H-01", "passed": False, "exit_code": 137,
         "stderr_tail": "Compiler run failed"},
        {"event": "task_done", "finding_id": "H-01", "outcome": "exhausted", "elapsed_s": 5.0})
    p = parse_runner_events(out, "H-01")
    assert p["infra_cause"] == "oom"
    r = signals_to_result(p)
    assert r.transient and r.infra_cause == "oom"
    assert not r.produced and not r.triggered and not r.verified   # NOT a capability miss


def test_t022_oom_signal9_marker_in_stderr():
    # no 137 exit, but the classic solc OOM signature "signal: 9" in the build tail
    out = _events(
        {"event": "tested", "finding_id": "H-02", "passed": False, "exit_code": 1,
         "stderr_tail": "solc exited with signal: 9 (SIGKILL)"},
        {"event": "task_done", "finding_id": "H-02", "outcome": "compile_only_defective",
         "elapsed_s": 3.0})
    p = parse_runner_events(out, "H-02")
    assert p["infra_cause"] == "oom"
    assert signals_to_result(p).transient


def test_t022_normal_build_failure_is_not_infra():
    # a GENUINE non-compiling build (no OOM marker, exit 1) must stay a capability result, not infra
    out = _events(
        {"event": "tested", "finding_id": "H-03", "passed": False, "exit_code": 1,
         "stderr_tail": "Error (7576): Undeclared identifier."},
        {"event": "task_done", "finding_id": "H-03", "outcome": "exhausted", "elapsed_s": 4.0})
    p = parse_runner_events(out, "H-03")
    assert p["infra_cause"] == ""
    r = signals_to_result(p)
    assert not r.transient and r.produced and not r.triggered      # honest capability miss preserved


def test_t022_infra_cause_beats_even_a_pass():
    # pathological: an OOM marker co-occurs with a reported PASS - infra still wins (never a false trigger)
    out = _events(
        {"event": "tested", "finding_id": "H-01", "passed": True, "exit_code": 137},
        {"event": "task_done", "finding_id": "H-01", "outcome": "passed_verified", "elapsed_s": 9.0})
    p = parse_runner_events(out, "H-01")
    r = signals_to_result(p)
    assert r.transient and not r.verified and not r.triggered      # infra outranks the outcome string


def test_t022_smoke_oom_labelled_infra_not_plumbing(placeholder_battery, prereg_record):
    # an OOM at smoke must be labelled `infra`, NOT `plumbing_fail` (which implies model output was bad)
    from scripts.capability_screen import AttemptResult

    def attempt_fn(cand, case, stage):
        # every smoke draw is an OOM → produced False, infra_cause set
        return AttemptResult(produced=False, triggered=False, verified=False,
                             seconds=1.0, transient=True, infra_cause="oom")

    cands = [_cheap("m")]
    rec = prereg_record(cands, n=3)
    events, log = _events_log()
    results, _ = run_cascade(record=rec, battery=placeholder_battery, candidates=cands,
                             attempt_fn=attempt_fn, log=log)
    cell = next(r for r in results if r.finding_class == "rounding_low")
    assert cell.smoke == "infra"
    assert cell.triggered is False and cell.interval is None        # excluded, not a capability miss
    kinds = {e.get("event") for e in events}
    assert "smoke_infra" in kinds and "smoke_plumbing_fail" not in kinds


def test_t022_screen_oom_is_unavailable_not_not_triggered(placeholder_battery, prereg_record):
    # an OOM at the trigger-screen flows through the T025 path → screen `unavailable`, not not_triggered
    from scripts.capability_screen import AttemptResult

    def attempt_fn(cand, case, stage):
        if stage == "smoke":
            return AttemptResult(produced=True, triggered=False, verified=False, seconds=1.0)
        # screen: always OOM
        return AttemptResult(produced=False, triggered=False, verified=False,
                             seconds=1.0, transient=True, infra_cause="oom")

    cands = [_cheap("m")]
    rec = prereg_record(cands, n=3)
    events, log = _events_log()
    results, _ = run_cascade(record=rec, battery=placeholder_battery, candidates=cands,
                             attempt_fn=attempt_fn, log=log)
    cell = next(r for r in results if r.finding_class == "rounding_low")
    assert cell.screen == "unavailable" and cell.smoke == "pass"
    kinds = {e.get("event") for e in events}
    assert "screen_unavailable" in kinds and "not_triggered" not in kinds
