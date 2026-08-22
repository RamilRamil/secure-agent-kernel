"""The CapabilityPack contract (feature 004, kernel side).

The single, declarative interface through which a task-specific pack contributes
capability to the task-agnostic kernel. Frozen dataclasses holding data +
callables (research R1) — no base class, no registry, no discovery. Exactly one
pack (`audit`) exists today and is wired explicitly in `cli.py`.

The whole security point (data-model.md "Constraint"): a `CapabilityPack` has
NO field that lets a pack set an action's confirmation requirement, its trust
tier, or opt a tool out of validation/containment/sandbox. Those are
kernel-derived and untouchable — the kernel reads `action_class` and derives
"write_execute ⇒ confirm" itself (R2); it sets the memory source tier itself;
it applies the sandbox + path-containment itself. This absence is what the
hostile-pack test verifies.

All annotations are strings (`from __future__ import annotations`) so this
kernel module imports nothing at runtime and cannot create an import cycle with
the loop/action modules that consume it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Mapping, Sequence

if TYPE_CHECKING:
    from pathlib import Path

    from sr_agent.guardrails.escalation import EscalationResult
    from sr_agent.llm_core.schemas import AgentAction
    from sr_agent.models.action import Action, ActionClass
    from sr_agent.models.dispatch import DispatchResult
    from sr_agent.tools.registry import ToolDefinition
    from sr_agent.tools.sandbox import DockerSandbox


@dataclass(frozen=True)
class ActionSpec:
    """Per-action metadata a pack supplies; the kernel's `validate_action` reads it.

    `action_class` is the pack's ONLY confirmation lever — the kernel derives the
    OOB-confirmation requirement from it (`write_execute ⇒ confirm`), and a
    missing/permissive `validate_params` fails closed (kernel whitelist +
    path-containment + sandbox still apply).
    """
    action_class: "ActionClass"
    is_reversible: bool
    validate_params: Callable[["Action", "Path"], "str | None"]


@dataclass(frozen=True)
class PackContext:
    """The narrow, least-privilege surface passed to pack callables (R8).

    Never the loop, never kernel internals — only kernel-sanctioned capabilities.
    Pack output re-enters context through `wrap_data`.

    Deliberately exposes NO memory handle (FR-006, strengthened during
    implementation 2026-07-03): a pack has no way to write memory, so it
    structurally cannot forge a `human_input`-tier record. The kernel owns every
    memory write and sets the source tier itself; the pack only *returns* domain
    artifacts (`persist_finding` returns the finding; `execute_confirmed` returns
    a status event) which the kernel then persists. Prior findings needed by
    `domain_escalation` are passed to it as arguments, not read from here.

    PoC state is NOT here (decision D2): a PoC output directory / body generator
    is a pack-side concern, so a pack that runs a write_execute PoC path carries
    its own — the kernel context stays task-agnostic (no `poc_dir`/`poc_generator`).

    `operation_id` / `transition_key` (feature 003) are read-only kernel-derived
    identifiers, present so a pack can make an external effect idempotent under
    the same identity the kernel commits under. They are NOT trusted back: a
    projection reads the identity off the kernel-authored envelope, never off a
    pack-supplied body.
    """
    scope_root: "Path"
    sandbox: "DockerSandbox"
    wrap_data: Callable[..., str]
    operation_id: str | None = None
    transition_key: str | None = None
    # Bound include set for kernel read tools (D20). A value, not a handle:
    # knowing the policy grants no write capability. None is the test shim;
    # production dispatch on a bound session supplies the policy.
    scope_policy: object | None = None


@dataclass(frozen=True)
class CapabilityPack:
    """A declarative bundle of task-specific capability the kernel consumes.

    See specs/004-kernel-pack-boundary/contracts/pack-interface.md for the full
    contract (what a pack provides; what the kernel guarantees regardless).
    """
    name: str
    actions: Mapping[str, ActionSpec]
    tools: Sequence["ToolDefinition"]
    privileged_statuses: frozenset[str]
    reasoning_prompt: str
    # Feature 003: the persistable contract is structured, not an opaque string.
    # `adapt_legacy_dispatch` wraps string-returning fixture packs for tests;
    # production packs return `DispatchResult` so the kernel can persist what the
    # pack computed without the pack ever holding a memory handle.
    dispatch: Callable[["Action", PackContext], "DispatchResult"]
    execute_confirmed: Callable[["Action", PackContext], "tuple[str, object | None]"]
    persist_finding: Callable[[dict, PackContext], "object | None"]
    domain_escalation: Callable[..., "EscalationResult | None"]
    signal_from: Callable[["AgentAction"], "object | None"]


def adapt_legacy_dispatch(
    dispatch: "Callable[[Action, PackContext], str]",
) -> "Callable[[Action, PackContext], DispatchResult]":
    """Wrap a string-returning dispatch as a payload-less `DispatchResult`.

    Test-only, and payload-less on purpose: a string carries no structure, so
    there is nothing the kernel could honestly persist from it. Inventing a
    payload here would manufacture provenance for output that never had any.
    """
    from sr_agent.models.dispatch import DispatchResult, DispatchStatus

    def adapted(action: "Action", ctx: PackContext) -> "DispatchResult":
        return DispatchResult(status=DispatchStatus.ran, body=dispatch(action, ctx))

    return adapted
