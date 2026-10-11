# Архитектура

Один Python-проект, два основных процесса: bot и worker.
PostgreSQL — источник истины. Redis, HTTP backend и frontend сейчас не требуются.

```mermaid
flowchart LR
    TG[Telegram] --> BOT[aiogram / long polling]
    BOT --> APP[Application use cases]
    APP --> DB[(PostgreSQL)]
    WORKER[Worker / durable jobs] --> APP
    APP --> DOMAIN[Pure domain rules]
    DB --> OUTBOX[Notification outbox]
    OUTBOX --> DELIVERY[Delivery adapter · planned]
    DELIVERY --> TG
```

## Границы

- bot: форматирование, кнопки, маршрутизация, немедленный answerCallbackQuery.
- domain: часы, рейтинги, симуляция, таблицы, правила переходов; переданный seed и
  явные входные данные. Сетевых вызовов и импортов framework здесь нет.
- application: права на клуб, игровые use cases, транзакции и последовательность эффектов.
- infrastructure: SQLAlchemy, репозитории, jobs/outbox.
- worker: получение просроченной работы и вызов application.
- portraits: recipe → SVG; PNG для Telegram рендерятся и кешируются отдельным адаптером.

## Долговечные операции

Уникальность тика: (match_id, tick_number). Сохранять в одной транзакции:
применённые команды, новое состояние, события, следующий срок и outbox.
Dedupe key очереди помогает, но не заменяет доменную защиту в БД.

Для claim клуба: блокировка свободного клуба и уникальный owner_telegram_id.
Для расширения: сериализованное выделение комплекта с повторной проверкой свободных
клубов лиги 5 после получения блокировки.
Для сезона: блокировка комплекта, уникальный переход для season_id, заранее рассчитанный
план для всех пяти лиг, обновление всех членств в одной транзакции.
Для денег: integer минимальные единицы, неизменяемый ledger и повторобезопасные операции.

## Jobs

Начальная таблица jobs содержит due_at, dedupe_key, kind, payload, status, attempts.
Диагностический handler system.ping показывает FOR UPDATE SKIP LOCKED и rollback.
Неизвестная задача помечается failed, чтобы очередь не зациклилась.
Обработчики игрового мира и ретраи ещё не реализованы.
Короткие DB-only handlers выполняются в транзакции с блокировкой строки.
Длительные/сетевые операции требуют lease и не должны удерживать DB lock.

## Уведомления

Outbox создаётся в той же транзакции, что и игровое событие.
Сетевой sender работает отдельно, учитывает retry_after и приоритеты.
Для повторных live-обновлений сохранять message_id и редактировать существующее табло.
Атомарную отправку сообщения в Telegram и commit PostgreSQL гарантировать нельзя:
доставка at-least-once может продублировать новое сообщение при аварии после отправки.
Документировать это ограничение, использовать стабильное табло и повторобезопасные edit.
Sender и политика доставки в плане, начальный worker сообщения не отправляет.

## Схема сейчас и далее

Аналитика запланирована до первого привлечения пользователей:
[контракт и метрики](analytics/measurement-plan.md), [события](analytics/event-catalog.md).
Application пишет успешное бизнес-событие атомарно с состоянием/accepted command;
infrastructure обеспечивает versioning, dedupe и псевдонимы до claim клуба.
Domain не импортирует аналитический сборщик. User-действия отделены от system/automation;
доставка уведомления не означает прочтение и не создаёт retention.
Отчёты используют PostgreSQL и операторский Grafana на HDC без публичного API.
Эти таблицы и сбор пока не реализованы.

Сейчас миграция содержит league_sets, leagues, clubs, jobs, outbox_messages.
Она не обеспечивает самостоятельно ровно 10 клубов в лиге: это инвариант seed/rollover.
Далее отдельными миграциями: managers, players, staff, seasons, fixtures, matches,
match_ticks, commands, standings, training, auctions, ledger, scouting, academy.
Схема не должна подменять реализацию соответствующих use cases.

Источники:
[aiogram polling](https://docs.aiogram.dev/en/latest/dispatcher/long_polling.html),
[PostgreSQL locking](https://www.postgresql.org/docs/current/sql-select.html),
[SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html).

Владение и простая автоматика: [bot-clubs.md](bot-clubs.md). Дедлайн неактивности —
последнее игровое действие + шесть календарных месяцев, отдельно от матчевых jobs.
Освобождение/возврат и повторные напоминания защищаются транзакцией и dedupe.
