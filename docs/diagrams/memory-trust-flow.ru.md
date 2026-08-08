---
type: Diagram
title: Поток доверия памяти — жизненный цикл HMAC-записи
description: Как запись памяти пишется и загружается — изоляция принципала, status-gate, HMAC-подпись, молчаливый сброс при подмене и цепочка supersede.
tags: [flow, memory, hmac, source-type, diagram, ru]
lang: ru
status: stable
generated:
  by: secure-agent-kernel/claude-opus-4.8
  at: 2026-08-08T00:00:00Z
sources:
  - resource: /diagrams/memory-trust-flow.md
    title: English original
---

# Поток доверия памяти — жизненный цикл HMAC-записи

> 🇬🇧 English version: [memory-trust-flow.md](memory-trust-flow.md)

Каждый факт, который агент запоминает, проходит через `EpisodicMemory` (`sr_agent/memory/`).
Путь записи обеспечивает политику до подписи; путь загрузки проверяет до того, как модель
вообще увидит запись. Хранилище **append-only** — исправления это новые `supersede`-записи,
и только `human_input` может их выпускать.

```mermaid
flowchart TB
    subgraph WRITE["Путь записи — EpisodicMemory.write (только оркестратор)"]
        W0["MemoryRecord — source_type выставляет ЯДРО"]
        WP{"project_id == principal.project_id?"}
        WPX["raise PrincipalMismatch (кросс-принципальная запись)"]
        WS{"привилегированный статус или supersedes?"}
        WSX{"source_type == human_input?"}
        WSXNO["raise MemoryWriteError — только человек может ставить/исправлять"]
        WH["fields_for_hmac(), затем hmac.sign() — HMAC-SHA256 над каноничной sort_keys-формой"]
        WA["добавить одну JSONL-строку под memory/{project_id}/{target}"]
    end

    subgraph LOAD["Путь загрузки — EpisodicMemory.load (в контекст модели)"]
        L0["открыть только memory/{project_id}/ — scoping принципала на уровне каталога"]
        LP["распарсить каждую строку"]
        LPX["непарсируемая или неподписанная, затем skip (только debug-лог)"]
        LV{"hmac.verify() — constant-time compare_digest"}
        LVX["молчаливый сброс — без WARNING (нет tamper-оракула)"]
        LS["_apply_supersedes() — отбросить записи, что переопределяет новая человеческая правка"]
        LOUT["выжившие записи, затем [DATA]-обёртка, затем модель"]
    end

    AUDIT["verify_integrity() — OOB-инструмент аудита: РЕПОРТИТ счётчик невалидных для incident response"]

    W0 --> WP
    WP -->|нет| WPX
    WP -->|да| WS
    WS -->|да| WSX
    WS -->|нет| WH
    WSX -->|нет| WSXNO
    WSX -->|да| WH
    WH --> WA
    WA -. jsonl на диске .-> L0
    L0 --> LP --> LV
    LP -->|плохая| LPX
    LV -->|провал| LVX
    LV -->|ok| LS --> LOUT
    WA -. те же файлы .-> AUDIT
```

## Как это читать

- **Тир выставляет ядро, на записи.** `source_type` — поле, которое заполняет оркестратор
  (`human_input` > `tool_output` > `external_llm_output` / `human_relayed_tool` >
  `llm_inference`, по `TRUST_LEVELS`). Ни один пак не дотягивается до этого пути, так что
  вывод модели/relay не может претендовать на человеческий тир.
- **Status-gate — жёсткая проверка на записи.** Запись, ставящая статус из
  `REQUIRES_HUMAN_CONFIRMATION` (напр. `verified_safe`, `audit_complete`), или несущая
  `supersedes`, отклоняется, если `source_type != human_input` (`_enforce_status_rules`).
  Именно это не даёт записи модельного тира перевернуть вердикт или переписать предыдущую.
- **Подмена fail closed и молча.** При загрузке запись, чей HMAC не проходит, отбрасывается
  лишь с debug-логом — никогда WARNING+, так что атакующий, щупающий хранилище, не получает
  сигнала о том, какая поддельная запись отклонена (нет tamper-оракула). `verify`
  использует `compare_digest`, поэтому это ещё и constant-time против восстановления ключа.
- **Append-only, с `supersede` как единственной правкой.** Нет update или delete.
  `_apply_supersedes` убирает любую запись, чей `record_id` переопределяет более поздняя
  (человеческая) запись, так что модель видит исправленный вид без мутабельности истории.
- **`verify_integrity` — единственное место, где невалидные записи *репортятся*.** Он стоит
  за out-of-band аудитом `sr-agent memory verify` — человеку в incident response нужен
  счётчик, и tamper-оракула вне пути загрузки модели тут нет.

## Связанное

- [mi-threat-model.ru.md](../mi-threat-model.ru.md) — атаки, которые нейтрализуют эти проверки.
- [turn-flow.ru.md](turn-flow.ru.md) — где `_persist_finding` вызывает путь записи.
