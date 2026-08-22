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
    actions,              # Mapping[str, ActionSpec] — ДОМЕННЫЕ id действий пака
    tools,                # Sequence[ToolDefinition]
    privileged_statuses,  # frozenset[str] — объявляемые паком статусы, доступные только human_input
    reasoning_prompt,     # str
    dispatch,             # (Action, PackContext) -> str            (read-only / одобренный путь)
    execute_confirmed,    # (Action, PackContext) -> (str, event?)  (пост-OOB путь)
    persist_finding,      # (payload, PackContext) -> artifact?      (пишет потом ядро)
    domain_escalation,    # доп. триггеры эскалации
    signal_from,          # доменный сигнал из действия модели
)
```

- **Открытая таксономия.** `Action.action_type` — свободная строка. Пак владеет
  **доменными** id действий (`pack.actions`) *и* своим **набором привилегированных
  статусов**; ядро держит только обобщённые, недоменные id, которые оно предоставляет
  каждому паку — control/memory-машинерию (`write_memory`, `request_human_confirmation`)
  **и** scope-ограниченные чтения (`read_file`, `search_code`, решение D6). `validate_action`
  резолвит id против `KERNEL_GENERIC_ACTIONS ∪ pack.actions` и **fail-closed** при промахе.
  `write_memory` — один из этих обобщённых id, но он **вообще не диспатчится паку**:
  `KernelActionExecutor` перехватывает его до `pack.dispatch` и сам строит запись по
  фиксированной форме (`payload={"note": …}`, `payload_kind="model_note"`,
  `source_type=llm_inference`, задано ядром). Пак ничего для него не объявляет и не
  должен реализовывать заглушку — для пака здесь нет ветки `dispatch` (фича 002).
- **`ActionSpec`** = `(action_class, is_reversible, validate_params)`. `action_class` —
  **единственный** рычаг подтверждения у пака; правило «`write_execute ⇒ confirm`» выводит
  само ядро. Отсутствующий или пермиссивный `validate_params` **fail closed**: whitelist
  ядра, path-containment и sandbox всё равно применяются.
- **`PackContext`** — узкая least-privilege-поверхность, отдаваемая каждому callable пака:
  `scope_root`, `sandbox`, `wrap_data`. Это **никогда loop и никогда handle на запись
  памяти**, и в нём **нет PoC-состояния** (`poc_dir`/`poc_generator` — на стороне пака,
  решение D2). Пак возвращает доменные артефакты (`persist_finding` возвращает находку;
  `execute_confirmed` — событие статуса); каждую запись в память делает *ядро* и само
  выставляет тир источника. (Переходный `__getattr__`-шим ещё отвечает на `ctx.audit_root`
  → `scope_root` с `DeprecationWarning`, пока аудит-пак не мигрирует; снимается в PR-3.)

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
  `human_input`. То же верно для `write_memory`: он никогда не доходит до пака, и ядро
  всегда пишет заметку как `llm_inference` — параметры модели не могут задать
  `source_type` или любое другое поле идентичности (отклоняется, а не вырезается).
- **H3 — не может выйти из containment.** Даже с пермиссивным `validate_params`
  принадлежащий ядру `read_file` отказывает путям вне `scope_root`, а `DockerSandbox.run`
  по умолчанию `--network none` — пак получает лишь *handle* sandbox, не его политику.
- **H3b — не может выбрать, куда ляжет его собственный коммит, без границ.**
  Параметр `target` у экшена пака становится именем файла памяти, поэтому ядро
  ограничивает его (`validate_commit_target`: 200 байт, без управляющих
  символов, только строка) **до** вызова `pack.dispatch`. Таргет вне границ —
  это чистый `DispatchStatus.error`, и пак вообще не входит: отказ не имеет
  права наступать после эффекта, потому что эффект, который ядро не смогло
  закоммитить, продиспатчится повторно. `target` — метка раздела, а не путь:
  разделители обезврежены, контейнмент даёт `project_id` из принципала, поэтому
  таргет вида traversal принимается и содержится, а не отклоняется.
- **H4 — недообъявление не обходит status-гейт.** Пак с *пустым* или *суженным*
  `privileged_statuses` гейтит ровно свой объявленный набор (пустой ⇒ «ничего не
  привилегировано», а не «гейт выключен») — он не может обойти статус, энфорсимый ядром,
  потому что после открытия таксономии ядро не энфорсит ни одного своего.

### Честный остаток

Пак **может** промаркировать запись как `read_only` (`action_class` — действительно его
рычаг). Этот остаток намеренно закреплён `test_H1_class_mislabel_is_bounded_not_open`: даже
промаркированный неверно, `read_file`/`search_code` обеспечивают `scope_root`, а исполнение
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
| **B6** | открытая таксономия не понизила ни одного guardrail: `action_type` — не закрытый доменный enum; ни один доменный id действия или привилегированный статус не операционен в `sr_agent/` (на AST); `KERNEL_GENERIC_ACTIONS` и `LOOP_TERMINALS` — ровно свои task-agnostic-наборы и не пересекаются; и в `PackContext` нет реального поля `poc_*` / `audit_root` |

B6 (фича 001) — анти-регрессионный латч открытой таксономии: верни в ядро доменный id или
поле `poc_*` — и он краснеет. Он вытесняет старую B3-толерантность к остаточному именованию:
`poc_dir`/`poc_generator` и `audit_root`/`AuditResult` теперь ушли из ядра начисто
(остаётся лишь переходный `audit_root`-шим, снимаемый в PR-3), так что на уровне
действий/контекста терпимого остаточного именования больше нет — см. [kernel.ru.md](kernel.ru.md).

## Доказано без задачного кода

`tests/fixtures/pack/FIXTURE_PACK` — минимальный, **только-ядерный** пак (он импортирует
ноль аудит-модулей — guards границы его сканируют). Он воспроизводит те же классы
MI-поверхности, что и настоящий аудит-пак — read-only инструмент, чей вывод DATA-обёрнут,
действие `write_execute`, достижимое только после OOB-подтверждения, и путь записи памяти,
где тир выставляет ядро, — так что MI-гарантии продемонстрированы в этом репо без задачи.
См. [mi-eval.ru.md](mi-eval.ru.md).

Динамического реестра плагинов намеренно **нет** (YAGNI). Единственный пак подключён явно;
узкая, «не дающая лишнего» граница *и есть* результат.
