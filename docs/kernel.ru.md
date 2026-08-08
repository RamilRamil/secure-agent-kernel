---
type: Reference
title: Ядро — task-agnostic, устойчивый к memory-injection защищённый агент
description: Переиспользуемое ядро защищённого агента — двухплоскостной раскол, MI-инварианты и граница CapabilityPack, которую пак не может перейти.
tags: [kernel, security, memory-injection, capability-pack, ru]
lang: ru
status: stable
generated:
  by: secure-agent-kernel/claude-opus-4.8
  at: 2026-08-08T00:00:00Z
sources:
  - resource: /kernel.md
    title: English original
---

# Ядро — task-agnostic, устойчивый к memory-injection защищённый агент

> 🇬🇧 English version: [kernel.md](kernel.md)

Ядро — это **переиспользуемая основа** проекта и его главный исследовательский вклад:
рантайм защищённого агента, чьи гарантии безопасности держатся **независимо от того,
что языковой модели велено делать** — в том числе вредоносным содержимым, подброшенным
в её собственную память. Оно ничего не знает про аудит смарт-контрактов (или любую
другую задачу). Аудит-агент — это лишь [первый capability-пак](https://github.com/RamilRamil/araratsec-agent),
который на нём едет.

## Суть

Большинство агентных фреймворков держат все возможности и все guardrail'ы в одном
месте, где хитро сформулированный промпт (или отравленная запись памяти) может
уговорить модель обойти guardrail. Ядро вместо этого разбивает систему на две
плоскости:

```
┌──────────── Плоскость оркестрации (детерминированный код) ─────────────────────┐
│  DATA-обёртка · иерархия доверия SourceType · HMAC append-only память ·        │
│  out-of-band gate подтверждения · бюджет tool-call'ов на ход · эскалация ·     │
│  path-containment + сетево-изолированный sandbox                                │
└──────────────────────────────────────┬────────────────────────────────────────┘
                                        │ узкий типизированный интерфейс
┌──────────────────────────────────────▼────────────────────────────────────────┐
│  Плоскость LLM-контекста (вероятностная модель)                                 │
│  локальная модель / relay / платный API · каждый внешний артефакт [DATA]…[DATA] │
└─────────────────────────────────────────────────────────────────────────────────┘
```

Вся безопасность живёт в плоскости оркестрации, в обычном коде. Модель может только
*предлагать*; решает — ядро. Атака prompt-injection или memory-injection способна
изменить то, что модель предлагает, но не то, что ядро разрешает. Полная карта модулей —
в [diagrams/kernel-architecture.ru.md](diagrams/kernel-architecture.ru.md).

Целевая угроза — **Memory Injection (MI)**: вредоносное содержимое, подброшенное в
память агента, которое направляет будущие сессии на эксфильтрацию, пропуск анализа или
ложный вердикт «безопасно». Незащищённые агенты показывают 55–85% успеха атаки;
архитектурные контроли ядра гонят это к ≤5% — по построению, а не по формулировке
промпта.

## Инварианты ядра (гарантии, которые пак не может ослабить)

1. **DATA-обёртка** — каждый вывод инструмента и каждый артефакт прошлого хода,
   возвращающийся в модель, оборачивается в `[DATA START]…[DATA END]` и трактуется как
   данные, никогда как инструкция (`orchestrator/context.py`).
2. **Иерархия доверия SourceType** — каждая запись памяти несёт тир происхождения, и
   порядок доверия — это константа ядра, а не рантайм-состояние
   (`models/memory.py`, `TRUST_LEVELS`):

   | тир | значение | кто/что |
   |------|-------|----------|
   | `human_input` | 4 | оператор, in-band |
   | `tool_output` | 3 | инструмент из whitelist |
   | `external_llm_output` / `human_relayed_tool` | 2 | вывод модели / релеенный текст инструмента |
   | `llm_inference` | 1 | собственные рассуждения модели |

   Вывод модели/relay **никогда** не повышается до `human_input`.
3. **HMAC append-only память** — каждая запись HMAC-подписана оркестратором
   (`memory/hmac.py`, `memory/episodic.py`); записи, не прошедшие проверку, молча
   отбрасываются до попадания в модель. Никаких update/delete — исправления это новые
   записи, которые `supersede`, и только `human_input` может их выпускать или ставить
   привилегированные статусы.
4. **Out-of-band gate подтверждения** — необратимое/привилегированное действие ставит
   run на паузу и требует осознанного одобрения через *отдельный* канал. Это
   **выводится ядром** из `action.action_class == write_execute`
   (`orchestrator/action.py::validate_action`), а не из флага, выставленного паком.
5. **Бюджет tool-call'ов на ход** — жёсткий лимит вызовов инструментов за ход.
6. **Механика эскалации** — путь рассуждения может эскалировать (local → relay/сильнее)
   при низкой уверенности или самозаявленной неопределённости; тир всегда виден
   (`guardrails/escalation.py`, `orchestrator/relay.py`).

Всё это обеспечивается кодом и покрыто тестами (включая property-тест на враждебный пак:
пак, пытающийся зарегистрировать `write_execute`-инструмент как не требующий
подтверждения или сочинить контент тира `human_input`, отклоняется/бессилен —
`tests/security/test_hostile_pack.py`, `tests/architecture/test_kernel_pack_boundary.py`).

## Модули ядра

```
sr_agent/
  orchestrator/   loop (OrchestratorLoop.run / run_turn), action (validate_action),
                  confirmation (OOB gate), context (DATA-обёртка), chat_session,
                  pack (интерфейс CapabilityPack), relay (ручной/файловый reasoning)
  guardrails/     sanitize, escalation (обобщённые триггеры)
  memory/         episodic (HMAC-хранилище), hmac, knowledge, lessons
  models/         memory (SourceType/MemoryRecord), action, principal, session, chat
  llm_core/       local_client, claude_client, gemini_client, openrouter_client,
                  router (резолвит routing[role]), chat_reasoning, schemas
  tools/          registry (механизм whitelist), sandbox (--network none Docker),
                  readonly
  config.py       конфиг из env      io/progress   eval/tracer (Langfuse)
```

Правило границы (проверяется архитектурным тестом): **ни один модуль ядра не импортирует
ни один downstream-модуль пака.** Паки зависят от ядра, никогда наоборот.

> **Замечание — здесь не поставляется composition root.** Ядро — только для импорта
> (`sr_agent`, без `[project.scripts]`, без `cli.py`). Его запускает composition root,
> живущий **downstream** — в репозитории [araratsec-agent](https://github.com/RamilRamil/araratsec-agent):
> CLI `sr-agent` (`audit_agent/cli.py`) и operator-фронтенд (`frontend/backend/app.py`).
> Оба собирают один и тот же `OrchestratorLoop(pack=…, …)`.

## Интерфейс CapabilityPack

Пак **декларативен и ограничен**. Подключается через один frozen-dataclass
(`sr_agent/orchestrator/pack.py`):

```python
CapabilityPack(
    name,                 # str
    actions,              # Mapping[str, ActionSpec]  — action_class, is_reversible, validate_params
    tools,                # Sequence[ToolDefinition]  — name, description, handler
    privileged_statuses,  # frozenset[str]            — доменные статусы, доступные только human_input
    reasoning_prompt,     # str
    dispatch / execute_confirmed / persist_finding,   # доменные callable'ы
    domain_escalation, signal_from,                   # доменные callable'ы
)
```

Callable'ы пака получают только узкий `PackContext` — **никогда loop, никогда handle на
запись памяти**. Пак может регистрировать инструменты и помечать действия
высокорисковыми, но у него нет рычага пропустить OOB-gate, подделать тир доверия или
тронуть HMAC-хранилище. Динамического реестра плагинов намеренно **нет** — единственный
пак подключён явно (YAGNI); ценность — сама граница.

## Честный охват — task-agnostic на уровне *гарантий*

**Гарантии** ядра действительно task-agnostic и MI-доказаны против внутрирепозиторного
[fixture-пака](../tests/fixtures/pack/), где нет никакого аудит-кода. Но часть
**имён и типов всё ещё несёт аудит-словарь** из до-раскольного монорепо (раскол
ядро ↔ пак, фича 048):

- `PackContext` выставляет `audit_root`, `poc_dir`, `poc_generator` (аудит-именованные
  поля) рядом с обобщёнными `sandbox` / `wrap_data`.
- `orchestrator/loop.py` всё ещё называет dataclass `AuditResult` и `_persist_finding`.
- `orchestrator/action.py::_validate_params(action, audit_root)` использует аудит-имя.

Ничего из этого не ослабляет инвариант — тесты границы проходят, и ни один пак не может
дотянуться дальше `PackContext`. Это остаточное именование: **task-agnostic там, где это
важно (механизмы безопасности), но пока не полностью на уровне типов/идентификаторов.**
Будущий проход может переименовать это в доменно-нейтральные термины, не трогая
поведение.

## Что нужно для запуска

Ядро не запускается само по себе — его ведёт **composition root**, спаривающий его с
паком. Требования:

- **Python ≥ 3.11**, зависимости из `pyproject.toml`.
- **`SR_SECRET_KEY`** (32-байтный hex) — ключ HMAC-подписи. Обязателен. Остальные корни
  (`SR_MEMORY_ROOT`, `SR_CONFIRMATIONS_ROOT`, `SR_RELAY_ROOT`) по умолчанию `./…`.
- **Reasoning-бэкенд** — локальная модель через Ollama (`local_client`), ручной файловый
  **relay** (`orchestrator/relay.py`) или (по желанию) хостед-API
  (`claude_client` / `gemini_client` / `openrouter_client`). Платный ключ для запуска не
  нужен; reasoning-бэкенд — рантайм-выбор, а не build-time.
- **Docker** — только если пак исполняет код под влиянием атакующего/модели, который ядро
  всегда гоняет `--network none` в эфемерном sandbox (`tools/sandbox.py`).

Пак, демонстрирующий всё это, — в downstream-репозитории
[araratsec-agent](https://github.com/RamilRamil/araratsec-agent), а историю изменений
этого бандла см. в [log.md](log.md).
