"""Offline test harness for the prover capability screen (feature 038, task T002).

Everything here is target-free and deterministic: a scripted `attempt_fn` (the cascade's sole
expensive seam), a placeholder non-target battery, and a minimal pre-registration fixture. The real
`attempt_fn` runs the 037 loop + the byte-unchanged oracle; these fakes stand in for it so the whole
cascade/map/pareto logic is exercised without a model, a network, or any target material.
"""
from __future__ import annotations

import os

os.environ.setdefault("SR_SECRET_KEY", "00" * 32)

import pytest

from scripts.capability_screen import AttemptResult, BatteryCase, Candidate, make_prereg

# ── placeholder battery: 3 closed-enum classes, 1 fix-bearing case each (cases_per_class=1) ──
CLASS_ENUM = ["rounding_low", "access_control_reentrancy", "narrow_precondition"]


@pytest.fixture
def placeholder_battery() -> list[BatteryCase]:
    """Non-target, pinned-by-label cases - no contract code, no target identifiers (FR-016)."""
    return [
        BatteryCase(case_id="case-alpha", finding_class="rounding_low"),
        BatteryCase(case_id="case-beta", finding_class="access_control_reentrancy"),
        BatteryCase(case_id="case-gamma", finding_class="narrow_precondition"),
    ]


class ScriptedAttempts:
    """A deterministic `attempt_fn(candidate, case, stage)` driven by a per-candidate script, plus a
    call log so tests can assert cheap-first ordering and no-expensive-on-rejected (A1/A5/SC-002).

    Per-candidate script keys (all optional):
      produced   : bool | list[bool]  - smoke plumbing signal (list ⇒ per smoke draw, for the retry)
      triggered  : bool               - trigger-screen bar
      verified   : list[bool] | int   - Bayes@N verdicts by call order (int ⇒ first-k verified)
      transient  : list[bool]         - inject timeout/503 by Bayes call order (verdict ignored there)
      screen_transient : list[bool]   - inject timeout/503 by TRIGGER-SCREEN call order (T025). A
                                        transient screen attempt carries no verdict: the cascade must
                                        retry it and, if it never resolves, mark the cell `unavailable`
                                        rather than concluding `not_triggered` (FR-014).
      tokens     : int  (default 100)
      seconds    : float (default 1.0)
    """

    def __init__(self, scripts: dict):
        self.scripts = scripts
        self.calls: list[tuple[str, str, str]] = []
        # counters keyed by (candidate, case) so a per-class script (one case per class) is isolated -
        # a shared per-candidate counter would leak one class's Bayes draws into the next.
        self._smoke_i: dict[tuple, int] = {}
        self._bayes_i: dict[tuple, int] = {}
        self._screen_i: dict[tuple, int] = {}

    def __call__(self, cand: str, case: str, stage: str) -> AttemptResult:
        self.calls.append((cand, case, stage))
        s = self.scripts[cand]
        tok, sec = s.get("tokens", 100), s.get("seconds", 1.0)

        if stage == "smoke":
            prod = s.get("produced", True)
            if isinstance(prod, (list, tuple)):
                i = self._smoke_i.get((cand, case), 0)
                self._smoke_i[(cand, case)] = i + 1
                produced = prod[i] if i < len(prod) else prod[-1]
            else:
                produced = bool(prod)
            return AttemptResult(produced=produced, triggered=False, verified=False,
                                 tokens=tok, seconds=sec)

        if stage == "screen":
            i = self._screen_i.get((cand, case), 0)
            self._screen_i[(cand, case)] = i + 1
            strans = s.get("screen_transient", [])
            if i < len(strans) and strans[i]:
                # a transient carries NO verdict - triggered=False here must never be read as one
                return AttemptResult(produced=False, triggered=False, verified=False,
                                     tokens=tok, seconds=sec, transient=True)
            return AttemptResult(produced=True, triggered=bool(s.get("triggered", False)),
                                 verified=False, tokens=tok, seconds=sec)

        # bayes
        i = self._bayes_i.get((cand, case), 0)
        self._bayes_i[(cand, case)] = i + 1
        trans = s.get("transient", [])
        if i < len(trans) and trans[i]:
            return AttemptResult(produced=True, triggered=True, verified=False,
                                 tokens=tok, seconds=sec, transient=True)
        ver = s.get("verified", 0)
        if isinstance(ver, (list, tuple)):
            verified = bool(ver[i]) if i < len(ver) else False
        else:
            verified = i < int(ver)
        return AttemptResult(produced=True, triggered=True, verified=verified, tokens=tok, seconds=sec)

    def count(self, *, stage: str | None = None, cand: str | None = None) -> int:
        return sum(1 for (c, _, st) in self.calls
                   if (stage is None or st == stage) and (cand is None or c == cand))

    def order(self, stage: str) -> list[str]:
        """Candidate ids in the order they hit `stage`, de-duplicated (for cheap-first assertions)."""
        seen, out = set(), []
        for (c, _, st) in self.calls:
            if st == stage and c not in seen:
                seen.add(c)
                out.append(c)
        return out


@pytest.fixture
def scripted():
    return ScriptedAttempts


@pytest.fixture
def prereg_record():
    """A minimal frozen pre-registration record over the placeholder battery, cases_per_class=1."""
    def _make(candidates: list[Candidate], *, n: int = 6, cases_per_class: int = 1,
              credible_level: float = 0.95, bench_id: str = "placeholder-battery"):
        return make_prereg(
            candidates=candidates, class_enum=CLASS_ENUM,
            n_by_class={c: n for c in CLASS_ENUM}, cases_per_class=cases_per_class,
            credible_level=credible_level, pinned_bench_id=bench_id,
            price_table={c.id: 0.001 for c in candidates}, ts="2026-07-26T00:00:00Z")
    return _make
