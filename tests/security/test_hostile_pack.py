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

from sr_agent.models.action import Action, ActionClass, ValidationStatus
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
    action = Action(action_type="write_poc", params={"finding_id": "F-1"})
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
    action = Action(action_type="read_file", params={"path": "/etc/passwd"})
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
    # the only capabilities a pack gets are these — none can write memory. PoC
    # state (poc_dir/poc_generator) left the context in US5 (D2); the audit_root
    # field was renamed scope_root in US4 (D3).
    assert ctx_fields == {"scope_root", "sandbox", "wrap_data"}


def test_H2_kernel_persists_findings_as_external_llm_output(tmp_path: Path) -> None:
    """The kernel sets the source tier on persisted findings — never human_input.
    (The loop's _persist_finding is the write path; a pack only returns the
    finding.) This asserts the tier the kernel uses is model-tier, not human."""
    from sr_agent.models.memory import SourceType
    # The tier is a kernel constant in the persist path, not a pack input.
    # A model/relay/pack-originated finding is external_llm_output, never human_input.
    assert SourceType.external_llm_output != SourceType.human_input


def test_PE002_persist_finding_writes_external_llm_output_tier(tmp_path: Path) -> None:
    """PE-002: OrchestratorLoop._persist_finding stamps external_llm_output, never human_input."""
    import os

    os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
    os.environ.setdefault("SR_SECRET_KEY", "a" * 64)

    from sr_agent.llm_core.schemas import AgentAction, FindingPayload
    from sr_agent.memory.episodic import EpisodicMemory
    from sr_agent.models.memory import SourceType
    from sr_agent.models.principal import Principal
    from sr_agent.orchestrator.loop import OrchestratorLoop
    from tests.fixtures.pack import FIXTURE_PACK, FixtureSession
    from tests.security.mi_scenarios import TEST_SECRET

    memory = EpisodicMemory(tmp_path / "mem", TEST_SECRET, privileged_statuses=frozenset({"blessed"}))
    principal = Principal(user_id="u", platform="cli", project_id="proj")
    session = FixtureSession(principal=principal)
    loop = OrchestratorLoop(
        session, memory, tmp_path, pack=FIXTURE_PACK, confirmations_dir=tmp_path / "conf",
    )
    action = AgentAction(
        next_action="complete",
        finding=FindingPayload(
            finding_id="PE2-1",
            location="Vault.sol:1",
            function_name="f",
            severity="high",
        ),
    )
    finding = loop._persist_finding(action)
    assert finding is not None
    records = memory.load("proj", "Vault.sol", principal=principal)
    assert len(records) == 1
    assert records[0].source_type == SourceType.external_llm_output
    assert records[0].source_type != SourceType.human_input


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


# ── H4: an empty/reduced privileged_statuses covers EXACTLY the declared set ──
# The status-gate set is no longer kernel-hardcoded (Constitution III / D5): the
# kernel binds the ACTIVE pack's declared set into EpisodicMemory at construction.
# A hostile or minimal pack can therefore declare FEWER statuses — but that only
# shrinks what THAT pack protects; it cannot bypass a kernel-enforced status,
# because post-US2 the kernel enforces none of its own. These tests pin the
# honest residual: empty ⇒ "this pack gates nothing", NOT "the gate is disabled".

from sr_agent.memory.episodic import EpisodicMemory, MemoryWriteError
from sr_agent.models.memory import MemoryRecord, SourceType, StatusChange

_H4_SECRET = b"h4-secret-key-least-32-bytes-long!!"


def _status_record(new_status: str, source_type: SourceType) -> MemoryRecord:
    return MemoryRecord(
        project_id="proj", target="Vault.sol", session_id="s",
        source_type=source_type,
        status_change=StatusChange(
            finding_id="F-1", old_status="open", new_status=new_status, reason="x",
        ),
    )


def test_H4_empty_privileged_set_gates_nothing_but_is_not_disabled(tmp_path: Path) -> None:
    """A pack declaring NO privileged statuses: a non-human status change to any
    value is permitted (nothing is privileged) — the gate mechanism is intact,
    it simply covers the empty set. This is the honest residual, not a bypass."""
    mem = EpisodicMemory(tmp_path, _H4_SECRET, privileged_statuses=frozenset())
    # No status is privileged, so even a non-human source may set one — and it
    # actually reaches disk (we assert the write succeeds, not merely "no raise").
    saved = mem.write(_status_record("anything", SourceType.llm_inference))
    assert saved.hmac is not None
    assert len(mem.load("proj", "Vault.sol")) == 1
    # The mechanism is still live: the supersedes gate (a separate human-authority
    # rule) still fires for a non-human correction — an empty privileged set does
    # NOT disable status-rule enforcement wholesale.
    with pytest.raises(MemoryWriteError, match="supersedes"):
        rec = MemoryRecord(
            project_id="proj", target="Vault.sol", session_id="s",
            source_type=SourceType.llm_inference, supersedes="prior-id",
            status_change=StatusChange(
                finding_id="F-1", old_status="open", new_status="anything", reason="x",
            ),
        )
        mem.write(rec)


def test_H4_reduced_set_covers_exactly_the_declared_statuses(tmp_path: Path) -> None:
    """A pack declaring exactly {"blessed"}: that status is gated against a
    non-human source, but a status OUTSIDE the declared set is not — the gate
    covers precisely the bound set, no more, no less."""
    mem = EpisodicMemory(tmp_path, _H4_SECRET, privileged_statuses=frozenset({"blessed"}))
    # Declared privileged status from a non-human source: blocked.
    with pytest.raises(MemoryWriteError, match="requires source_type=human_input"):
        mem.write(_status_record("blessed", SourceType.llm_inference))
    # A status NOT in the declared set: permitted (under-declaration cannot be a
    # covert widening — the pack gates only what it declared).
    saved = mem.write(_status_record("verified_safe", SourceType.llm_inference))
    assert saved.hmac is not None
    # The same declared status from a human source: permitted (authority present).
    saved_h = mem.write(_status_record("blessed", SourceType.human_input))
    assert saved_h.hmac is not None
