"""Feature 038 - pre-registration integrity (contract prereg.md, C1..C6). The append-only, frozen,
hash-guarded record is what makes the map trustworthy instead of fitted to the winner.

OFFLINE / TARGET-FREE. Records are written to `tmp_path` (outside the repo); no target identifier
enters any record (C6).
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("SR_SECRET_KEY", "00" * 32)

from pathlib import Path

import pytest

from scripts.capability_screen import (
    CapabilityScreenError, Candidate, active_record, content_hash, enforce_class,
    enforce_subject, load_prereg, make_prereg, write_prereg,
)

CLASS_ENUM = ["rounding_low", "access_control_reentrancy", "narrow_precondition"]


def _rec(candidates, *, bench="placeholder-battery", level=0.95, cases=1):
    return make_prereg(
        candidates=candidates, class_enum=CLASS_ENUM,
        n_by_class={c: 6 for c in CLASS_ENUM}, cases_per_class=cases,
        credible_level=level, pinned_bench_id=bench,
        price_table={c.id: 0.001 for c in candidates}, ts="2026-07-26T00:00:00Z")


def _cands():
    return [Candidate(id="m-a"), Candidate(id="m-b")]


# ── C1 - captured before any paid call; missing/mismatched record refuses ──────

def test_c1_no_record_refuses(tmp_path):
    with pytest.raises(CapabilityScreenError):
        load_prereg(tmp_path / "nope.jsonl")             # no record at all → loud


def test_c1_no_active_entry_for_bench_refuses(tmp_path):
    p = write_prereg(tmp_path / "prereg.jsonl", _rec(_cands(), bench="battery-x"))
    records = load_prereg(p)
    with pytest.raises(CapabilityScreenError):
        active_record(records, bench_id="a-different-battery")


# ── C2 - subject ∈ record ─────────────────────────────────────────────────────

def test_c2_unregistered_subject_rejected(tmp_path):
    rec = _rec(_cands())
    enforce_subject(rec, "m-a")                          # registered → ok
    with pytest.raises(CapabilityScreenError):
        enforce_subject(rec, "m-unregistered")


# ── C3 - append-only; late-add is a NEW entry, prior byte-identical ────────────

def test_c3_late_add_is_new_entry_prior_unchanged(tmp_path):
    p = tmp_path / "prereg.jsonl"
    write_prereg(p, _rec(_cands()))
    first_line = p.read_text(encoding="utf-8").splitlines()[0]
    first_hash = json.loads(first_line)["content_hash"]

    # late-add a third candidate ⇒ a NEW entry appended
    write_prereg(p, _rec(_cands() + [Candidate(id="m-c")]))
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0] == first_line                        # prior entry byte-identical
    assert json.loads(lines[0])["content_hash"] == first_hash
    assert json.loads(lines[1])["content_hash"] != first_hash


# ── C4 - frozen fields immutable: a post-hoc mutation is detected on load ──────

def test_c4_tampered_frozen_field_is_detected(tmp_path):
    p = tmp_path / "prereg.jsonl"
    write_prereg(p, _rec(_cands()))
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    rec["credible_level"] = 0.50                         # mutate a frozen field, keep old hash
    p.write_text(json.dumps(rec, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(CapabilityScreenError):
        load_prereg(p)                                   # hash mismatch → loud (C4)


def test_c4_content_hash_ignores_ts_metadata():
    a = _rec(_cands())
    b = make_prereg(
        candidates=_cands(), class_enum=CLASS_ENUM,
        n_by_class={c: 6 for c in CLASS_ENUM}, cases_per_class=1, credible_level=0.95,
        pinned_bench_id="placeholder-battery",
        price_table={"m-a": 0.001, "m-b": 0.001}, ts="A-DIFFERENT-TIMESTAMP")
    assert content_hash(a) == content_hash(b)            # ts is metadata, not a frozen field


# ── C5 - closed class enum ─────────────────────────────────────────────────────

def test_c5_out_of_enum_class_rejected(tmp_path):
    rec = _rec(_cands())
    enforce_class(rec, "rounding_low")                   # in enum → ok
    with pytest.raises(CapabilityScreenError):
        enforce_class(rec, "some_freetext_class")


# ── C6 - no target material in any record ─────────────────────────────────────

_TARGET_IDS = ["FooCooldown", "Foo", "FooCDO", "Foo", "Foo"]


def test_c6_record_carries_no_target_identifier(tmp_path):
    p = write_prereg(tmp_path / "prereg.jsonl", _rec(_cands()))
    blob = p.read_text(encoding="utf-8")
    offenders = [t for t in _TARGET_IDS if t in blob]
    assert not offenders, f"pre-registration record leaked target identifier(s): {offenders}"


# ── external-repo guard (records live under <target>/audit/) ───────────────────

def test_record_path_inside_repo_refused():
    import scripts.capability_screen as cs
    with pytest.raises(CapabilityScreenError):
        write_prereg(Path(cs._AGENT_ROOT) / "scripts" / "prereg.jsonl", _rec(_cands()))
