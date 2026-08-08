---
type: Reference
title: Документация secure-agent-kernel
description: OKF-бандл знаний для task-agnostic, устойчивого к memory-injection ядра агента (sr_agent).
okf_version: "0.2"
tags: [index, documentation, kernel, ru]
lang: ru
status: stable
generated:
  by: secure-agent-kernel/claude-opus-4.8
  at: 2026-08-08T00:00:00Z
sources:
  - resource: /index.md
    title: English original
---

# Документация secure-agent-kernel

> 🇬🇧 English version: [index.md](index.md)

Этот каталог — бандл [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf)
(OKF v0.2): каждый концепт — Markdown-файл с YAML-frontmatter, версионируется в git рядом
с кодом, который описывает. История изменений — в [log.md](log.md).

**Двуязычная конвенция.** У каждого концепта есть английский базовый файл (`name.md`) и
русский сосед (`name.ru.md`), перекрёстно связанные вверху и несущие `lang: en` / `lang: ru`.
Держи пару в синхроне при изменении проводки.

**Происхождение.** `generated.by` следует OKF-конвенции акторов: `human:<id>` для
человеческого контента, `<producer>/<model>` для контента, сгенерированного агентом.
Сгенерированные агентом доки ещё не прошли человеческую `verified`-проверку; добавь запись
`verified` после ревью.

## Концепты

- [kernel.ru.md](kernel.ru.md) · [🇬🇧](kernel.md) — переиспользуемое ядро защищённого
  агента: двухплоскостной раскол, MI-инварианты (DATA-обёртка, иерархия доверия
  `SourceType`, HMAC append-only память, выводимый ядром OOB-gate), граница
  `CapabilityPack` и честная заметка про остаточное аудит-именование.
- [mi-threat-model.ru.md](mi-threat-model.ru.md) · [🇬🇧](mi-threat-model.md) — Memory
  Injection в глубину: пять векторов атаки (MI-001..005), что нейтрализует каждый, и честная
  форма заявки ≤5% ASR.
- [capability-pack-interface.ru.md](capability-pack-interface.ru.md) · [🇬🇧](capability-pack-interface.md) —
  декларативный контракт пака и проверяемое свойство, что пак не может понизить guardrail
  (враждебный пак H1/H2/H3, граница B1..B5).
- [mi-eval.ru.md](mi-eval.ru.md) · [🇬🇧](mi-eval.md) — ось Secure: как считается ASR, набор
  тестов безопасности и границы, и что числа заявляют, а что нет.

## Диаграммы

- [diagrams/kernel-architecture.ru.md](diagrams/kernel-architecture.ru.md) · [🇬🇧](diagrams/kernel-architecture.md) —
  двухплоскостной раскол и карта модулей `sr_agent`, с downstream composition root / паком,
  нарисованными как внешние.
- [diagrams/turn-flow.ru.md](diagrams/turn-flow.ru.md) · [🇬🇧](diagrams/turn-flow.md) — один
  `OrchestratorLoop.run_turn`: DATA-обёртка → предложение → `validate_action` → OOB-gate → ограниченный dispatch.
- [diagrams/memory-trust-flow.ru.md](diagrams/memory-trust-flow.ru.md) · [🇬🇧](diagrams/memory-trust-flow.md) —
  жизненный цикл HMAC-записи: изоляция принципала → status-gate → подпись → verify/drop → supersede.
- [diagrams/README.ru.md](diagrams/README.ru.md) · [🇬🇧](diagrams/README.md) — под-индекс
  диаграмм.

## Связанное (вне этого бандла)

- `../README.md` — собственный обзор ядра и «чего здесь НЕТ».
- Downstream-репозиторий [araratsec-agent](https://github.com/RamilRamil/araratsec-agent) —
  первый `CapabilityPack`, который едет на этом ядре, со своим OKF-бандлом `docs/`.
