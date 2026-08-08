---
type: Diagram
title: Архитектура ядра — двухплоскостной раскол
description: Карта модулей sr_agent — детерминированная плоскость оркестрации, вероятностная плоскость LLM-контекста и граница CapabilityPack.
tags: [architecture, kernel, two-plane, diagram, ru]
lang: ru
status: stable
generated:
  by: secure-agent-kernel/claude-opus-4.8
  at: 2026-08-08T00:00:00Z
sources:
  - resource: /diagrams/kernel-architecture.md
    title: English original
---

# Архитектура ядра — двухплоскостной раскол

> 🇬🇧 English version: [kernel-architecture.md](kernel-architecture.md)

Что реально подключено в `sr_agent` сегодня (ядро версии 0.1.1). Вся безопасность живёт
в **плоскости оркестрации** (детерминированный код); модель живёт в **плоскости
LLM-контекста** и может только *предлагать*. Задаче-специфичный `CapabilityPack` и
composition root, запускающий loop, живут **downstream** (в репозитории
[araratsec-agent](https://github.com/RamilRamil/araratsec-agent)) — ядро не импортирует
ни строчки кода паков.

```mermaid
flowchart TB
    subgraph ROOTS["Composition root — DOWNSTREAM (не в этом репо)"]
        DR["araratsec-agent<br/>audit_agent/cli.py · frontend/backend/app.py<br/>собирают OrchestratorLoop(pack=…)"]
    end

    subgraph ORCH["Плоскость оркестрации — детерминированный код (ядро)"]
        LOOP["orchestrator/loop.py<br/>OrchestratorLoop.run · run_turn<br/>бюджет tool-call'ов на ход"]
        ACT["orchestrator/action.py<br/>validate_action — 'write_execute ⇒ confirm'"]
        CONF["orchestrator/confirmation.py<br/>out-of-band gate одобрения"]
        CTX["orchestrator/context.py<br/>DATA-обёртка каждого артефакта [DATA]…[DATA]"]
        PACKIF["orchestrator/pack.py<br/>CapabilityPack · узкий PackContext"]
        RELAY["orchestrator/relay.py<br/>ручной/файловый reasoning-relay"]
        GUARD["guardrails/{sanitize,escalation}"]
        MEM["memory/{episodic,hmac,knowledge,lessons}<br/>HMAC append-only · тиры SourceType · supersede"]
        TOOLS["tools/{registry,sandbox,readonly}<br/>whitelist · --network none эфемерный"]
        MODELS["models/{memory,action,principal,session,chat}"]
        CFG["config.py · io/progress · eval/tracer"]
    end

    subgraph LLM["Плоскость LLM-контекста — вероятностная модель (только предлагает)"]
        CORE["llm_core/{local_client,claude_client,gemini_client,<br/>openrouter_client,router,chat_reasoning,schemas}<br/>router резолвит routing[role]"]
    end

    subgraph PACK["CapabilityPack — задаче-специфичный, инъектируется (DOWNSTREAM)"]
        P["напр. audit_agent AUDIT_PACK<br/>actions · tools · reasoning_prompt"]
    end

    DR --> LOOP
    LOOP --> ACT --> CONF
    LOOP --> CTX
    LOOP --> GUARD
    LOOP --> MEM
    LOOP --> TOOLS
    LOOP -->|"routing[role]"| CORE
    LOOP -->|"узкий PackContext"| PACKIF
    P -->|"инъектируется в"| PACKIF
    P -.->|"модель предлагает действия"| ACT
    CORE -.->|"вывод возвращается обёрнутым [DATA]…[DATA]"| CTX
    TOOLS -->|"вывод инструмента → тир tool_output"| MEM
```

## Как это читать

- **Две плоскости, одно направление власти.** Всё в плоскости оркестрации — обычный
  детерминированный код. Плоскость LLM-контекста может *предложить* действие или выдать
  текст; она никогда не решает. Атака prompt- или memory-injection меняет предложение, а
  не разрешение — инварианты, обеспечиваемые здесь, см. в [kernel.ru.md](../kernel.ru.md).
- **OOB-gate выводится ядром.** `validate_action` требует out-of-band одобрения всякий
  раз, когда `action.action_class == write_execute`. Пак задаёт `action_class` через свой
  `ActionSpec`, но у него **нет поля**, чтобы пометить такое действие как
  skip-confirmation, — понизить gate он не может.
- **Граница пака реальна и покрыта тестом.** Ни один модуль ядра не импортирует модули
  паков (`tests/architecture/test_kernel_pack_boundary.py`). Пак дотягивается до ядра
  только через единственный `CapabilityPack`, который собирает, а ядро отдаёт callable'ам
  пака только узкий `PackContext` — никогда loop, никогда handle на запись памяти. Именно
  поэтому пак структурно не может подделать запись тира `human_input`.
- **Здесь нет composition root.** Ядро не поставляет `cli.py` и `[project.scripts]`; оно
  только для импорта. Блок `ROOTS` — downstream в araratsec-agent. Поэтому диаграмма
  показывает ядро таким, каким его *потребляют*, с root/паком, нарисованными как внешние.
- **Task-agnostic там, где это важно, — но не в каждом имени.** Механизмы задаче-нейтральны
  и MI-доказаны против внутрирепозиторного fixture-пака, но `PackContext` всё ещё называет
  `audit_root`/`poc_dir`/`poc_generator`, а `loop.py` — `AuditResult` (остаток до-раскола,
  фича 048). Ни один инвариант от этих имён не зависит; см. раздел «Честный охват» в
  [kernel.ru.md](../kernel.ru.md).

## Связанное

- [kernel.ru.md](../kernel.ru.md) — инварианты и контракт CapabilityPack прозой.
- [turn-flow.ru.md](turn-flow.ru.md) — один `OrchestratorLoop.run_turn` пошагово.
- [memory-trust-flow.ru.md](memory-trust-flow.ru.md) — жизненный цикл HMAC-записи.
