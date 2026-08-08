---
type: Reference
title: Интерфейс CapabilityPack
description: Декларативный контракт, в который подключается задаче-специфичный пак — и проверяемое тестами свойство, что пак не может понизить guardrail ядра.
tags: [capability-pack, boundary, kernel, security, ru]
lang: ru
status: stable
generated:
  by: secure-agent-kernel/claude-opus-4.8
  at: 2026-08-08T00:00:00Z
sources:
  - resource: /capability-pack-interface.md
    title: English original
---

# Интерфейс CapabilityPack

> 🇬🇧 English version: [capability-pack-interface.md](capability-pack-interface.md)

Задача едет на ядре как **`CapabilityPack`** — единственный frozen-dataclass из данных
плюс callable'ов. Ни базового класса, ни реестра, ни discovery (research R1): сегодня
существует ровно один пак, и его явно подключает composition root. Вся ценность
безопасности — в том, что интерфейс **не даёт**: пак приносит возможности, но у него нет ни
поля, ни handle, которые могли бы ослабить гарантию.

## Контракт

```python
CapabilityPack(
    name,                 # str
    actions,              # Mapping[str, ActionSpec]
    tools,                # Sequence[ToolDefinition]
    privileged_statuses,  # frozenset[str] — доменные статусы, доступные только human_input
    reasoning_prompt,     # str
    dispatch,             # (Action, PackContext) -> str            (read-only / одобренный путь)
    execute_confirmed,    # (Action, PackContext) -> (str, event?)  (пост-OOB путь)
    persist_finding,      # (payload, PackContext) -> artifact?      (пишет потом ядро)
    domain_escalation,    # доп. триггеры эскалации
    signal_from,          # доменный сигнал из действия модели
)
```

- **`ActionSpec`** = `(action_class, is_reversible, validate_params)`. `action_class` —
  **единственный** рычаг подтверждения у пака; правило «`write_execute ⇒ confirm`» выводит
  само ядро. Отсутствующий или пермиссивный `validate_params` **fail closed**: whitelist
  ядра, path-containment и sandbox всё равно применяются.
- **`PackContext`** — узкая least-privilege-поверхность, отдаваемая каждому callable пака:
  `audit_root`, `sandbox`, `poc_dir`, `wrap_data` и опциональный `poc_generator`. Это
  **никогда loop и никогда handle на запись памяти.** Пак возвращает доменные артефакты
  (`persist_finding` возвращает находку; `execute_confirmed` — событие статуса); каждую
  запись в память делает *ядро* и само выставляет тир источника.

## Свойство: пак не может понизить guardrail

Это свойство Принципа III из конституции, и оно **проверено тестом, а не заявлено**
(`tests/security/test_hostile_pack.py`). Враждебный пак — это просто плохое значение,
собранное инлайн, без наследования:

- **H1 — не может пропустить подтверждение.** Пак, объявивший действие `write_execute`, не
  может заодно объявить его skip-confirmation: `validate_action` ставит
  `human_confirmation = False` (pending) из класса, а структурный тест утверждает, что на
  `ActionSpec` и `CapabilityPack` **нет** поля `requires_confirmation` /
  `skip_confirmation` / `human_confirmation`.
- **H2 — не может подделать тир `human_input`.** У `PackContext` нет поля `memory` (тест
  фиксирует его набор полей точно), так что пак вообще не может писать память; находки,
  сообщённые моделью, ядро сохраняет как `external_llm_output`, никогда не повышая до
  `human_input`.
- **H3 — не может выйти из containment.** Даже с пермиссивным `validate_params`
  принадлежащий ядру `read_file` отказывает путям вне `audit_root`, а `DockerSandbox.run`
  по умолчанию `--network none` — пак получает лишь *handle* sandbox, не его политику.

### Честный остаток

Пак **может** промаркировать запись как `read_only` (`action_class` — действительно его
рычаг). Этот остаток намеренно закреплён `test_H1_class_mislabel_is_bounded_not_open`: даже
промаркированный неверно, `read_file`/`search_code` обеспечивают `audit_root`, а исполнение
инструментов идёт в сетево-изолированном sandbox. Дверь **ограничена безусловным
containment, а не закрыта** — и тест существует, чтобы любое будущее изменение, её
расширяющее, было замечено.

## Граница проверяется машинно

`tests/architecture/test_kernel_pack_boundary.py` обеспечивает направление зависимости и
шов после раскола (фича 048):

| проверка | гарантия |
|-------|-----------|
| **B1** | ни один модуль ядра не импортирует `sr_agent.packs` (на AST, так что строка в комментарии не считается) |
| **B2** | ни один модуль ядра не читает audit-владеемое config-поле (`alchemy_api_key`, `tenderly_api_key`, `workspaces_root`, `git_token`, `smartgraphical_root`) |
| **B3** | ни один routing-модуль ядра не несёт stage/PoC-слот-идентификатор или дефолт `sr-stage2` — роутинг shape-agnostic |
| **B4** | роутер ядра резолвит произвольный `Mapping[str, str]`; отсутствующая роль поднимает `KeyError`, никогда молчаливый дефолт |
| **B5** | ни один тест ядра не импортирует audit-only тулинг (`scripts.*` / `frontend.*`) |

Обрати внимание, что B3 **не** флагует: `poc_dir` / `poc_generator` в `loop.py` — это
легитимные пути исполнения PoC, а не слоты моделей, поэтому дисциплина границы осознанно
оставляет эти audit-именованные *имена* на месте. Это и есть точная граница между
«task-agnostic там, где важно» (импорты, конфиг, роутинг — все проверенно чисты) и
терпимым остаточным именованием, описанным в [kernel.ru.md](kernel.ru.md).

## Доказано без задачного кода

`tests/fixtures/pack/FIXTURE_PACK` — минимальный, **только-ядерный** пак (он импортирует
ноль аудит-модулей — guards границы его сканируют). Он воспроизводит те же классы
MI-поверхности, что и настоящий аудит-пак — read-only инструмент, чей вывод DATA-обёрнут,
действие `write_execute`, достижимое только после OOB-подтверждения, и путь записи памяти,
где тир выставляет ядро, — так что MI-гарантии продемонстрированы в этом репо без задачи.
См. [mi-eval.ru.md](mi-eval.ru.md).

Динамического реестра плагинов намеренно **нет** (YAGNI). Единственный пак подключён явно;
узкая, «не дающая лишнего» граница *и есть* результат.
