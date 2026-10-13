# Внешняя волна после приёмки A–D · 13 октября 2026

Общая опубликованная база на момент подготовки: main63812358. Для начала работы
всегда fetch актуальной origin/main, новая ветка, SHA в сдаче. Каждый файл ниже
самодостаточен: копировать целиком одному исполнителю. Агенты не запущены.

| Пакет | Промпт | Практический результат | Связь |
|---|---|---|---|
| A | [Добровольный отзыв](01-voluntary-feedback.md) | Отзывы в Telegram после результата, сначала internal | Часть AND-78; прежний E |
| B | [Сезонное старение](02-player-season-plan.md) | Чистый переход active→last season→retired для общего rollover | Часть AND-64/57 |
| C | [Гонки PostgreSQL](03-postgres-races.md) | Дополнительные настоящие regression cases рынка/sender | Часть AND-71 |
| D | [Матчевые возможности](04-match-opportunity-calculator.md) | Чистый расчёт двух недельных n/N с корректным waiting/coverage | Часть AND-77/79/82 |

Приоритет A → B → C → D. A–D можно выдать параллельно: app-файлы разные, общий
models/config/migration/UI wiring меняет только A. В A retention комментариев
обязателен для включения сбора; невыбранный срок не придумывается. Его отсутствие
не мешает подготовке кода и кнопочных ответов. Тесты без реальных Telegram send.

B/D — вычислительные компоненты с реальным reusable API и строгими acceptance
fixtures. Durable rollover и production KPI сами по себе ещё не появляются:
Codex интегрирует locks/persistence/market/outbox и SQL/data coverage после review.
C пишет PG-тесты; выполняет их только Codex после backup на одной Goaletto DB.
A — продолжение старого пакета E: не выдавать их двум агентам одновременно.

Приёмка: review/fix,ruff/format/mypy,unit; для A/C — последовательный PG,
для A — native Telegram RU/EN. После приёмки merge/push main и точное обновление
Linear по подпакетам. Генерация картинок, AND-84, полный staff/academy/cup/Stars,
HDC и неутверждённые численные правила в эту волну не входят.

[Предыдущая приёмка](../../agent-wave3-review.md); [игровые правила](../../game-rules.md).
