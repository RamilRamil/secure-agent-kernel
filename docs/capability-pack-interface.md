---
type: Reference
title: The CapabilityPack interface
description: The declarative contract a task-specific pack plugs into — and the tested property that a pack can never lower a kernel guardrail.
tags: [capability-pack, boundary, kernel, security]
lang: en
status: stable
generated:
  by: secure-agent-kernel/claude-opus-4.8
  at: 2026-08-08T00:00:00Z
sources:
  - resource: sr_agent/orchestrator/pack.py
    title: CapabilityPack / ActionSpec / PackContext
  - resource: tests/security/test_hostile_pack.py
    title: Hostile-pack property (H1/H2/H3)
  - resource: tests/architecture/test_kernel_pack_boundary.py
    title: Boundary checks (B1..B5)
  - resource: tests/fixtures/pack/fixture_pack.py
    title: FIXTURE_PACK — a kernel-only pack
---

# The CapabilityPack interface

> 🇷🇺 Русская версия: [capability-pack-interface.ru.md](capability-pack-interface.ru.md)

A task rides on the kernel as a **`CapabilityPack`** — a single frozen dataclass of data
plus callables. No base class, no registry, no discovery (research R1): exactly one pack
exists today and is wired explicitly by the composition root. The whole security value is
in what the interface **withholds**: a pack contributes capability but has no field, and
no handle, that could weaken a guarantee.

## The contract

```python
CapabilityPack(
    name,                 # str
    actions,              # Mapping[str, ActionSpec] — the pack's DOMAIN action ids
    tools,                # Sequence[ToolDefinition]
    privileged_statuses,  # frozenset[str] — pack-declared statuses only human_input may set
    reasoning_prompt,     # str
    dispatch,             # (Action, PackContext) -> str            (read-only / approved path)
    execute_confirmed,    # (Action, PackContext) -> (str, event?)  (post-OOB path)
    persist_finding,      # (payload, PackContext) -> artifact?      (kernel then writes it)
    domain_escalation,    # extra escalation triggers
    signal_from,          # domain signal from a model action
)
```

- **Open taxonomy.** `Action.action_type` is a free string. The pack owns the **domain**
  action ids (`pack.actions`) *and* its **privileged-status set**; the kernel retains only
  the generic, non-domain ids it provides to every pack — the control/memory machinery
  (`write_memory`, `request_human_confirmation`) **and** the scope-bounded reads
  (`read_file`, `search_code`, decision D6). `validate_action` resolves an id against
  `KERNEL_GENERIC_ACTIONS ∪ pack.actions` and **fails closed** on a miss.
- **`ActionSpec`** = `(action_class, is_reversible, validate_params)`. `action_class` is
  the pack's **only** confirmation lever — the kernel derives "`write_execute ⇒ confirm`"
  itself. A missing or permissive `validate_params` **fails closed**: the kernel whitelist,
  path-containment, and sandbox still apply.
- **`PackContext`** is the narrow, least-privilege surface handed to every pack callable:
  `scope_root`, `sandbox`, `wrap_data`. It is **never the loop and never a memory-write
  handle**, and it carries **no PoC state** (`poc_dir`/`poc_generator` are pack-side,
  decision D2). A pack returns domain artifacts (`persist_finding` returns the finding;
  `execute_confirmed` returns a status event); the *kernel* performs every memory write and
  sets the source tier. (A transitional `__getattr__` shim still answers `ctx.audit_root`
  → `scope_root` with a `DeprecationWarning` until the audit pack migrates; removed in PR-3.)

## The property: a pack can never lower a guardrail

This is the constitution's Principle-III property, and it is **tested, not asserted**
(`tests/security/test_hostile_pack.py`). A hostile pack is just a bad value built inline —
no subclassing needed:

- **H1 — cannot skip confirmation.** A pack that declares an action `write_execute` cannot
  also declare it skip-confirmation: `validate_action` sets `human_confirmation = False`
  (pending) from the class, and a structural test asserts **no** field named
  `requires_confirmation` / `skip_confirmation` / `human_confirmation` exists on
  `ActionSpec` or `CapabilityPack`.
- **H2 — cannot forge a `human_input` tier.** `PackContext` has no `memory` field (the test
  pins its field set exactly), so a pack cannot write memory at all; the kernel persists
  model-reported findings as `external_llm_output`, never promoted to `human_input`.
- **H3 — cannot opt out of containment.** Even with a permissive `validate_params`, the
  kernel-owned `read_file` refuses paths outside `scope_root`, and `DockerSandbox.run`
  defaults to `--network none` — a pack only gets the sandbox *handle*, not its policy.
- **H4 — under-declaration cannot bypass a status gate.** A pack declaring an *empty* or
  *reduced* `privileged_statuses` gates exactly its declared set (empty ⇒ "nothing
  privileged", not "gate disabled") — it cannot bypass a kernel-enforced status, because
  post-taxonomy the kernel enforces none of its own.

### The honest residual

A pack **can** mislabel a write as `read_only` (`action_class` is genuinely its lever).
That residual is deliberately pinned by `test_H1_class_mislabel_is_bounded_not_open`: even
mislabeled, `read_file`/`search_code` enforce `scope_root` and tool execution runs in the
network-isolated sandbox. The door is **bounded by unconditional containment, not closed** —
and the test exists so any future change that widens it is noticed.

## The boundary is machine-checked

`tests/architecture/test_kernel_pack_boundary.py` enforces the direction of dependency and
the post-split (feature 048) seam:

| check | guarantee |
|-------|-----------|
| **B1** | no kernel module imports `sr_agent.packs` (AST-based, so a string in a comment never counts) |
| **B2** | no kernel module reads an audit-owned config field (`alchemy_api_key`, `tenderly_api_key`, `workspaces_root`, `git_token`, `smartgraphical_root`) |
| **B3** | no kernel routing module carries a stage/PoC slot identifier or the `sr-stage2` default — routing is shape-agnostic |
| **B4** | the kernel router resolves an arbitrary `Mapping[str, str]`; a missing role raises `KeyError`, never a silent default |
| **B5** | no kernel test imports audit-only tooling (`scripts.*` / `frontend.*`) |
| **B6** | the open taxonomy lowered no guardrail: `action_type` is not a closed domain enum; no domain action id or privileged status is operative in `sr_agent/` (AST-checked); `KERNEL_GENERIC_ACTIONS` and `LOOP_TERMINALS` are exactly their task-agnostic sets and disjoint; and `PackContext` carries no `poc_*` / `audit_root` real field |

B6 (feature 001) is the anti-regression latch for the open taxonomy — reintroduce a domain
id or a `poc_*` field into the kernel and it goes red. It supersedes the old B3 naming-residue
tolerance: `poc_dir`/`poc_generator` and `audit_root`/`AuditResult` have now left the kernel
outright (only a transitional `audit_root` shim remains, removed in PR-3), so there is no
longer a tolerated naming residue at the action/context layer — see [kernel.md](kernel.md).

## Proven with no task code

`tests/fixtures/pack/FIXTURE_PACK` is a minimal, **kernel-only** pack (it imports zero
audit modules — the boundary guards scan it). It reproduces the same MI attack-surface
classes as the real audit pack — a read-only tool whose output is DATA-wrapped, a
`write_execute` action reachable only after OOB confirmation, and a memory-write path where
the kernel sets the tier — so the MI guarantees are demonstrated in this repo with no task
present. See [mi-eval.md](mi-eval.md).

There is intentionally **no dynamic plugin registry** (YAGNI). The one pack is wired
explicitly; the narrow, withholding boundary *is* the deliverable.
