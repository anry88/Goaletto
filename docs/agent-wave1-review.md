# Review и интеграция первой волны агентов · 2026-10-12

Ветка `codex/integrate-agent-wave1` объединяет sender AND-68, карточки (часть
AND-48), состав AND-49 и историю AND-66. Коммиты карточек `ce5c082f` и истории
`2125c73f` уже были в ancestry; sender/lineup и стыковочные исправления фиксируются
этой сборкой. Параллельные portrait assets/importer/checkpoints не включаются.

## Найденные и исправленные проблемы

- Sender: retry_after сокращался, приоритеты не записывались producers, поздний
  finish мог испортить новую попытку, две outbox rows могли дать два первых табло,
  старый tick мог откатить счёт. Добавлены уникальный board stream с lease/revision,
  attempt history, fresh preflight, deadline/token/attempt fences, bounded network
  timeout и независимые worker tasks. Подробнее docs/outbox-delivery.md.
- Состав: использовался вычисленный update number; callbacks не различали новое
  владение, deadline проверялся до ожидания locks, UI мог открыться во время live.
  Теперь реальные Telegram update IDs, ownership/revision scope (до 64 bytes),
  повторный deadline/status check под locks. Fixture не блокируется после Club
  против порядка runtime; Club lock сериализует snapshot. Captain persisted в
  AcceptedCommand; нового влияния капитана на симуляцию не вводилось.
- Карточки/routers: сеть выполнялась внутри общей transaction, cache мог теряться
  после restart, generic ошибка фото провоцировала новую загрузку. Теперь handlers
  открывают короткий transaction сами и отвечают после commit; durable cache
  отдельно load/store, повтор фото только при известном invalid file_id.
- Alembic: cache ORM не загружалась standalone env.py; server default расходился
  с migration0010. Модель перенесена в models/Base metadata. В 0009 asyncpg не
  принимал два UPDATE одним prepared statement — разделены op.execute.
- История: использовала текущие переименованные клубы, пропускала halftime stage
  substitutions, могла считать игрока соперника историей своего клуба. Названия
  из starting snapshots, MatchTick+MatchStage по persisted IDs, scope только своего
  team snapshot, retired card требует текущего клуба/истории этого клуба.

Codex зарегистрировал игровые routers, Database dependency injection и настоящие
Update IDs, `/start → offers → preview → claim → club`, `/squad /lineup /history
/table /club /help`, кнопочные переходы и legacy result callbacks.
`GOALETTO_GAME_ENABLED` и `GOALETTO_OUTBOX_ENABLED` остаются false по умолчанию.

## Проверки

- Ruff check и format: успешно (178 Python файлов).
- Mypy strict: 71 source file без ошибок.
- Unit/offline: 305 passed, 8 integration deselected.
- На настоящем PostgreSQL 17: 8 integration passed, 305 deselected.
- Alembic online upgrade head, metadata check без различий; реальный downgrade
  0010→0008→upgrade head; offline upgrade/downgrade; одна head0010.
- PostgreSQL: seed/retry и 1000 уникальных appearance reservations, конкурентный
  tier5 claim, конкурентный save с revision, snapshot и lock deadline; parallel
  board stream claim/edit; durable file_id через новую session; worker rollback
  и SKIP LOCKED; полный матч из 30 ticks + halftime и повтор финала без вторых очков.
- Реальный aiogram Dispatcher.feed_update: start/offers/preview/claim, команды
  клуба/состава/истории/таблицы, captain/tactic mutations с настоящими update IDs.
  Fake Telegram проверяет answerCallbackQuery первым и отсутствие DB transaction
  при каждом send/edit. Никаких реальных Telegram запросов.

Длительность матча проверяется по scheduled deadlines с управляемым временем;
это не 33-минутная запись реального polling. Нет проверки HDC, реального Telegram
API, rollover/экономики или человеческих live-команд, которые ещё не реализованы.

## Единственная рабочая БД

Документы/предыдущий основной чат подтвердили: persistent Goaletto DB и HDC deploy
ранее не создавались. Старые временные DB были удалены. Порт5432 занят New Universe.
Создана одна основная локальная `goaletto` в `goaletto-postgres-1`, PostgreSQL17,
127.0.0.1:5433, постоянный volume `goaletto_postgres_data`. Пароль в host-only .env
(не в Git); `.env` права0600. Другой проект не изменялся.

Пользователь уточнил: никаких дополнительных тестовых DB локально/в CI; пока нет
реальных пользователей, проверять/reset на основной Goaletto DB разрешено.
Fixture требует environment development, database goaletto и явный
`GOALETTO_ALLOW_DEVELOPMENT_RESET=1`; перед каждым integration test сбрасывает
игровые таблицы. Это не режим для игры с реальными пользователями.

После тестов искусственные менеджеры/матчи удалены. Оставлен первый основной мир:
50 клубов, 1000 футболистов и уникальных faces, 0 менеджеров. Все calendar jobs
held. Основной календарь/worker/polling/sender не запущены; HDC не изменялся.
Локальный запуск postgres: `docker compose -f compose.yaml -f compose.local.yaml
up -d postgres`. CI без DB service, unit+offline migrations.

## Границы завершения

AND-49 и AND-68 реализованы и проверены в сборке; AND-56 имеет рабочие handlers
и реальные атомарные standings. AND-48 остаётся частичным из-за staff/recovery/
lifecycle UI. AND-66 требует bounded SQL и решения по MVP; не закрывать весь parent.
AND-47/75 game start path подключён; дальнейшая capacity/attribution приёмка
описана в их задачах. Live admission, rollover/retirement и ledger/market — Codex.
Конкретные внешние задания: docs/agent-handoff-plan.md.
