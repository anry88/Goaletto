# Следующая волна внешней разработки · 2026-10-12

План после review и интеграции первых четырёх заданий. Задания ниже готовы для
копирования исполнителям; автоматически никому не отправлены. Общая база веток:
финальный commit ветки `codex/integrate-agent-wave1` (хеш в отчёте этой сборки).

## Что принято из первой волны

| Задание | Результат после доработки Codex | Что ещё осталось |
|---|---|---|
| Sender AND-68 | Отдельный worker loop, fresh preflight, durable attempts/board lease/revision, bounded retry и transport, edit одного табло | Реальный Telegram smoke, распределённый token limiter при масштабировании |
| Карточки, часть AND-48 | Клуб/состав/игрок, durable file_id; routers подключены, сеть после commit | Staff UI и lifecycle уведомления, полный recovery/analytics AND-76 |
| Состав AND-49 | XI/bench/formation/captain/tactic; real update_id, ownership scope/revision, повторный дедлайн под locks; runtime snapshot | Live-команды относятся к AND-53 |
| История AND-66 | Списки/архив/report/player history; halftime events, frozen names, чужой игрок исключён | Bounded SQL pagination и MVP с согласованной формулой |

Codex подключил `/start → offers → preview → claim → club` и команды
`/club /squad /lineup /history /table /help`. Включение игры отдельно:
`GOALETTO_GAME_ENABLED=true`; sender отдельно: `GOALETTO_OUTBOX_ENABLED=true`.
По умолчанию оба выключены. Полная готовность родительских задач не следует из
готовности внешнего подпакета: staff, rollover, экономика и live admission ещё в плане.

## Общие условия для каждого исполнителя

Работай в отдельной ветке от зафиксированного commit. Прочитай AGENTS.md, README,
docs/game-rules.md, корневой GDD v2 и документы своей подсистемы. Только Python и
Telegram: без Mini App, JavaScript и публичного API. Domain чистый. Durable state,
receipts, jobs и outbox в PostgreSQL. Analytics opt-out не отменяет бизнес-дедуп.

До реализации перечисли интерфейсы и общие файлы. ORM-модели, Alembic head,
регистрацию routers, worker wiring и общий Actor/receipt contract ведёт Codex.
Изменения туда предложи отдельным точным patch; не делай параллельную миграцию
с тем же номером. Не редактируй текущие portrait manifests/checkpoints/assets.

Передай commit/patch, перечень файлов, проверки, ограничения и короткий сценарий
приёмки. Минимум: ruff check/format, mypy, релевантные pytest; миграции — offline
SQL и сверка DDL. Создавать новые тестовые БД локально/в CI нельзя. Есть одна рабочая
Goaletto DB; пока нет реальных пользователей, на ней разрешены проверки и reset.
DB-прогоны с reset согласовываются по времени с Codex, чтобы агенты не сбрасывали
данные друг друга. Никаких паролей/DSN в отчёте. Polling и рассылки для тестов
заменяй fake transport. HDC/deploy не входят в эти задания.

## Порядок выдачи

1. Можно параллельно выдать A — настройки, B — SQL-историю, C — аналитические отчёты.
   Файлы разные; общий dispatcher/models/migrations собирает Codex.
2. Операционную диагностику выдавать после фиксации этой сборки, можно параллельно
   с первой тройкой. Агента по истории после сдачи переключить на неё.
3. Тренировки выдавать после согласования weekly policy и численных коэффициентов.
   Стадион — после рабочего ledger Codex и согласованной demand policy.
4. Новую партию внешностей можно вести независимо после фиксации ID-диапазона и
   небольшой визуально принятой партии. Текущую генерацию lifecycle не дублировать.

## A · Настройки уведомлений и доступности · завершение UI AND-50

**Готово к выдаче сейчас.** Используй существующие Manager.reminders_enabled,
application.game_actions и sender check_recipient. Сделай `/settings`, кнопки
включить/выключить напоминания, экран текущего состояния и переход в клуб.
Изменение только действующим менеджером, атомарно, с operational dedupe.
Preferences не должны считаться матчем или продлевать владение автоматически.
Не вводи молча отдельную политику обязательных результатов/live-табло: сверяй
существующий sender contract. Availability подтверждается явным /start;
простое переключение preferences не объявляет Telegram снова доступным.

**Файлы:** новые application notification_preferences.py, bot settings_views.py/
settings_router.py, свои tests; integration registration отдаётся Codex.
**Приёмка:** retry без второго эффекта, истёкшее владение/старая кнопка,
opt-out analytics не влияет на бизнес-данные, queued reminder после отключения
не отправляется, сеть только после commit. Readme: docs/club-entry.md,
docs/outbox-delivery.md, docs/analytics/contract-v1.md.

## B · Ограниченные SQL-запросы истории · доработка AND-66

**Готово к выдаче сейчас.** В application.history убери загрузку всего архива
для одной страницы и запросы на каждый match. COUNT + bounded SELECT для списка
клуба/сезонов, загрузка только запрошенной страницы; stable ordering с ID.
Для player history используй сохранённые starting snapshots/receipts и команды
матча. Не включай игрока из команды соперника и не считай нахождение на bench
сыгранными минутами. Include MatchStage halftime events, event IDs dedup.

Сохрани текущие dataclasses и signatures, authorization и user effects, frozen
names/state hash. Если нужна projection/index, отдельно предложи миграцию Codex.
MVP не изобретай: либо явно согласованная формула, либо экран честно без MVP.

**Файлы:** application/history.py, свои query tests; presenters менять только
по необходимости, без регистрации routers.
**Приёмка:** 0/1/5/6 записей, границы страниц и одинаковая дата, старое владение,
чужой/retired игрок, stage events, неизменность terminal snapshot. На единственной
DB заполни большой искусственный архив, проверь результаты и EXPLAIN; количество
SQL и объём читаемых строк не растут со всем архивом для обычной страницы.
Документы: docs/match-results.md, docs/data-model.md.

## C · Воронки, зрелые когорты и доставка · AND-79, часть AND-77

**Готово к выдаче сейчас.** Дополняй существующий source_funnel и measurement
contract Python CLI/report module + bounded PostgreSQL aggregate queries.
Покажи start→claim→manual lineup, D1/D3/D7/D30 явных действий и исходы delivery
по outbox_delivery_attempts. Не переписывай collector/envelope и не добавляй API.

Для каждого показателя n/N, UTC окно, coverage/eligibility, waiting и unknown
отдельно. Internal/opt-out исключены; automation и доставка не retention.
Network-start timestamp — начало попытки, не доказательство получения Telegram.
Пассивное live-табло не активность менеджера. Отзывы не имитируй до AND-78.

**Файлы:** новые application analytics_reports.py/analytics_reports_cli.py,
свои tests и docs/analytics; existing acquisition contract не ломать.
**Приёмка:** ручные ожидаемые n/N на synthetic examples совпадают с SQL на одной
рабочей DB; границы суток/зрелости, late recorded_at, unknown, opt-out/retention.
Список отсутствующих событий передать Codex для AND-76/77. Grafana/deploy позже.
Документы: analytics/measurement-plan.md, contract-v1.md, storage.md, acquisition.md.

## D · Read-only диагностика и reconciliation · части AND-69/70

**После фиксации общей сборки.** Сделай Python CLI для состояния jobs/outbox:
backlog age, expired leases, exhausted unknown, match tick lag, результат без
receipt/неполный receipt, зависшие held/pending jobs и portrait capacity.
Вывод конечных кодов и агрегатов, без токенов, DSN и raw Telegram ошибок.
По умолчанию только read-only; план исправлений можно сформировать как документ.
Не удаляй историю и не ставь unknown в sent. Mutating recovery предлагается
отдельным patch для Codex, который проверит idempotency/locks.

**Файлы:** новый diagnostics module/CLI, tests, runbook. Приёмка: намеренно
повреждённые synthetic состояния, false positives при нормальном halftime,
read-only действительно не меняет строки. Не создавать резервную БД, не выполнять
restore/deploy/HDC операции. docs/match-runtime.md, outbox-delivery.md, deploy-hdc.md.

## E · Недельные тренировки · AND-58

**После согласования policy, не выдавать как безусловную реализацию сегодня.**
Pure calculation с versioned policy + атомарное применение раз в club/week.
Ограничения skills/potential, age/form; численные гипотезы явно согласовать.
Staff bonus через contract, без фиктивного нанятого персонала. Клубы без владельца
не тренируются; автоматическая тренировка не продлевает ownership.

**Приёмка:** retry не тренирует дважды, retirement/ownership перепроверяются под
lock, изменения в границах, нет age/draw в training. UI только по готовому use case.
Общие schema/calendar изменения интегрирует Codex.

## F · Стадион и доход · AND-63

**После AND-59 ledger и согласования стоимости/спроса.** Pure demand/attendance
calculation, upgrades/maintenance, ledger effects по контракту Codex.
Attendance не выше capacity; прогноз pinned до матча, replay не меняет crowd.
Версионируй costs/durations/supporter policy, не подбирай деньги молча.

**Приёмка:** upgrade/job/fixture retry не дублирует стройку/расход/доход,
баланс сходится с ledger, сохранённый crowd воспроизводим. Финансовые операции
не писать прямым `club.budget += ...` поверх общего ledger.

## Независимая художественная задача · запас новых identity · часть AND-44

Не продолжать чужой активный lifecycle checkpoint. Отдельные новые мужские
player identity, C — Dugout, whole portraits, source отдельно, garment masks,
экспорты 256/96 без искажения пропорций. Первый этап 10–20 лиц на визуальный review;
только после принятия масштабировать. Второму комплекту нужны ещё 1000 уникальных
людей; age/career variants существующих лиц не пополняют capacity. Закрепить
непересекающийся ID-range, не использовать rejected pilots, не повторять identity.

## Что последовательно делает Codex

1. **AND-53 live admission/UI:** реальные update IDs, match/ownership locks,
   серверный deadline, accepted/applied distinction, лимиты substitutions/windows/
   tactics по актуальным правилам; подключение к runtime и sender.
2. **AND-57 + сезонная AND-64 + AND-54:** один rollover после завершения всех
   матчей; simultaneous 2×2, ровно 5×10; age+1; persisted retirement draw
   5/10/20/35/55/80/100% в 32–38; полный последний сезон; retire/youth replacement,
   unique appearance allocation, новая membership/calendar/outbox без дублей.
3. **AND-59 ledger/reservations → AND-60 market:** одна система денег и
   пенсионных guards, отмена listings/refunds идемпотентно. Внешний stadium только
   после этого. UI рынка позже можно вынести отдельно.
4. Общие models/migrations/dispatcher/worker, review каждой внешней ветки,
   реальные PG race/rollback проверки на единственной DB; AND-76 instrumentation.
5. Финальная локальная сборка, Telegram smoke и HDC-подготовка отдельным этапом.

AND-61/83 staff lifecycle не выдавать целиком до утверждения вероятностей пенсии
60–70, условий career transition и стартового pool. Cup AND-65/Stars AND-67/
experiments AND-81 отложить до работающего первого цикла игры.
