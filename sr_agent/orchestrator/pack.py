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
    from sr_agent.memory.episodic import EpisodicMemory
    from sr_agent.models.action import Action, ActionClass
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
    Pack output re-enters context through `wrap_data`; memory writes go through
    `memory` but the kernel sets the source tier (a pack cannot forge human_input).
    """
    audit_root: "Path"
    sandbox: "DockerSandbox"
    poc_dir: "Path"
    wrap_data: Callable[..., str]
    memory: "EpisodicMemory"


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
    dispatch: Callable[["Action", PackContext], str]
    execute_confirmed: Callable[["Action", PackContext], "tuple[str, object | None]"]
    persist_finding: Callable[[dict, PackContext], "object | None"]
    domain_escalation: Callable[..., "EscalationResult | None"]
    signal_from: Callable[["AgentAction"], "object | None"]
