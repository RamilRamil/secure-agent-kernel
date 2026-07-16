"""Spec 023: the discovery benchmark rig — loader guards, anti-inflation matching, scoring.

The synthetic case is BUILT IN tmp_path (never a checked-in fixture): it satisfies the
external-root guard AND keeps even invented finding-shaped data out of the repo.
Fully offline — no model, no network, no dataset.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("SR_SECRET_KEY", "00" * 32)

from pathlib import Path

import pytest

import scripts.bench as bench
from scripts.bench import (
    BenchError,
    Candidate,
    Case,
    GroundTruth,
    heuristic_detector,
    load_dataset,
    match_findings,
    score,
)
from sr_agent.packs.audit.finding import BastetTag

_TAG = BastetTag.reentrancy
_OTHER = BastetTag.incorrect_access_control


def _gt(fid="H-01", tag=_TAG, loc="Vault.sol", fn="withdraw"):
    return GroundTruth(finding_id=fid, bastet_tag=tag, location=loc,
                       function_name=fn, severity="high")


def _cand(fid="C-1", tag=_TAG, loc="Vault.sol", fn="withdraw"):
    return Candidate(finding_id=fid, bastet_tag=tag, location=loc, function_name=fn)


def _case(truth=None):
    return Case(case_id="synth", truth=truth if truth is not None else [_gt()])


def _write_case(root: Path, target: Path, labels: list[dict]) -> Path:
    d = root / "cases" / "synth"
    d.mkdir(parents=True)
    (d / "case.json").write_text(
        json.dumps({"case_id": "synth", "target_path": str(target)}), encoding="utf-8")
    (d / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    return d


# ── loader guards (FR-001/004) ───────────────────────────────────────────────

def test_root_inside_agent_repo_rejected():
    with pytest.raises(BenchError):
        load_dataset(Path(bench._AGENT_ROOT) / "some" / "bench")


def test_unknown_tag_is_loud_not_skipped(tmp_path):
    tgt = tmp_path / "t"
    tgt.mkdir()
    _write_case(tmp_path, tgt, [{"finding_id": "H-01", "bastet_tag": "not-a-real-tag",
                                 "location": "V.sol", "function_name": "f"}])
    with pytest.raises(BenchError) as ei:
        load_dataset(tmp_path)
    assert "not-a-real-tag" in str(ei.value)


def test_valid_case_loads(tmp_path):
    tgt = tmp_path / "t"
    tgt.mkdir()
    _write_case(tmp_path, tgt, [{"finding_id": "H-01", "bastet_tag": "reentrancy",
                                 "location": "V.sol", "function_name": "f", "severity": "high"}])
    cases = load_dataset(tmp_path)
    assert len(cases) == 1 and len(cases[0].truth) == 1
    assert cases[0].truth[0].bastet_tag is BastetTag.reentrancy


def test_both_target_and_repo_url_rejected(tmp_path):
    d = tmp_path / "cases" / "synth"
    d.mkdir(parents=True)
    (d / "case.json").write_text(
        json.dumps({"target_path": str(tmp_path), "repo_url": "https://x/y"}), encoding="utf-8")
    (d / "labels.json").write_text("[]", encoding="utf-8")
    with pytest.raises(BenchError):
        load_dataset(tmp_path)


# ── ANTI-INFLATION: the integrity core (FR-006/007, SC-004) ──────────────────

def test_exact_match_is_credited():
    m = match_findings([_cand()], [_gt()])
    assert len(m.matched) == 1 and not m.needs_review and not m.missed


def test_same_location_wrong_tag_not_credited():
    m = match_findings([_cand(tag=_OTHER)], [_gt()])
    assert m.matched == [] and len(m.missed) == 1
    assert m.needs_review and m.needs_review[0][2] == "tag_mismatch"


def test_same_tag_wrong_location_not_credited():
    m = match_findings([_cand(loc="Other.sol", fn="deposit")], [_gt()])
    assert m.matched == [] and len(m.missed) == 1
    assert m.needs_review and m.needs_review[0][2] == "location_mismatch"


def test_untagged_candidate_never_credited():
    m = match_findings([_cand(tag=None)], [_gt()])
    assert m.matched == [] and len(m.missed) == 1


def test_textual_similarity_alone_never_credited():
    # Different place AND different class — only prose would "look" similar.
    m = match_findings([_cand(fid="C-9", tag=_OTHER, loc="Zzz.sol", fn="other")], [_gt()])
    assert m.matched == [] and m.spurious and len(m.missed) == 1


def test_two_candidates_one_truth_credited_once():
    m = match_findings([_cand(fid="C-1"), _cand(fid="C-2")], [_gt()])
    assert len(m.matched) == 1
    assert len(m.spurious) + len(m.needs_review) == 1   # the surplus is not credited


def test_volume_cannot_buy_recall():
    truth = [_gt()]
    spam = [_cand(fid=f"C-{i}", tag=_OTHER, loc=f"F{i}.sol", fn="x") for i in range(100)]
    m = match_findings(spam, truth)
    card = score(_case(truth), "fake", m, len(spam))
    assert card.recall == 0.0
    assert card.precision == 0.0   # noise only hurts precision


def test_path_insensitive_location_match():
    m = match_findings([_cand(loc="contracts/deep/Vault.sol")], [_gt(loc="Vault.sol")])
    assert len(m.matched) == 1


# ── scoring (FR-008/009/010) ─────────────────────────────────────────────────

def test_per_tag_recall_arithmetic():
    truth = [_gt("H-01", _TAG), _gt("H-02", _TAG, loc="A.sol", fn="a"),
             _gt("H-03", _OTHER, loc="B.sol", fn="b")]
    m = match_findings([_cand(fid="C-1", tag=_TAG)], truth)          # matches H-01 only
    card = score(_case(truth), "fake", m, 1)
    assert card.per_tag_recall["reentrancy"] == 0.5                   # 1 of 2
    assert card.per_tag_recall["incorrect-access-control"] == 0.0     # 0 of 1
    assert card.recall == round(1 / 3, 4)


def test_tag_absent_from_truth_is_not_reported_as_zero():
    truth = [_gt("H-01", _TAG)]
    card = score(_case(truth), "fake", match_findings([], truth), 0)
    assert "incorrect-access-control" not in card.per_tag_recall      # n/a, never a fake 0


def test_empty_detector_yields_zero_recall_and_names_all_missed():
    truth = [_gt("H-01"), _gt("H-02", loc="A.sol", fn="a")]
    card = score(_case(truth), "fake", match_findings([], truth), 0)
    assert card.recall == 0.0
    assert {g["finding_id"] for g in card.missed_named} == {"H-01", "H-02"}


def test_scoring_is_deterministic():
    truth = [_gt("H-01"), _gt("H-02", loc="A.sol", fn="a")]
    prod = [_cand(fid="C-1"), _cand(fid="C-2", loc="A.sol", fn="a")]
    a = score(_case(truth), "fake", match_findings(prod, truth), 2).to_dict()
    b = score(_case(truth), "fake", match_findings(prod, truth), 2).to_dict()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ── detectors: pluggable + the honest floor (US2, FR-005/013) ────────────────

def test_fake_detector_plugs_in_without_touching_scoring(tmp_path, monkeypatch):
    truth = [_gt()]
    monkeypatch.setitem(bench.DETECTORS, "fake", lambda case: [_cand()])
    det = bench.resolve_detector("fake", None)
    card = score(_case(truth), "fake", match_findings(det(_case(truth)), truth), 1)
    assert card.recall == 1.0


def _sol_case(tmp_path: Path, body: str) -> Case:
    tgt = tmp_path / "t"
    tgt.mkdir()
    (tgt / "V.sol").write_text(
        f"contract V {{\n  function f() public {{\n{body}\n  }}\n}}\n", encoding="utf-8")
    return Case(case_id="s", truth=[], target_path=tgt)


def test_heuristic_maps_tx_origin(tmp_path):
    out = heuristic_detector(_sol_case(tmp_path, "    require(tx.origin == owner);"))
    assert out and out[0].bastet_tag is BastetTag.incorrect_access_control


def test_heuristic_maps_external_call_before_state_write_to_reentrancy(tmp_path):
    out = heuristic_detector(_sol_case(
        tmp_path, '    msg.sender.call{value: 1}("");\n    balance = 0;'))
    assert any(c.bastet_tag is BastetTag.reentrancy for c in out)


def test_heuristic_emits_nothing_for_flags_without_an_honest_tag(tmp_path):
    # assembly / .transfer( fire red flags but have NO defensible tag → emit nothing.
    out = heuristic_detector(_sol_case(
        tmp_path, "    assembly { let x := 1 }\n    payable(a).transfer(1);"))
    assert out == []


def test_heuristic_emits_nothing_on_business_logic(tmp_path):
    # The honest floor: no red-flag substring at all → the H-01 class is invisible to it.
    out = heuristic_detector(_sol_case(
        tmp_path, "    uint c = a * 1e18 / b;\n    shares[msg.sender] = c;"))
    assert out == []


def test_repo_url_case_needs_local_path(tmp_path):
    with pytest.raises(BenchError):
        heuristic_detector(Case(case_id="r", truth=[], repo_url="https://x/y"))
