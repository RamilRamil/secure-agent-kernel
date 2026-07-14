"""Spec 017 US1: offline, deterministic query layer over a checked-in fixture map.

Drives tests/fixtures/codegraph_sample.json — no graphify, no network, no LLM.
"""
from pathlib import Path

import pytest

from scripts.codegraph import CodeGraph, CodeGraphFormatError

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "codegraph_sample.json"


@pytest.fixture
def g() -> CodeGraph:
    return CodeGraph.load(FIXTURE)


def test_find_resolves_short_name(g):
    hits = g.find("add")
    assert [n.id for n in hits] == ["util_add"]


def test_find_missing_returns_empty(g):
    assert g.find("nope") == []


def test_callers_of_util_add(g):
    callers = g.callers("util_add")
    assert {e.source for e in callers} == {"main_run", "util_calc_total"}
    assert all(e.relation == "calls" for e in callers)
    assert all(e.confidence == "EXTRACTED" for e in callers)  # confidence surfaced (FR-005)


def test_callees_of_main_run(g):
    assert {e.target for e in g.callees("main_run")} == {"util_add", "util_calc"}


def test_dependencies_of_main_module(g):
    deps = g.dependencies("main")
    assert {e.relation for e in deps} <= {"imports", "imports_from"}
    assert {e.target for e in deps} == {"util", "util_add", "util_calc"}


def test_neighbors_includes_in_and_out(g):
    nb = g.neighbors("util_calc")
    pairs = {(e.source, e.target) for e in nb}
    assert ("util", "util_calc") in pairs          # inbound contains
    assert ("main_run", "util_calc") in pairs        # inbound call
    assert ("util_calc", "util_calc_total") in pairs  # outbound method


def test_path_returns_chain(g):
    chain = g.path("main_run", "util_add")
    assert chain, "expected a path main_run -> util_add"
    assert chain[0].source == "main_run"
    assert chain[-1].target == "util_add"


def test_path_absent_is_empty(g):
    # util_add is a leaf (no outbound edges) -> cannot reach main_run
    assert g.path("util_add", "main_run") == []


def test_module_summary_lists_children(g):
    s = g.module_summary("util")
    child_targets = {e.target for e in s["children"]}
    assert {"util_add", "util_calc"} <= child_targets


def test_query_ordering_is_deterministic(g):
    once = [e.source for e in g.callers("util_add")]
    twice = [e.source for e in CodeGraph.load(FIXTURE).callers("util_add")]
    assert once == twice


def test_malformed_map_raises_format_error():
    with pytest.raises(CodeGraphFormatError):
        CodeGraph.from_dict({"nodes": "not-a-list", "links": []})
    with pytest.raises(CodeGraphFormatError):
        CodeGraph.from_dict({"nodes": [], "links": [{"source": "a"}]})  # edge missing fields
