---
type: Reference
title: Индекс диаграмм ядра
description: Индекс диаграмм архитектуры и потоков secure-agent-kernel; двуязычная конвенция EN/RU.
tags: [index, diagrams, kernel, ru]
lang: ru
status: stable
generated:
  by: secure-agent-kernel/claude-opus-4.8
  at: 2026-08-08T00:00:00Z
sources:
  - resource: /diagrams/README.md
    title: English original
---

# Диаграммы

Диаграммы архитектуры и потоков **secure-agent-kernel**, отражающие **то, что реально
подключено сегодня**. Исходники на Mermaid, рендерятся в GitHub/VS Code/большинстве
markdown-вьюеров.

**Двуязычные доки.** У каждого дока есть английский базовый файл (`name.md`) и русский
сосед (`name.ru.md`); они перекрёстно ссылаются вверху. Держи пару в синхроне при
изменении проводки.

- [kernel-architecture.ru.md](kernel-architecture.ru.md) · [🇬🇧](kernel-architecture.md) —
  **двухплоскостной раскол**: детерминированная плоскость оркестрации (где живёт каждая
  гарантия), вероятностная плоскость LLM-контекста, карта модулей `sr_agent` и граница
  `CapabilityPack`. Помечает downstream composition root / пак как внешние и отмечает
  остаточное аудит-именование.
- [turn-flow.ru.md](turn-flow.ru.md) · [🇬🇧](turn-flow.md) — один `OrchestratorLoop.run_turn`
  пошагово: DATA-обёртка → модель предлагает → `validate_action` → OOB-gate на
  `write_execute` → ограниченный read-only dispatch.
- [memory-trust-flow.ru.md](memory-trust-flow.ru.md) · [🇬🇧](memory-trust-flow.md) —
  жизненный цикл записи памяти: изоляция принципала → status-gate → HMAC-подпись →
  append-only → загрузка → проверка / молчаливый сброс при провале → `supersede` (только
  human_input).

Обновляй их при изменении проводки — диаграмма, которая врёт о том, что подключено, хуже,
чем отсутствие диаграммы.
