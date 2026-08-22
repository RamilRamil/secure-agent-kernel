"""`write_memory` param policy (feature 002, FR-004 / D11 / D12).

`write_memory` carried `_noop_validate` until this feature: a blessed, non-gated
kernel-generic id whose params nobody checked. The gate here is the EXPLICIT half
of D12 — the structural half is in the executor, which builds the record from a
fixed shape and never reads a forbidden key at all.

The explicit half exists because an ignored field is a claim the model made about
itself that the kernel silently accepted and then could not see. It is also what
makes the defence testable: a stripped attempt is indistinguishable from an
attempt never made.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sr_agent.models.action import Action, ValidationStatus
from sr_agent.models.dispatch import MAX_PAYLOAD_BODY_BYTES
from sr_agent.orchestrator.action import validate_action

# Identity and signature fields a model must never author. Mirrors the set
# `003`'s H5 tests pin on `DispatchPayload`, plus the composition fields `004`
# added. `target` is deliberately NOT here — see the D11 tests below.
FORBIDDEN = [
    "source_type", "hmac", "supersedes", "status_change", "status", "seq",
    "chain_prev", "log_sequence", "record_id", "project_id", "session_id", "tool",
]


def _validate(params: dict, tmp_path: Path):
    return validate_action(Action(action_type="write_memory", params=params), tmp_path)


def _ok(tmp_path: Path, **extra) -> dict:
    return dict({"note": "withdraw() has no reentrancy guard"}, **extra)


# ── T002: forged provenance is refused, not ignored ──────────────────────────


@pytest.mark.parametrize("field", FORBIDDEN)
def test_forbidden_param_is_rejected_not_stripped(field: str, tmp_path: Path) -> None:
    """One key at a time, so a validator that catches only the first still fails."""
    result = _validate(_ok(tmp_path, **{field: "human_input"}), tmp_path)
    assert result.status is ValidationStatus.rejected
    assert field in (result.rejection_reason or "")


def test_all_forbidden_params_at_once_are_rejected(tmp_path: Path) -> None:
    result = _validate(_ok(tmp_path, **{f: "x" for f in FORBIDDEN}), tmp_path)
    assert result.status is ValidationStatus.rejected


def test_a_clean_note_is_approved(tmp_path: Path) -> None:
    assert _validate(_ok(tmp_path), tmp_path).status is ValidationStatus.approved


# ── T003: the note itself ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "params",
    [
        {},                       # missing
        {"note": ""},             # empty
        {"note": "   \n\t "},     # whitespace only
        {"note": 42},             # not a string
        {"note": None},
        {"note": {"a": "b"}},
    ],
    ids=["missing", "empty", "whitespace", "int", "none", "dict"],
)
def test_unusable_note_is_rejected(params: dict, tmp_path: Path) -> None:
    """Fail closed, not a silent no-op that reads back to the model as success."""
    assert _validate(params, tmp_path).status is ValidationStatus.rejected


def test_note_at_the_limit_is_accepted(tmp_path: Path) -> None:
    result = _validate({"note": "a" * MAX_PAYLOAD_BODY_BYTES}, tmp_path)
    assert result.status is ValidationStatus.approved


def test_note_over_the_limit_is_rejected(tmp_path: Path) -> None:
    result = _validate({"note": "a" * (MAX_PAYLOAD_BODY_BYTES + 1)}, tmp_path)
    assert result.status is ValidationStatus.rejected
    assert str(MAX_PAYLOAD_BODY_BYTES) in (result.rejection_reason or "")


def test_note_limit_counts_bytes_not_characters(tmp_path: Path) -> None:
    """A multi-byte note must not slip past a length check written in characters."""
    note = "я" * MAX_PAYLOAD_BODY_BYTES   # 2 bytes each in UTF-8
    assert _validate({"note": note}, tmp_path).status is ValidationStatus.rejected


# ── T004: the target is honoured, but bounded (D11) ──────────────────────────


def test_target_is_accepted(tmp_path: Path) -> None:
    """The one params key this path honours: an agent moves across targets."""
    result = _validate(_ok(tmp_path, target="Vault.sol"), tmp_path)
    assert result.status is ValidationStatus.approved


def test_target_may_be_absent(tmp_path: Path) -> None:
    assert _validate(_ok(tmp_path), tmp_path).status is ValidationStatus.approved


@pytest.mark.parametrize(
    "target",
    ["", "   ", "a\x00b", "a\nb", "a\tb", "\x1b[31m", "x" * 201],
    ids=["empty", "whitespace", "nul", "newline", "tab", "escape", "too-long"],
)
def test_unusable_target_is_rejected(target: str, tmp_path: Path) -> None:
    """A target becomes a filename. Without the bound these are an OSError deep
    inside the append, not a refusal the model can read."""
    assert _validate(_ok(tmp_path, target=target), tmp_path).status is ValidationStatus.rejected


def test_target_limit_counts_bytes_not_characters(tmp_path: Path) -> None:
    result = _validate(_ok(tmp_path, target="я" * 150), tmp_path)   # 300 bytes
    assert result.status is ValidationStatus.rejected


def test_traversal_shaped_target_is_accepted_and_contained(tmp_path: Path) -> None:
    """`target` is a memory partition label, not a path.

    It is accepted because it is not a containment boundary: `project_id` comes
    from the principal, and `_target_stem` renders separators inert. Rejecting it
    would imply the store defends itself here, which it does not — it defends
    itself one layer down.
    """
    from sr_agent.memory.episodic import EpisodicMemory

    hostile = "../../etc/passwd"
    assert _validate(_ok(tmp_path, target=hostile), tmp_path).status is ValidationStatus.approved

    memory = EpisodicMemory(tmp_path, b"\x00" * 32)
    path = memory._path("proj1", hostile)
    assert path.parent == tmp_path / "proj1"
    assert path.name == ".._.._etc_passwd.jsonl"
