"""Feature 026: the proof-pipeline eval — external loading, the Jeffreys interval, the attrition
funnel, the overlap/config-mismatch comparison, and anti-inflation scoring.

OFFLINE and SYNTHETIC only. The real harness run (`run_case`) is the expensive measured subject and
is NEVER exercised here — scoring is tested on invented manifests and scripted harness event streams /
outcomes. No target material enters the repo (memory `feedback_no_target_code_in_agent`).
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("SR_SECRET_KEY", "00" * 32)

from pathlib import Path

import pytest

import scripts.proof_bench as pb
from scripts.proof_bench import (
    CaseOutcome, ProofBenchError, RunConfig, build_funnel, compare, credible_interval,
    load_case, score,
)


def _cfg(**over):
    base = dict(case_set_id="strata", provider="gemini", model="m", scaffold="", example="",
                settings={"fork": True}, n=5, harness_version="abc123")
    base.update(over)
    return RunConfig(**base)


def _report(successes, trials, outcomes=None, cfg=None):
    outs = outcomes if outcomes is not None else []
    r = score(outs, cfg or _cfg())
    # override the interval to a chosen (successes, trials) when testing compare directly
    return pb.Report(interval=credible_interval(successes, trials), funnel=r.funnel,
                     config=r.config)


def _write_case(root: Path, case_id: str, **fields) -> Path:
    d = root / "cases" / case_id
    d.mkdir(parents=True)
    (d / "case.json").write_text(json.dumps({"case_id": case_id, **fields}), encoding="utf-8")
    return d


# ── loading (external-only, loud) ─────────────────────────────────────────────

def test_external_guard_rejects_dataset_in_repo():
    with pytest.raises(ProofBenchError):
        pb.load_dataset(Path(pb._AGENT_ROOT) / "some" / "proof")


def test_missing_fix_is_loud(tmp_path):
    fix = tmp_path / "f.patch"; fix.write_text("--- a\n+++ b\n", encoding="utf-8")
    # no fix_path at all
    d = _write_case(tmp_path, "c1", target_path=str(tmp_path / "t"), report_path=str(tmp_path / "r.md"),
                    finding_id="1")
    with pytest.raises(ProofBenchError) as e:
        load_case(d)
    assert "fix_path" in str(e.value)
    # fix_path points at a non-existent file
    d2 = _write_case(tmp_path, "c2", target_path=str(tmp_path / "t"), report_path=str(tmp_path / "r.md"),
                     finding_id="1", fix_path=str(tmp_path / "nope.patch"))
    with pytest.raises(ProofBenchError):
        load_case(d2)


def _curated(tmp_path, cid="c1", **over):
    """A fully-formed case manifest incl. feature-028 curated finding fields."""
    (tmp_path / "t").mkdir(exist_ok=True)
    fix = tmp_path / "f.patch"; fix.write_text("--- a\n+++ b\n", encoding="utf-8")
    fields = dict(target_path=str(tmp_path / "t"), report_path=str(tmp_path / "r.md"),
                  finding_id="H-01", fix_path=str(fix),
                  title="Reentrancy in withdraw", location="Vault.withdraw",
                  description="external call before the balance write")
    fields.update(over)
    return _write_case(tmp_path, cid, **fields), fix


def test_valid_case_loads(tmp_path):
    # feature 028: a case now carries its curated finding (title/location/description)
    d, fix = _curated(tmp_path)
    case = load_case(d)
    assert case.case_id == "c1" and case.finding_id == "H-01" and case.fix_path == fix.resolve()
    assert case.title == "Reentrancy in withdraw" and case.location == "Vault.withdraw"
    assert case.description == "external call before the balance write"


def test_missing_curated_finding_is_loud(tmp_path):
    # feature 028 FR-008: absent OR empty curated field → loud, never a silent fallback to extraction
    for missing in ("title", "location", "description"):
        d, _ = _curated(tmp_path, cid=f"absent-{missing}", **{missing: None})
        # rewrite without the key entirely
        import json as _json
        m = _json.loads((d / "case.json").read_text()); m.pop(missing, None)
        (d / "case.json").write_text(_json.dumps(m), encoding="utf-8")
        with pytest.raises(ProofBenchError) as e:
            load_case(d)
        assert missing in str(e.value)
    # empty string is treated as missing
    d, _ = _curated(tmp_path, cid="empty-title", title="   ")
    with pytest.raises(ProofBenchError):
        load_case(d)


def test_run_case_pins_the_finding_via_tasks_from(tmp_path, monkeypatch):
    """feature 028 FR-009/FR-010: run_case writes a single-task file (id==finding_id, curated text)
    and the harness argv uses --tasks-from and NOT --only. The harness subprocess is STUBBED."""
    d, fix = _curated(tmp_path, cid="pin", finding_id="7")
    case = load_case(d)
    seen = {}

    def _fake_run(argv, **k):
        seen["argv"] = argv
        # capture the task file the harness was pointed at, before run_case cleans it up
        i = argv.index("--tasks-from")
        seen["task_file"] = json.loads(Path(argv[i + 1]).read_text())
        return type("R", (), {"stdout": '{"event": "task_done", "outcome": "passed_verified"}'})()

    monkeypatch.setattr(pb.subprocess, "run", _fake_run)
    cfg = _cfg(n=1)
    pb.run_case(case, cfg, image=None, fork=False)

    argv = seen["argv"]
    assert "--tasks-from" in argv and "--only" not in argv          # pinned, not id-filtered
    assert f"7={fix.resolve()}" in argv or f"7={fix}" in " ".join(argv)  # fix keyed on the same id
    task = seen["task_file"]
    assert len(task) == 1 and task[0]["id"] == "7"                  # exactly the one pinned finding
    assert task[0]["title"] == "Reentrancy in withdraw"            # curated text, verbatim


# ── the Jeffreys interval (US1) ───────────────────────────────────────────────

def test_interval_anchors():
    # the betai + bisection core, via the underlying quantile
    assert abs(pb._beta_ppf(0.5, 1, 1) - 0.5) < 1e-6
    assert abs(pb._beta_ppf(0.025, 1, 1) - 0.025) < 1e-6
    assert abs(pb._beta_ppf(0.5, 0.5, 0.5) - 0.5) < 1e-6


def test_interval_deterministic():
    a = credible_interval(3, 10)
    b = credible_interval(3, 10)
    assert (a.lo, a.hi) == (b.lo, b.hi)


def test_interval_widens_with_smaller_n():
    small = credible_interval(1, 2).width     # same 0.5 rate, less data
    big = credible_interval(10, 20).width
    assert small > big


def test_interval_edges_do_not_collapse():
    # Jeffreys stays bounded at s=0 and s=n (the small-N regime); n=1 is wide.
    z = credible_interval(0, 5)
    assert 0.0 <= z.lo < z.hi < 1.0 and z.hi > 0.0
    full = credible_interval(5, 5)
    assert 0.0 < full.lo < full.hi <= 1.0 and full.lo < 1.0
    assert credible_interval(1, 1).width > 0.5   # n=1 → wide


# ── comparison: overlap (US1) + config mismatch (US4) ─────────────────────────

def test_compare_overlapping_not_distinguishable():
    a = _report(5, 10); b = _report(6, 10)
    out = compare(a, b)
    assert out["comparable"] and out["verdict"] == "not_distinguishable"


def test_compare_separated_decides():
    a = _report(0, 20); b = _report(20, 20)
    out = compare(a, b)
    assert out["comparable"] and out["verdict"] == "b_better"


def test_compare_flags_config_mismatch():
    a = _report(5, 10, cfg=_cfg(model="x"))
    b = _report(5, 10, cfg=_cfg(model="y"))   # differs beyond harness_version
    out = compare(a, b)
    assert not out["comparable"] and out["reason"] == "config_mismatch"
    assert "model" in out["differing_fields"]


def test_compare_same_config_diff_version_proceeds():
    a = _report(0, 20, cfg=_cfg(harness_version="v1"))
    b = _report(20, 20, cfg=_cfg(harness_version="v2"))
    out = compare(a, b)
    assert out["comparable"] and out["verdict"] == "b_better"


# ── the funnel + stage mapping (US2) ──────────────────────────────────────────

def _ev(fid_ids=("1",), written=False, compiled=False, real_pass=False, outcome=None,
        error=False, not_found=False):
    ev = []
    if error:
        ev.append({"event": "run_error"})
        return ev
    ev.append({"event": "extracted", "ids": list(fid_ids)})
    if not_found:
        ev.append({"event": "only_ids_not_found", "missing": ["1"]})
    if written:
        ev.append({"event": "written"})
    if compiled or real_pass:
        ev.append({"event": "tested", "compiled": compiled, "real_pass": real_pass})
    if outcome:
        ev.append({"event": "task_done", "outcome": outcome})
    return ev


def test_stage_of_maps_raw_event_streams():
    # the fragile coupling to the runner's real event shapes — tested DIRECTLY, not via pre-staged outcomes
    assert pb._stage_of(_ev(written=True, compiled=True, real_pass=True, outcome="passed_verified"), "1") == "verified"
    assert pb._stage_of(_ev(written=True, compiled=True, real_pass=True, outcome="passed_unchecked"), "1") == "real_pass"
    assert pb._stage_of(_ev(written=True, compiled=True), "1") == "compiled"
    assert pb._stage_of(_ev(written=True), "1") == "draft"
    assert pb._stage_of(_ev(), "1") == "extracted"


def test_stage_of_requires_id_membership():
    # extraction emits ALL ids — a bare `extracted` event must not count every case as extracted
    assert pb._stage_of(_ev(fid_ids=("1", "2"), written=True), "9") == "not_extracted"
    assert pb._stage_of(_ev(fid_ids=("1",), not_found=True), "1") == "not_extracted"
    assert pb._stage_of(_ev(error=True), "1") == "error"


def _out(cid, stage, outcome=""):
    return CaseOutcome(case_id=cid, run_idx=0, stage=stage, outcome=outcome)


def test_funnel_counts_and_names_casualties():
    outs = [_out("a", "verified", "passed_verified"), _out("b", "real_pass"),
            _out("c", "compiled"), _out("d", "draft")]
    fn = build_funnel(outs)
    assert fn.survivors["extracted"] == 4 and fn.survivors["verified"] == 1
    assert fn.survivors["real_pass"] == 2 and fn.survivors["compiled"] == 3
    assert fn.casualties["verified"] == ["b"]     # b died going real_pass→verified
    assert fn.casualties["real_pass"] == ["c"]
    assert fn.casualties["compiled"] == ["d"]


def test_funnel_monotonic_non_increasing():
    import random
    rng = random.Random(0)
    outs = [_out(f"c{i}", rng.choice(pb.STAGES)) for i in range(50)]
    fn = build_funnel(outs)
    counts = [fn.survivors[s] for s in pb.STAGES]
    assert all(counts[i] >= counts[i + 1] for i in range(len(counts) - 1))


def test_funnel_real_pass_cliff_is_visible():
    # this session's exact situation: many real_pass, zero verified → the fixes are the problem
    outs = [_out(f"c{i}", "real_pass") for i in range(5)]
    fn = build_funnel(outs)
    assert fn.survivors["real_pass"] == 5 and fn.survivors["verified"] == 0
    assert set(fn.casualties["verified"]) == {f"c{i}" for i in range(5)}


def test_funnel_off_ladder_buckets():
    outs = [_out("a", pb.NOT_EXTRACTED), _out("b", pb.ERROR), _out("c", "verified", "passed_verified")]
    fn = build_funnel(outs)
    assert fn.off_ladder[pb.NOT_EXTRACTED] == ["a"] and fn.off_ladder[pb.ERROR] == ["b"]
    assert fn.survivors["verified"] == 1


# ── anti-inflation scoring (US3) ──────────────────────────────────────────────

def test_score_counts_exactly_passed_verified():
    outs = [_out("a", "verified", "passed_verified"),
            _out("b", "real_pass", "passed_unchecked"),
            _out("c", "real_pass", "unverified_pass"),
            _out("d", "compiled", "compiled")]
    r = score(outs, _cfg())
    assert r.interval.successes == 1 and r.interval.trials == 4   # ONLY passed_verified counts


def test_denominator_is_all_loaded_case_runs():
    # every case is fix-bearing (fix-less rejected at load), so trials == number of case-runs;
    # nothing is silently included or excluded
    outs = [_out(f"c{i}", "compiled", "passed_unchecked") for i in range(7)]
    r = score(outs, _cfg())
    assert r.interval.trials == 7 and r.interval.successes == 0


def test_render_states_n_width_and_dev_caveat():
    r = score([_out("a", "verified", "passed_verified")], _cfg(n=3))
    text = pb.render(r)
    assert "N=3" in text and "width" in text.lower()
    assert "DEV SET" in text and "NOT absolute capability" in text
