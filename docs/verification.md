# Проверка начального каркаса · 2026-10-11

Исторический прогон ниже был выполнен до текущего решения пользователя не создавать
тестовые БД. На новом этапе PostgreSQL локально/в CI не поднимается.

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
