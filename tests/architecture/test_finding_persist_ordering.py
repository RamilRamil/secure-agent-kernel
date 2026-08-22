"""The two loop paths cannot drift apart on finding provenance (kernel/005).

The defect this feature fixed was an ORDERING mistake made identically in two
places, and the fix has to be made in two places too. Behavioural tests cover
what each path does today
(`tests/unit/test_finding_provenance_paths.py`, one table, both runners); these
are the structural guards that survive a refactor the table was not updated for.

They are deliberately about shape, not behaviour. A structural test that tried to
express "the finding is persisted after the action resolves" as a position in the
source would be wrong: in the terminal branches the persist correctly comes
early, because there the outcome is already known — no action ran.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

LOOP = Path(__file__).resolve().parents[2] / "sr_agent" / "orchestrator" / "loop.py"
TREE = ast.parse(LOOP.read_text())


def _func(name: str) -> ast.FunctionDef:
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in loop.py")


def _calls(node: ast.AST, attr: str) -> list[ast.Call]:
    return [
        n for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr
    ]


def _provenance_factories(node: ast.AST) -> list[str]:
    """The `FindingProvenance.<factory>` names used inside `node`, in order."""
    out = []
    for call in ast.walk(node):
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "FindingProvenance"):
            out.append(call.func.attr)
    return out


# ── The single choke point ───────────────────────────────────────────────────


def test_persist_finding_is_reached_through_one_helper() -> None:
    """Eight exits × an inline `if agent_action.finding` is how the guard, the
    bookkeeping and the stamp end up right in one path and wrong in the other."""
    callers = {
        f.name for f in ast.walk(TREE)
        if isinstance(f, ast.FunctionDef) and _calls(f, "_persist_finding")
    }
    assert callers == {"_record_finding"}


@pytest.mark.parametrize("path", ["run", "run_turn"])
def test_every_record_finding_call_names_an_outcome(path: str) -> None:
    """The stamp is a required argument, and it must be constructed at the call
    site. A bare name would let a single value be computed once and reused across
    exits that did not share an outcome."""
    calls = _calls(_func(path), "_record_finding")
    assert calls, f"{path} no longer records findings at all"
    for call in calls:
        assert len(call.args) >= 2, "provenance is not optional"
        arg = call.args[1]
        assert isinstance(arg, ast.Call), "provenance must be built here, not passed through"
        assert isinstance(arg.func, ast.Attribute)
        assert isinstance(arg.func.value, ast.Name) and arg.func.value.id == "FindingProvenance"


# ── The two paths agree ──────────────────────────────────────────────────────


def test_both_paths_cover_the_same_outcomes() -> None:
    """A count comparison, and only that — it cannot prove the two paths mean the
    same thing. What it does catch is the failure mode that actually happened:
    one path losing an exit while the other keeps it."""
    batch = sorted(_provenance_factories(_func("run")))
    chat = sorted(_provenance_factories(_func("run_turn")))
    assert batch == chat, f"batch {batch} and chat {chat} no longer cover the same outcomes"
    assert batch.count("of") == 1
    assert batch.count("not_dispatched") == 3


@pytest.mark.parametrize("path", ["run", "run_turn"])
def test_the_dispatch_stamp_is_taken_after_the_dispatch(path: str) -> None:
    """`FindingProvenance.of` reads a result, so it has to come after the call
    that produced one. A pre-computed stamp would be a guess."""
    func = _func(path)
    execute = [c for c in _calls(func, "execute")]
    assert len(execute) == 1
    of_calls = [
        c for c in ast.walk(func)
        if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
            and c.func.attr == "of" and isinstance(c.func.value, ast.Name)
            and c.func.value.id == "FindingProvenance")
    ]
    assert len(of_calls) == 1
    assert of_calls[0].lineno > execute[0].lineno


# ── FR-011: the kernel's view of the domain object does not deepen ───────────


def test_the_kernel_reads_the_domain_finding_no_deeper_than_location() -> None:
    """The kernel writes a pack-built object it must not understand.

    `location` derives the record target and `finding_id` is session bookkeeping;
    `model_dump` stores the body opaquely. Reaching for `.severity` or
    `.preconditions` here would be the kernel starting to interpret a domain
    artifact, which is the boundary Principle III draws. If a future change needs
    another attribute, it needs a decision, not a quiet addition.
    """
    func = _func("_persist_finding")
    touched: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "finding":
            touched.add(node.attr)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "finding"
                and isinstance(node.args[1], ast.Constant)):
            touched.add(node.args[1].value)
    assert touched <= {"location", "model_dump", "finding_id"}, touched


# ── FR-004: no grounding claim anywhere in the shape ─────────────────────────


@pytest.mark.parametrize("module", [
    "sr_agent/models/memory.py",
    "sr_agent/models/dispatch.py",
    "sr_agent/orchestrator/loop.py",
])
def test_no_field_claims_a_finding_is_grounded(module: str) -> None:
    """The kernel observes co-occurrence within one model turn, never derivation.

    A field asserting the stronger thing would create an evidence tier nothing
    verified, and a consumer would build proof on it. The word is allowed in
    prose — that prose is what explains the absence — so this looks at annotated
    field names only.
    """
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / module).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assert "ground" not in node.target.id.lower(), node.target.id
