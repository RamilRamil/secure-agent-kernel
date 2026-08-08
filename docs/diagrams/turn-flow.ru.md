---
type: Diagram
title: Поток хода — один OrchestratorLoop.run_turn
description: Пошагово один чат-ход — DATA-обёртка → модель предлагает → validate_action → OOB-gate на write_execute → ограниченный read-only dispatch.
tags: [flow, orchestrator, turn, oob-gate, diagram, ru]
lang: ru
status: stable
generated:
  by: secure-agent-kernel/claude-opus-4.8
  at: 2026-08-08T00:00:00Z
sources:
  - resource: /diagrams/turn-flow.md
    title: English original
---

# Поток хода — один `OrchestratorLoop.run_turn`

> 🇬🇧 English version: [turn-flow.md](turn-flow.md)

Один чат-ход (`sr_agent/orchestrator/loop.py::run_turn`). Local-first рассуждение, read-only
вызовы инструментов, ограниченные бюджетом на ход, и жёсткая пауза на любом действии
`write_execute`. Собственное сообщение пользователя входит в контекст модели **как
`[DATA]`** — его формулировка не несёт власти (FR-004).

```mermaid
flowchart TB
    START["run_turn(user_message, system_prompt)"]
    WRAP["wrap_data(user_message) — ввод пользователя это [DATA], не власть"]
    BUILD["build_messages() — каждый артефакт возвращается обёрнутым [DATA]"]
    COMPLETE["reasoning.complete(messages) — local-first; может эскалировать"]
    OUTCOME{"outcome.kind"}
    BLOCKED["return blocked_local_unavailable"]
    RELAY["return paused_relay"]
    FIND{"agent_action.finding?"}
    PERSIST["_persist_finding() — ЯДРО пишет source_type=external_llm_output (никогда human_input)"]
    NEXT{"next_action"}
    DONE["return completed (answer = reasoning_summary)"]
    VALIDATE["validate_action(action, audit_root, pack)"]
    REJECT["вернуть 'ACTION REJECTED' обратно как [DATA]; tool_calls++"]
    GATE{"human_confirmation is False? (write_execute)"}
    OOB["request_confirmation(), затем ПАУЗА — return paused_confirmation (возобновление только out-of-band)"]
    DISPATCH["pack.dispatch(action, ctx) — read-only / одобрено"]
    RESULT["результат возвращается как [DATA]; tool_calls++"]
    BUDGET{"ещё в пределах бюджета на ход?"}
    EXHAUST["return budget_exhausted"]

    START --> WRAP --> BUILD --> COMPLETE --> OUTCOME
    OUTCOME -->|blocked_local_unavailable| BLOCKED
    OUTCOME -->|paused_relay| RELAY
    OUTCOME -->|action| FIND
    FIND -->|да| PERSIST --> NEXT
    FIND -->|нет| NEXT
    NEXT -->|"complete / escalate"| DONE
    NEXT -->|unknown| REJECT
    NEXT -->|tool action| VALIDATE
    VALIDATE -->|rejected| REJECT
    VALIDATE -->|approved| GATE
    GATE -->|да| OOB
    GATE -->|нет| DISPATCH --> RESULT --> BUDGET
    REJECT --> BUDGET
    BUDGET -->|да| BUILD
    BUDGET -->|нет| EXHAUST
```

## Как это читать

- **Всё, чего касается модель, — это `[DATA]`.** Сообщение пользователя, каждый вывод
  инструмента и session-facts-grounding проходят через `wrap_data`
  (`orchestrator/context.py`) до входа в контекст. Модель читает их как данные; она никогда
  не наследует их власть.
- **Модель предлагает; ядро распоряжается.** `reasoning.complete` возвращает предложенное
  действие. Ничего не происходит, пока `validate_action` его не одобрит — а отклонённое или
  неизвестное действие это не крах, а возврат как `[DATA]`, чтобы модель поправилась в том
  же ходу.
- **`write_execute` всегда ставит на паузу.** Когда `validate_action` оставляет
  `human_confirmation = False` (выведено ядром из `action_class`), ход подаёт out-of-band
  подтверждение и **возвращается** — он не блокирует опросом здесь. Исполнение — позже,
  только через `execute_confirmed`, и только после того, как человек одобрит через отдельный
  процесс (см. [kernel.ru.md](../kernel.ru.md), `orchestrator/confirmation.py`). Обхода gate
  нет.
- **Находка пишется тиром ядра.** Если модель сообщает находку, её сохраняет ядро — не пак —
  как `external_llm_output`; пак лишь возвращает доменный объект (см.
  [capability-pack-interface.ru.md](../capability-pack-interface.ru.md)).
- **Ход ограничен.** Read-only-dispatch'и крутятся, пока не достигнут бюджет вызовов на ход
  (`MAX_TOOL_CALLS_PER_TURN`), затем ход останавливается с `budget_exhausted`. Сессия
  охватывает много ходов; один ход не может крутиться бесконечно.

## Связанное

- [kernel-architecture.ru.md](kernel-architecture.ru.md) — где сидят эти модули.
- [memory-trust-flow.ru.md](memory-trust-flow.ru.md) — что делают `_persist_finding` и путь загрузки.
