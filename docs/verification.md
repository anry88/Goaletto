# Проверка начального каркаса · 2026-10-11

## Вторая волна + live admission · 2026-10-13

Четыре ветки сведены в main9734c4fe: settings, bounded history, analytics reports,
read-only diagnostics. Исправления, миграция0011 и372 unit/16 PG —
[agent-wave2-review.md](agent-wave2-review.md). После Imagegen429 продолжен AND-53;
[v1 compatibility и live-приёмка](live-commands.md). PostgreSQL одна основная
Goaletto DB; новых test DB нет, HDC/polling/Telegram не запускались.

## Текущая общая сборка первой волны · 2026-10-12

305 unit/offline passed; 8 real PostgreSQL integration passed. Ruff/format (178
файлов), mypy71, online upgrade/metadata check и downgrade0010→0008→upgradehead
успешны. Рабочая локальная Goaletto DB одна, порт5433, без реальных пользователей;
дополнительные тестовые DB не создаются. PostgreSQL race/rollback/full match и
настоящий Dispatcher проверены с fake Telegram. Polling/Telegram/HDC не запускались.
Полный перечень исправлений и ограничений — [agent-wave1-review.md](agent-wave1-review.md).

Раздел sender ниже описывает промежуточный этап до PostgreSQL-проверки и интеграции.

## Review/integration sender AND-68 · 2026-10-12

- Исправлены retry_after clipping, поздние finish/skip, отсутствие persisted
  priority у producers, параллельный первый send и откат табло старым тиком.
- Добавлены board stream lease/revision, operational attempt history и guard
  более нового подтверждения bot availability. Telegram HTML-экранируется;
  abandoned не объявляется присуждённым результатом.
- Worker wiring проверен без запуска: независимые jobs/sender tasks, opt-in flag,
  token validation, cancellation/cleanup. Fake transport проверяет реально
  открытые transaction contexts; запросы ограничены временем.
- Ruff check/format и mypy64 модулей успешны. Целевые sender/transport/worker/
  schema/entry проверки:60 passed. Общий checkout (включая параллельные изменения
  карточек/состава и портретов):289 passed,2 integration deselected.
- Offline upgrade до текущего head0010 и downgrade0009→0008 сформированы;
  frozen CREATE TABLE/index/checks сверены с ORM. Одна Alembic head.
- PostgreSQL, polling, seed/activation --apply, sender и HDC не запускались.
  Реальные PG races/rollback/constraints и Telegram не считаются проверенными.
- Изменения остаются в рабочем дереве общей сборки; AND-68 In Progress.
  Чужие портретные изменения не включались в sender review.

Исторические прогоны ниже описывают состояние на указанный этап.

Исторический прогон ниже был выполнен до текущего решения пользователя не создавать
дополнительные тестовые БД. Уточнение 2026-10-12 разрешает проверки/reset
на одной рабочей Goaletto DB, пока нет реальных пользователей.

- Ruff check и format: успешно.
- mypy strict: успешно, 18 исходных Python-модулей.
- pytest с изолированным PostgreSQL 17: 23 теста успешно.
- Alembic upgrade head: успешно; alembic check: нет расхождений со схемой.
- Проверены повторное enqueue, rollback незавершённой задачи и SKIP LOCKED.
- Docker Compose HDC merge/config --quiet: успешно.
- Docker image python:3.12-slim: успешно собран; application работает от non-root user.
- SVG/PNG: 6 портретов, recipes, contact sheet и HTML; компоненты проверены как XML.
- Локальные ссылки документации: без отсутствующих файлов.

Проверки относятся к начальному scaffold. Реальные матчи, club claim, сезонный
rollover, экономика и отправка outbox-уведомлений пока не реализованы.
Реальный Telegram-бот без токена и HDC production-деплой не запускались.
CI на GitHub проверяется отдельно после первого push.

## Цельные возрастные портреты · 2026-10-11

- Базовый банк: 1 000 игроков и 80 исходных сотрудников.
- lifecycle-v1: 87 исходных листов, 1 740 дополнительных PNG256/96 вариантов;
  240 игроков со всеми семью группами и 20 с тремя. Полный банк не завершён:
  275 листов остановлены лимитом Imagegen, checkpoint и все prompts сохранены.
- Аудит всех 1 740 новых файлов: реальный alpha, маски, смена двух цветов формы,
  неизменность пикселей вне одежды, хеши и отсутствие полос соседнего ряда над волосами.
- Визуально просмотрены все 13 возрастных сравнительных листов и пары с самым
  высоким расхождением coarse shape hash. Не обнаружены явные перестановки лиц;
  это просмотр иллюстраций, не автоматическое доказательство идентичности.
- Ruff check/format и mypy: успешно. Pytest: 64 passed, 2 skipped;
  PostgreSQL integration пропущены без отдельной тестовой БД.
- 5 686 локальных ссылок/изображений галерей проверены, отсутствующих нет.
- Новые возрастные стадии не увеличивают ёмкость банка уникальных людей.
  `recipe_for_age` сохраняет identity; missing stage даёт ошибку. Старые base
  variant IDs и PNG сохранены. PG assignment, старение/карьеры и Telegram cache
  ещё не реализованы.

## Основание данных и контракт аналитики · 2026-10-12

- Новая миграция `0002_game_foundation`: 19 новых таблиц, всего 24 с исходной схемой.
  Миграция содержит замороженный DDL, не импортирует текущие ORM-модели и не делает seed.
- Offline upgrade/downgrade успешно сформированы без соединения с БД; новые CREATE
  TABLE и все индексы сопоставлены с PostgreSQL DDL моделей. Это не выполнение SQL сервером.
- Контракт v1: allowlist/required refs/origin, закрытый envelope, стабильный effect key,
  отказ от произвольного текста, различие user activity и ownership activity, UTC-границы
  зрелости, D7, перенос матча, 30-минутная сессия и шесть календарных месяцев.
- Каталог: 1080 identity, дополнительные age/staff варианты не увеличивают число людей;
  все registry asset/mask пути существуют. DB registry и allocation используют общий
  advisory transaction lock; гонки/rollback на PostgreSQL пока не проверены.
- Ruff check/format: успешно, 94 Python-файла. mypy strict: успешно, 26 исходных модулей.
- pytest без integration: **96 passed, 2 deselected**. Новых БД не создано для прогона.
- Исполнение миграции, `alembic check` и PostgreSQL integration не выполнялись по
  указанию пользователя. Созданный до уточнения временный контейнер удалён вместе
  с его данными. CI больше не создаёт PostgreSQL service и не выполняет online migration.
- Ни сбор событий, ни club claim/seed, ни симуляция/пенсия/найм из схемы автоматически
  не появляются. Worker остаётся `system.ping`, Telegram-бот — preview; HDC не менялся.

## Мир и двухкруговой календарь · 2026-10-12

- Офлайн `initial`, seed 0, пример kickoff 2026-10-17 18:00 UTC: 5 лиг, 50 клубов,
  1000 футболистов и уникальных whole identity; 450 fixtures, 15302 held jobs.
- Одинаковое позиционное/возрастное покрытие всех составов, 2 GK и фиксированная XI
  4-4-2. Среднее восьми core skills — 8.91875 у каждого клуба, spread 0;
  проверка является стартовым proxy, не гарантией окончательного матчевого баланса.
- Каждый portrait подходит визуальной возрастной группе; исчерпание identities/
  age coverage даёт ошибку. Зарезервированные лица не используются во втором комплекте.
- Circle calendar: 18 туров, 5 матчей/тур, по одной home/away встрече пары,
  9 домашних и 9 гостевых игр клуба, стабильные UUID и dedupe deadlines.
- 60-секундные тики/3-минутный перерыв: 15-й на 15:00, second-half на 18:00,
  16-й на 19:00, 30-й на 33:00. Две субботы межсезонья без fixtures.
- Пенсионные threshold draw проверены для каждого возраста 32–38; 38 гарантированно
  последний сезон. Initial policy сохраняется при seed, сезонное старение ещё не реализовано.
- SQL-recording unit выполняет application-код без БД, компилирует batches и проверяет
  1000 уникальных reservations/held jobs/receipt последним. Повтор готового seed после
  начала матчей/изменений каталога не перестраивает мир, конфликт config отклоняется.
- Ruff/format: успешно. mypy strict: 32 исходных модуля.
  pytest без БД: **134 passed, 2 deselected**. Новых DB/контейнеров не создавалось.
- Offline upgrade до 0003 и downgrade 0003→0002 сформированы; полный CREATE TABLE/index
  новых таблиц соответствует ORM. Исполнение SQL, PG race/rollback/lock проверки и
  `--apply` не запускались. HDC, токен, изображения и действующие боты не менялись.
- AND-46 реализована как seed use case/CLI. AND-55: планировщик/fixtures/held jobs готовы,
  задача остаётся In Progress до подключения runtime AND-52 и уведомлений AND-68.

## Чистый матчевый движок и поддержка трибун · 2026-10-12

- regulation-v1: 30 ticks/90 game minutes, отдельный halftime, immutable snapshots,
  named seeded streams, closed checkpoints и stable event IDs.
- Мораль 0–100, временная домашняя поддержка по pinned home_fans <= stadium capacity.
  Большой пустой стадион не даёт бонус; расчёт спроса/билетного дохода пока AND-63.
- Golden seed7 1:2, 57 событий, SHA ae4002fb500d7769e280a9939d3f345f78e935b6c1b1be6a3f0bb792850c6841;
  checkpoint после 17 тиков даёт тот же финальный state и 27 оставшихся событий.
- 200 сопоставимых seeds: home/away/draw 78/56/66 при 2500 болельщиках против
  67/67/66 без поддержки; mean goals 2.14, chances25.22. Это начальный баланс.
- Проверены лимиты 5/3/3, halftime windows, duplicate commands/ticks, cards/injuries,
  injury-only bench order, GK fallback и abandoned без присуждения счёта.
- Ruff check/format, mypy strict39 модулей: успешно. pytest: **156 passed, 2 deselected**.
- DB/контейнеры/polling/HDC не запускались; runtime/persistence/outbox ещё не подключены.

## Транзакционный collector · 2026-10-12

- Миграция 0004: coverage/exclusions/sessions/journey, закрытые поля/name allowlist,
  immutable UPDATE guard, export view без Telegram mapping и внутренних аккаунтов.
- Application collector разделяет транзакцию game use case, не commit/сеть/экспорт.
  Unit SQL-recording: dedupe/conflict, <30m session, rejection без session touch,
  background без activity, internal/opt-out, first start после cleanup, retention.
- Ruff/format и mypy40 модулей: успешно. **166 passed, 2 deselected**; offline SQL
  upgrade сформирован, CREATE TABLE/index соответствует ORM.
- БД/контейнеры не создавались; PG races/rollback/CASCADE/GRANT не выполнялись.
  Collector не включён в preview; HDC и текущие боты не изменены.

## Regulation runtime · 2026-10-12

- Durable worker/activation API/CLI и миграция0005; seed jobs остаются held до explicit
  activation. Snapshot pin/immutable trigger, MatchTick/Stage/events/hash/outbox.
- Unit все30 transitions дают тот же golden hash; 15/18/19/33m boundaries, duplicate/
  restart, delayed start vs late ticks, pause/resume/postpone, halftime commands,
  applied tick FK order, deadline cancellation, abandoned и notification outbox.
- Worker savepoint/deferred/failed attempts проверены без внешних вызовов.
- Ruff/format, mypy42: успешно. Full pytest **182 passed, 2 deselected**, после него
  добавлен crowd pin test; все **17 runtime/dispatcher tests** проходят.
- Offline CREATE/index/ALTER соответствует ORM; upgrade сформирован.
- PG server/races/rollback/triggers не выполнялись; БД/контейнеры не создавались.
  Seed/runtime --apply/polling/HDC не запускались; sender/standings/rollover ещё в плане.
- GitHub CI предыдущих engine/collector commits успешен.

## Результаты и таблицы · 2026-10-12

- Pure3/1/0, points/GD/GF/preseason draw; MatchResult receipt/standing locks и
  финальный тик/outbox в одной transaction. Abandoned без автоматических очков.
- Telegram presenters5/page, callbacks<=64 bytes, canonical UUID/HTML escapes.
  Реальные report/table handlers не подключены; AND-56 остаётся In Progress.
- Ruff/format и mypy45: успешно; полный pytest **190 passed, 2 deselected**.
  После прогона усилена проверка receipt identity/date и повторно проверены relevant tests.
- Offline0006 upgrade/DDL успешно, PG не запускался. CI runtime645b109 прошёл.

## Attribution foundation · 2026-10-12

- Opaque campaigns, immutable first-touch до claim, direct/unknown/known и retry
  без нового manager/cohort/session. Internal/opt-out/retention учтены.
- SQL source funnel/CLI, зрелость24h/waiting, same ownership readiness, coverage
  clipping и aggregate guards; реальное SQL n/N на PostgreSQL не проверялось.
- Ruff/format, mypy48: успешно. Full pytest **204 passed, 2 deselected**.
  Offline0007 DDL/upgrade, CLI preview без .env/соединения: успешно.
- AND-75 остаётся In Progress до game /start path в AND-47. Preview cohort отсутствует.
  DB/HDC/Telegram не запускались; CI предыдущего results9ce74a0 прошёл.

## Club entry/lease backend · 2026-10-12

- Atomic tier5 claim/operation retry, budget unchanged, complete world expansion
  preflight/no_capacity, actor scope/operational receipt независимо от analytics.
-6 calendar months, end-month clamp, return/release guards, stale/opt-out reminders,
  expiry при отключённых reminders; pending accepted commands cancelled после expiry.
- Backend не routed в Telegram: AND-47/50/75 остаются In Progress. Sender/update
  preferences и проверки прямо перед send ещё не реализованы.
- Ruff/format, mypy50: успешно. Full pytest **212 passed, 2 deselected**, после него добавлен guard required refs
  без analytics; все16 targeted entry/dispatcher/schema tests проходят.
- Offline0008 upgrade/downgrade/DDL без БД успешно. Существующий банк обеспечивает
  лишь первый полный комплект; расширение ждёт новых уникальных внешностей.
- DB/HDC/polling не запускались, реальные PG locks/races/rollback не проверены.
