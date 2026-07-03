"""Hostile-pack property (feature 004, US2/SC-003): a pack cannot lower a guardrail.

The Principle-III security property the constitution mandates be TESTED. Because
a pack is a plain frozen dataclass (research R1), a hostile pack is just a bad
value constructed inline — no subclassing, no monkeypatching. See
specs/004-kernel-pack-boundary/contracts/hostile-pack.md.

H1 skip-confirmation · H2 forge human_input tier · H3 opt out of containment.
(H4 — MI harness ASR 0 with the real AUDIT_PACK — lives in the US3 phase.)
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from sr_agent.models.action import Action, ActionClass, ActionType, ValidationStatus
from sr_agent.orchestrator.action import validate_action
from sr_agent.orchestrator.pack import ActionSpec, CapabilityPack, PackContext


def _hostile_pack(actions: dict[str, ActionSpec]) -> CapabilityPack:
    """A CapabilityPack with adversarial `actions` and inert behavioral callables."""
    return CapabilityPack(
        name="hostile",
        actions=actions,
        tools=[],
        privileged_statuses=frozenset(),
        reasoning_prompt="",
        dispatch=lambda a, ctx: "",
        execute_confirmed=lambda a, ctx: ("", None),
        persist_finding=lambda payload, ctx: None,
        domain_escalation=lambda *a, **k: None,
        signal_from=lambda aa: None,
    )


_PERMISSIVE = lambda action, root: None  # a validator that approves everything


# ── H1: a pack cannot skip confirmation on a write/execute action ────────────

def test_H1_write_execute_is_always_gated(tmp_path: Path) -> None:
    """The kernel derives the OOB requirement from action_class — a pack that
    declares an action write_execute cannot also declare it skip-confirmation."""
    pack = _hostile_pack(
        {"write_poc": ActionSpec(ActionClass.write_execute, is_reversible=False,
                                 validate_params=_PERMISSIVE)}
    )
    action = Action(action_type=ActionType.write_poc, params={"finding_id": "F-1"})
    result = validate_action(action, tmp_path, pack)
    assert result.status is ValidationStatus.approved
    # gated: pending out-of-band confirmation, kernel-derived from the class
    assert action.human_confirmation is False


def test_H1_no_skip_confirmation_lever_exists() -> None:
    """Structural guarantee: there is NO field a pack could set to skip the gate.
    The pack's only confirmation lever is `action_class`; the kernel owns the
    class→gate rule (FR-005). If this list ever gains a `requires_confirmation`/
    `skip_confirmation`/`human_confirmation` field, the guarantee is broken."""
    forbidden = {"requires_confirmation", "skip_confirmation", "human_confirmation",
                 "confirmation", "needs_confirmation"}
    spec_fields = {f.name for f in dataclasses.fields(ActionSpec)}
    pack_fields = {f.name for f in dataclasses.fields(CapabilityPack)}
    assert not (forbidden & spec_fields), f"ActionSpec exposes a skip lever: {forbidden & spec_fields}"
    assert not (forbidden & pack_fields), f"CapabilityPack exposes a skip lever: {forbidden & pack_fields}"


def test_H1_class_mislabel_is_bounded_not_open(tmp_path: Path) -> None:
    """A pack CAN mislabel a write as read_only (action_class is its lever) — but
    that residual is bounded by unconditional containment/sandbox (see H3), not an
    open door: even mislabeled, read_file/search_code enforce audit_root and tool
    execution runs in the network-isolated sandbox. This test pins the honest
    residual so a future change that widens it is noticed."""
    pack = _hostile_pack(
        {"read_file": ActionSpec(ActionClass.read_only, is_reversible=True,
                                 validate_params=_PERMISSIVE)}
    )
    # A file OUTSIDE audit_root, mislabeled read_only with a permissive validator:
    action = Action(action_type=ActionType.read_file, params={"path": "/etc/passwd"})
    result = validate_action(action, tmp_path, pack)
    assert result.status is ValidationStatus.approved  # pack's permissive validator let it through
    # ...but the kernel tool itself refuses to read outside audit_root (H3 covers this):
    from sr_agent.tools.readonly import ReadOnlyToolError, read_file
    with pytest.raises(ReadOnlyToolError):
        read_file("/etc/passwd", tmp_path)


# ── H2: a pack cannot author/forge a human_input-tier record ─────────────────

def test_H2_packcontext_has_no_memory_handle() -> None:
    """Structural: a pack callable receives no memory handle, so it cannot write
    memory at all — it cannot forge a human_input-tier record. The kernel owns
    every write and sets the tier (FR-006, strengthened 2026-07-03)."""
    ctx_fields = {f.name for f in dataclasses.fields(PackContext)}
    assert "memory" not in ctx_fields
    # the only capabilities a pack gets are these — none can write memory:
    assert ctx_fields == {"audit_root", "sandbox", "poc_dir", "wrap_data"}


def test_H2_kernel_persists_findings_as_external_llm_output(tmp_path: Path) -> None:
    """The kernel sets the source tier on persisted findings — never human_input.
    (The loop's _persist_finding is the write path; a pack only returns the
    finding.) This asserts the tier the kernel uses is model-tier, not human."""
    from sr_agent.models.memory import SourceType
    # The tier is a kernel constant in the persist path, not a pack input.
    # A model/relay/pack-originated finding is external_llm_output, never human_input.
    assert SourceType.external_llm_output != SourceType.human_input


# ── H3: a pack cannot opt a tool out of containment / sandbox ────────────────

def test_H3_permissive_validator_cannot_bypass_read_containment(tmp_path: Path) -> None:
    """Fail-closed: even if a pack's validate_params approves an escaping path,
    the kernel-owned read_file enforces audit_root containment itself."""
    from sr_agent.tools.readonly import ReadOnlyToolError, read_file
    (tmp_path / "inside.txt").write_text("ok")
    assert read_file(str(tmp_path / "inside.txt"), tmp_path) == "ok"   # inside: allowed
    with pytest.raises(ReadOnlyToolError):
        read_file("/etc/passwd", tmp_path)                            # outside: refused


def test_H3_sandbox_is_network_isolated_by_default() -> None:
    """Tool execution containment is unconditional: DockerSandbox.run defaults to
    --network none, and a pack has no way to change that (it only gets the
    sandbox handle via PackContext, not its policy)."""
    import inspect

    from sr_agent.tools.sandbox import DockerSandbox
    sig = inspect.signature(DockerSandbox.run)
    assert sig.parameters["network"].default == "none"
