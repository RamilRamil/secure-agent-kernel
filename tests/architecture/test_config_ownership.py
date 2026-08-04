"""US4 / FR-001 (kernel side) — KernelConfig owns exactly the 13 kernel fields.

Written test-first (Phase A): imports `KernelConfig`, which does not exist yet, so
it is RED until T009 lands. This is the KERNEL-side half of the ownership check —
it imports only `sr_agent.config` and stays clean of any audit dependency, so it
can live in the kernel repo. The cross-seam partition (KernelConfig ∪ AuditConfig
== the 22) is verified audit-side in tests/audit/architecture/test_config_partition.py,
because only the audit repo can import both sides.

See specs/048-repo-split/spec.md (Configuration ownership) and data-model.md.
"""
from __future__ import annotations

import dataclasses

KERNEL_13 = {
    "anthropic_api_key", "gemini_api_key", "openrouter_api_key",
    "secret_key",
    "memory_root", "knowledge_root", "confirmations_root", "relay_root", "lessons_root",
    "langfuse_secret_key", "langfuse_public_key", "langfuse_host", "langfuse_enabled",
}
AUDIT_5 = {
    "alchemy_api_key", "tenderly_api_key", "workspaces_root", "git_token",
    "smartgraphical_root",
}
ROLES_4 = {"stage1_model", "stage2_model", "stage3_model", "poc_model"}


def _field_names(cls) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def test_reference_sets_are_disjoint_and_total_22() -> None:
    """Sanity on the reference data this file asserts against."""
    assert len(KERNEL_13 | AUDIT_5 | ROLES_4) == 22
    assert KERNEL_13.isdisjoint(AUDIT_5)
    assert KERNEL_13.isdisjoint(ROLES_4)
    assert AUDIT_5.isdisjoint(ROLES_4)


def test_kernel_config_holds_exactly_the_13_kernel_fields() -> None:
    from sr_agent.config import KernelConfig

    fields = _field_names(KernelConfig)
    assert fields == KERNEL_13, (
        f"KernelConfig must hold exactly the 13 kernel fields.\n"
        f"  unexpected (audit/routing leaked into kernel): {sorted(fields - KERNEL_13)}\n"
        f"  missing: {sorted(KERNEL_13 - fields)}"
    )


def test_kernel_config_has_no_audit_or_routing_field() -> None:
    from sr_agent.config import KernelConfig

    leaked = _field_names(KernelConfig) & (AUDIT_5 | ROLES_4)
    assert leaked == set(), f"audit/routing fields must not live in KernelConfig: {sorted(leaked)}"
