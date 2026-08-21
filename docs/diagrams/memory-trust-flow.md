---
type: Diagram
title: Memory trust flow — the HMAC record lifecycle
description: How a memory record is written and loaded — principal isolation, status gate, HMAC signing, silent drop on tamper, and the supersede chain.
tags: [flow, memory, hmac, source-type, diagram]
lang: en
status: stable
generated:
  by: secure-agent-kernel/claude-opus-4.8
  at: 2026-08-08T00:00:00Z
sources:
  - resource: sr_agent/memory/episodic.py
    title: EpisodicMemory.write / load / _apply_supersedes
  - resource: sr_agent/memory/hmac.py
    title: sign / verify (HMAC-SHA256, constant-time)
  - resource: sr_agent/models/memory.py
    title: SourceType / REQUIRES_HUMAN_CONFIRMATION
---

# Memory trust flow — the HMAC record lifecycle

> 🇷🇺 Русская версия: [memory-trust-flow.ru.md](memory-trust-flow.ru.md)

Every fact the agent remembers passes through `EpisodicMemory` (`sr_agent/memory/`). The
write path enforces policy before signing; the load path verifies before the model ever
sees a record. The store is **append-only** — corrections are new `supersede` records, and
only `human_input` may issue them.

```mermaid
flowchart TB
    subgraph WRITE["Write path — EpisodicMemory.write (orchestrator only)"]
        W0["MemoryRecord — source_type set by the KERNEL"]
        WP{"project_id == principal.project_id?"}
        WPX["raise PrincipalMismatch (cross-principal write)"]
        WS{"privileged status or supersedes?"}
        WSX{"source_type == human_input?"}
        WSXNO["raise MemoryWriteError — only a human may set/correct"]
        WL["link into the chain — kernel sets seq and chain_prev inside the signed fields"]
        WH["fields_for_hmac() then hmac.sign() — HMAC-SHA256 over canonical sort_keys form"]
        WA["append one JSONL line under memory/{project_id}/{target}"]
        WHD["update the signed chain head — memory/{project_id}/_chain_head.json"]
    end

    subgraph LOAD["Load path — EpisodicMemory.load (into model context)"]
        L0["open only memory/{project_id}/ — principal-scoped at the directory level"]
        LP["parse each line"]
        LPX["unparseable or unsigned then skip (debug log only)"]
        LV{"hmac.verify() — constant-time compare_digest"}
        LVX["silent drop — no WARNING (no tamper oracle)"]
        LC{"chain reconstructs? seq / chain_prev / signed head"}
        LCX["composition break then withhold the whole project (fail-closed) then WARNING to the operator log"]
        LS["_apply_supersedes() — project-wide, drop records a newer human correction overrides"]
        LOUT["surviving records then [DATA]-wrapped then model"]
    end

    AUDIT["verify_integrity() — OOB audit tool: REPORTS invalid counts for incident response"]

    W0 --> WP
    WP -->|no| WPX
    WP -->|yes| WS
    WS -->|yes| WSX
    WSX -->|no| WSXNO
    WSX -->|yes| WL
    WS -->|no| WL
    WL --> WH
    WH --> WA --> WHD
    WA -. jsonl on disk .-> L0
    L0 --> LP --> LV
    LP -->|bad| LPX
    LV -->|fail| LVX
    LV -->|ok| LC
    LC -->|no| LCX
    LC -->|yes| LS --> LOUT
    WA -. same files .-> AUDIT
```

## Reading this

- **The tier is set by the kernel, at write time.** `source_type` is a field the
  orchestrator fills (`human_input` > `tool_output` > `external_llm_output` /
  `human_relayed_tool` > `llm_inference`, per `TRUST_LEVELS`). No pack reaches this path, so
  model/relay output cannot claim a human tier.
- **The status gate is a hard write-time check.** A record that sets a status in
  `REQUIRES_HUMAN_CONFIRMATION` (e.g. `verified_safe`, `audit_complete`), or that carries a
  `supersedes`, is rejected unless `source_type == human_input` (`_enforce_status_rules`).
  This is what stops a model-tier record from flipping a verdict or overwriting a prior one.
- **Tamper fails closed and silently.** On load, a record whose HMAC does not verify is
  dropped with only a debug log — never WARNING+, so an attacker probing the store gets no
  signal about which forged record was rejected (no tamper oracle). `verify` uses
  `compare_digest`, so it is also constant-time against key-recovery.
- **Append-only, with `supersede` as the only correction.** There is no update or delete.
  `_apply_supersedes` removes any record whose `record_id` a later (human) record supersedes,
  so the model sees the corrected view without the history being mutable. Resolution runs
  over the principal's whole project, not per file — a human may file the correction under a
  different `target` than the record it overrides, and per-file resolution made such a
  correction silently ineffective.
- **Content integrity is not composition integrity.** A per-record HMAC proves a record was
  not *altered*. On its own it proves nothing about the record *set*: a record could be
  removed, and the removal was indistinguishable from a record that had never been written.
  Since a correction is itself just a record, that turned "delete the correction" into
  "restore the thing it corrected" — a rollback of the agent's beliefs available to anyone
  with write access to the file and no key at all. Each record therefore carries a signed
  `seq` and `chain_prev` (the previous record's signature), and each project carries a
  signed `_chain_head.json` attesting how far the chain reaches. Middle removals break the
  back-links; tail truncation — where the newest correction lives — is caught by the head.
- **A composition break withholds the project, and says so out of band.** The agent gets no
  memory rather than an attacker-chosen subset of it, and the operator gets a WARNING plus
  `chain_breaks` in `verify_integrity`. This signal is deliberately *not* the silent drop
  above: it never fires on a failed signature, only on validly-signed records disagreeing
  about what the file contains. Forged lines therefore stay non-events — an attacker probing
  with guessed signatures still learns nothing. The break itself is observable, and that is
  accepted: one bit of "noticed" is a good trade for denying the rollback.
- **`verify_integrity` is the one place invalid records are *reported*.** It backs the
  out-of-band `sr-agent memory verify` audit — a human running incident response needs the
  count, and there is no tamper-oracle concern outside the model's load path.

## Related

- [mi-threat-model.md](../mi-threat-model.md) — the attacks these checks neutralise.
- [turn-flow.md](turn-flow.md) — where `_persist_finding` calls the write path.
