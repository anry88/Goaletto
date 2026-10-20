# ⚽ Goaletto · Голетто

[Подготовка к запуску в коде](docs/prelaunch-code-2026-10-20.md): однократное
переименование, флаги, signed финансы/банкротство, защита реальных данных и
конфигурация PostgreSQL на D. Это не новая HDC-приёмка.

[Закрытый HDC-запуск](docs/hdc-acceptance-2026-10-20.md): bot/worker/PostgreSQL,
проверенный backup в Documents на рабочем Mac и restore из этой копии.
Доступ пока только внутренним аккаунтам; внешняя launch-приёмка остаётся открытой.

[Итоговая приёмка штаба, второй карьеры и развития](docs/staff-development-acceptance.md)
— AND-61/83/64: игровые потоки подключены, сезонная калибровка учитывает все матчи.

[Операторский Grafana](docs/analytics/grafana.md): bot/worker отдают Prometheus-метрики,
существующая VictoriaMetrics собирает их для трёх dashboard. Команды, Telegram API,
worker/SQL/process и фоновые DB-снимки: [мониторинг](docs/prometheus-monitoring.md).

[Работоспособность worker](docs/worker-health.md) и [нагрузка перед запуском](docs/launch-load.md):
отдельные jobs/sender heartbeat, Docker healthcheck и измеренные live-профили 50/75 матчей.

[Восстановление и технические задержки](docs/runtime-observation.md) — AND-69/77:
потерянное live-задание возвращается без перемотки, commit/render/очередь/Telegram
измеряются отдельно от игровой активности.

[Фоновый запас клубов и общий календарь](docs/world-reserve.md) — AND-47/119:
регистрация выдаёт готовый клуб, прошедшие матчи нового комплекта играют боты.

[Вход первых игроков и отчёты](docs/launch-measurement.md) — AND-75/76/79:
источники, помощь после ошибок, последние действия и реальные матчевые возможности.
[Внутренние добровольные отзывы](docs/feedback.md) — AND-78; [проверка аналитики
перед привлечением](docs/analytics/launch-gate.md) — локальный этап AND-82.

[Кубок и матчи навылет](docs/tournament-runtime.md) — AND-123/124/125:
97 матчей, настоящее допвремя и пенальти, нейтральный финал и раздел выручки.
[Кубки чемпионов](docs/champions-runtime.md) сохраняют отбор и группы нескольких
комплектов. [Межкомплектный переход](docs/cross-set-rollover.md) атомарно меняет
всю совместимую когорту после её готовности; новые комплекты входят в общий календарь.
[Добровольные товарищеские](docs/friendly-runtime.md) — `/friendly`;
[выпуск и улучшения академии](docs/academy-runtime.md) — `/academy`.

[Карточки персонала и форма клуба](docs/staff-cards.md): портрет, квалификация,
условия найма и комплект в настоящих клубных цветах. [Задания внешним агентам](docs/agent-prompts/README.md).

[Сезонный спонсор](docs/season-sponsorship.md): фиксированная ставка по итогам сезона,
болельщикам и новой лиге. [Календарь тренировок](docs/training.md) в `/train` позволяет
выбирать свободные UTC-даты на десять дней вперёд.

[Матчевое развитие](docs/match-development.md): небольшой рост за фактические минуты.
[Матчевая практика и форма](docs/match-practice-runtime.md): все четыре вида матчей
сохраняют изменение формы и прогресс; отчёт показывает результат каждого игрока.
[Бывшие игроки в штабе](docs/retired-staff-offers.md): первое предложение клубу и −20% зарплаты.

[Тренировки и вклад штаба](docs/training.md): специалисты открывают профильные
занятия, главный тренер помогает морали и лидерству. Рост имеет общий сезонный предел.

[Главные тренеры и замена штаба](docs/coaches-and-staff.md): возможности тренера
ограничивают предматчевую тактику и live-команды; кадровые операции — через `/staff`.
Главного тренера можно только заменить наймом другого.

[Скаутские задания](docs/scouting.md) доступны через `/scout`; новые рыночные и
скаутские игроки могут иметь [2–3 близкие позиции](docs/player-positions.md).


AND-105/107: [экономика за четырёхдневный цикл](docs/economy-calculation.md),
разные публичные футболисты 26–30 лет, цена и зарплата по качеству/возрасту.
Существующие контракты и история платежей сохраняются.


Футбольный менеджер целиком в Telegram: клуб, состав, тренировки, трансферы и live-матчи.
Python, сообщения и inline-кнопки; без Mini App и JavaScript.

**Состояние: собрана первая игровая вертикаль, полная игра ещё в разработке.**
Доступны Telegram-вход и получение клуба, карточки, предматчевый состав, история/отчёты, таблицы и настройки напоминаний,
чистые правила времени и переходов между лигами, миграции основания PostgreSQL,
контракт и транзакционный collector аналитических событий, инициализация мира 5×10×20, двухкруговой календарь,
граница назначения внешностей, диагностический worker и портретные инструменты.
Пользователь выбрал цельные иллюстрации вместо
сборки деталей: первый комплект 50 × 20 требует 1 000 разных футбольных внешностей,
в первом составе, и отдельный банк персонала. Уточнение 2026-10-13: каталог
можно переиспользовать для новых людей, сохраняя лицо и историю каждого. [Проверка подходов](docs/portrait-feasibility.md),
[библиотека цельных людей](assets/portraits/whole-v2/README.md), [вторая карьера](docs/staff-lifecycle.md).
Экспорт 256/96px, одежда перекрашивается локально в цвета клуба. Новый каталог на
визуальной проверке. Создана одна локальная рабочая БД Goaletto; PostgreSQL-проверки выполняются на ней.
Чистый [матчевый движок](docs/match-engine.md) симулирует 30 тиков, поддерживает
checkpoint/replay и события. Durable [live-worker](docs/match-runtime.md) реализован; активация включает 10-дневный горизонт календаря.
Очки и таблицы начисляются атомарно с финальным тиком. Backend регистрации/выдачи клуба/6-месячного владения реализован:
[club-entry.md](docs/club-entry.md). Игровой `/start` и кнопки подключены при `GOALETTO_GAME_ENABLED=true`.
[Добровольные отзывы](docs/feedback.md) включаются отдельно для internal/test через `/feedback`;
[Live-команды](docs/live-commands.md) подключены через `/match`;
[RU/EN и имена](docs/languages-and-names.md) переключаются через `/settings`;
[дневные тренировки](docs/training.md) доступны через `/train`; [экономика и денежный журнал](docs/club-finances.md) — через `/finance`; [стадион и посещаемость](docs/stadium.md) — через `/stadium`; [аукционный рынок](docs/transfer-auctions.md) — через `/market`; [общий rollover](docs/season-rollover.md) подключён к worker.
[Квалификация персонала](docs/staff-quality.md) (пять уровней, новые сотрудники 50–56 лет) и
[настройки часового пояса/времени](docs/kickoff-preferences.md) доступны в Telegram.
[Расписание на 10 суток](docs/schedule-screen.md) — `/calendar`;
[межматчевая энергия](docs/player-recovery.md) сохраняется и восстанавливается по реальному времени.
[Свободные агенты и персонал](docs/candidate-pools.md) — общие ограниченные пулы, `/freeagents` и `/staff`. [Outbox sender](docs/outbox-delivery.md) подключён отдельным
циклом worker; по умолчанию выключен (`GOALETTO_OUTBOX_ENABLED=false`).
В табло и `/match` добавлена компактная [лента событий](docs/live-commentary.md)
с голами, опасными моментами, карточками, травмами и применёнными командами.
После завершения [результат приходит отдельным сообщением](docs/final-match-notification.md)
владельцам; `/match` сохраняет итог и кнопку полного отчёта.

- [Актуальный GDD](football-manager-bot-gdd.md)
- [Правила и принятые решения](docs/game-rules.md)
- [Документация](docs/README.md)
- [Архитектура](docs/architecture.md)
- [Воронки, удержание и обратная связь](docs/analytics/measurement-plan.md)
- [Стиль и портреты](docs/art-direction.md)
- [Задачи в Linear](https://linear.app/andrei-brazhnikov/project/goaletto-eb8cb486bc79)
- [GitHub](https://github.com/anry88/Goaletto)

## Зафиксированные правила

- Тик: 60 секунд = 3 игровые минуты. Два тайма по 15 тиков и перерыв 3 минуты:
  90 игровых минут за 33 реальные минуты.
- Первый комплект: 5 лиг × 10 клубов = 50 клубов; все изначально без владельцев.
- Места 1–2 поднимаются, 9–10 опускаются на соседний уровень; по новым вводным
  лига назначения может принадлежать другому совместимому комплекту.
  Из первой лиги нет повышения, из пятой нет вылета; стыковых нет.
- Новичок получает только свободный бот-клуб пятой лиги.
  Если свободных мест в нижних лигах нет, создаётся новый комплект 5×10.
- Реализован [четырёхдневный календарь](docs/four-day-calendar.md): 18 циклов сезона
  и 8 дней межсезонья, полный оборот 80 дней. Точные старты назначаются на 10 дней
  вперёд с учётом часового пояса и удобного времени. Обычный кубок подключён;
  подключены также Кубки чемпионов и добровольные товарищеские.
- Бот-клубы: близкие базовые составы, без тренировок и нейросетей; замены только
  травмированных, пополнение после пенсии из молодёжки в межсезонье.
- Владение сохраняется шесть календарных месяцев с последнего игрового действия;
  периодические напоминания, затем освобождение. [Правила](docs/bot-clubs.md).
- Портреты: отдельные мужские банки игроков и персонала, одежда в двух цветах клуба.
  Направление C — Dugout согласовано; цельный каталог проходит визуальную проверку,
  назначенное лицо человека сохраняется; новые люди могут переиспользовать каталог.
- Пенсия: утверждённая шкала 5/10/20/35/55/80/100% в 32–38; заранее объявленный
  последний сезон запрещает сделки с футболистом. [Контракт](docs/player-lifecycle.md).

## Локальная разработка

Python 3.12+, PostgreSQL 17 (или совместимая проверенная версия).

`main` — общая ветка с принятыми наработками. Новую задачу начинайте отдельной
веткой от актуального `origin/main`; после review и проверок изменения возвращаются
в `main`. Следующая волна агентов снова стартует от обновлённого `main`.
Порядок и границы заданий — [план внешней разработки](docs/agent-handoff-plan.md).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
cp .env.example .env
docker compose -f compose.yaml -f compose.local.yaml up -d postgres
alembic upgrade head
goaletto-worker
```

В отдельном терминале задайте GOALETTO_BOT_TOKEN в .env и запустите goaletto-bot.
Для Windows PowerShell активация: .venv\Scripts\Activate.ps1.

Исходящие уведомления worker включаются отдельно через `GOALETTO_OUTBOX_ENABLED=true`
с тем же токеном, после миграций и подготовки игрового потока. Sender не запускает
polling и не удерживает DB-транзакцию во время Telegram-вызова. Он проверен
на PostgreSQL с fake Telegram; 2026-10-20 закрытый HDC smoke также использовал
настоящие polling и Telegram IO для внутренних аккаунтов.
[Эксплуатационная приёмка](docs/hdc-acceptance-2026-10-20.md).
Локальная БД использует постоянный volume и порт 5433; 5432 занят другим проектом.
Для игры включите `GOALETTO_GAME_ENABLED=true`; календарь активируется отдельно.
Для закрытого теста задайте host-only списки `GOALETTO_ALLOWED_TELEGRAM_IDS` и
`GOALETTO_INTERNAL_TELEGRAM_IDS` (JSON arrays). [Сценарий двух аккаунтов](docs/closed-telegram-test.md).

Весь стек локально:
1. В .env задайте URL с хостом **postgres**, а не localhost:
   postgresql+asyncpg://goaletto:YOUR_PASSWORD@postgres:5432/goaletto.
2. Установите такой же POSTGRES_PASSWORD и собственный токен.
3. Выполните docker compose up -d --build.
Миграция должна успешно завершиться до старта bot и worker.
Не запускайте одновременно локальный polling и контейнер bot с одним токеном.

## Операторские отчёты

Read-only `python -m goaletto.diagnostics.cli --format json` проверяет jobs/matches/
outbox; `python -m goaletto.application.analytics_reports_cli --help` — воронки,
retention и delivery. CLI используют согласованный read-only snapshot PostgreSQL.
[Приёмка второй волны](docs/agent-wave2-review.md), [диагностика](docs/runtime-diagnostics-runbook.md),
[границы метрик](docs/analytics/reports.md).

## Проверки

```bash
ruff check .
ruff format --check .
mypy
pytest -q -m 'not integration'
mkdir -p work
alembic upgrade head --sql > work/migration.sql
```

Уточнение пользователя 2026-10-12: не создавать дополнительные тестовые БД.
Пока нет реальных пользователей, проверять на единственной рабочей Goaletto DB
и при необходимости сбрасывать игровые данные. CI остаётся без новой DB/service.
Сохранять и восстанавливать тестовый мир перед каждым прогоном не требуется:
во время разработки настоящую БД Goaletto можно свободно пересоздавать.
Локальный интеграционный прогон **сбрасывает данные Goaletto перед каждым тестом**:

```bash
GOALETTO_ALLOW_DEVELOPMENT_RESET=1 python -m pytest -q -m integration
```

Требуются `GOALETTO_ENVIRONMENT=development`, настроенная база `goaletto` и миграции
head. Не направляйте этот прогон в БД другого проекта или в игру с пользователями.
Online migrations, `alembic check`, concurrency/rollback/full match уже проверены.
Каталог и транзакционные границы описаны в [схеме данных](docs/data-model.md).

## Мир и календарь

Офлайн-матч: `python -m goaletto.application.match_cli --seed 7`.
Снимки и восстановление описаны в [match-engine.md](docs/match-engine.md).

[Мир и календарь](docs/world-initialization.md): офлайн-план первого комплекта,
возрастные лица, фиксированные составы и 547 планов (450 лиги + 97 кубка).
Точные старты и тики создаются только внутри 10-дневного горизонта.
Команда по умолчанию не подключается к БД:

```bash
goaletto-world --key initial --seed 0 --first-kickoff 2026-10-17T18:00:00Z \
  --output work/initial-world-preview.json
```

Запись в игровую БД требует явного `--apply`. Задачи нового мира исходно `held`;
активация сезона включает их и заполняет 10-дневный горизонт. После пересоздания
тестового мира 2026-10-15 календарь активирован. Операторские команды —
[match-runtime.md](docs/match-runtime.md), [four-day-calendar.md](docs/four-day-calendar.md).

## Предпросмотр портретов

**Текущий этап — проверка качества**, [A/B/C и выводы](docs/portrait-feasibility.md).
Прежний PNG-комбинатор ниже сохранён для воспроизведения и не принят для игры.

Банк содержит 1 000 портретов футболистов и 80 портретов персонала; это ёмкость
иллюстраций, не утверждённые 80 нанятых сотрудников. [Галерея](assets/portraits/whole-v2/index.html),
[сходные пары для проверки](assets/portraits/whole-v2/previews/closest-pairs.png).
Воспроизведение: `python scripts/pack_portrait_atlases.py --require-complete`,
затем `python scripts/review_whole_portraits.py`. Отчёты и предпросмотры —
assets/portraits/whole-v2/. Назначение identity подключено к коду игрового seed,
и проверено на PostgreSQL в локальной игровой БД;
Durable Telegram file cache реализован; реальные загрузки Telegram ещё не проверены.
Возрастное расширение добавляет 5 780 вариантов: 780 игроков со всеми семью группами,
20 с четырьмя футбольными группами и все 80 исходных сотрудников с тремя кадровыми группами.
Остаётся 73 листа / 1 460 вариантов; Imagegen сообщил сброс лимита 13 октября 2026 в 13:57 по Белграду.
[Галерея возраста](assets/portraits/whole-v2/lifecycle-v1/index.html), [контракт](docs/portrait-lifecycle.md).

```bash
python scripts/check_portrait_feasibility.py
python scripts/check_portrait_combinations.py
goaletto-portraits --seed player-01 --bank players --output work/player.svg
python scripts/render_portrait_preview.py
python -m goaletto.portraits.raster_cli --seed player-01 --bank players --age 24 \
  --kit-primary '#743D42' --kit-secondary '#F2E9D8' --output work/player.png \
  --recipe-output work/player-recipe.json
python scripts/render_raster_portrait_preview.py
```

Для иллюстрированных PNG достаточно обычных runtime dependencies (Pillow; без сети при рендере).
Для старых SVG→PNG-предпросмотров нужны python -m pip install '.[art]' и системная библиотека Cairo
(macOS: brew install cairo; Debian/Ubuntu: libcairo2). SVG-комбинатор работает без Cairo.
Концепты находятся в assets/concepts/, модульные пилоты — в assets/portraits/.
[Растровый эксперимент](docs/portrait-system.md) хранит рецепт лица отдельно от возраста и формы.
Статическая галерея открывается как обычный HTML-файл и не использует JavaScript.

## Структура

```text
src/goaletto/
  bot/              Telegram handlers и запуск polling
  domain/           чистые clock и league movement rules
  application/      контракт событий, создание человека; остальные use cases в плане
  infrastructure/   PostgreSQL schema, catalog publication, transactions, jobs
  worker/           отдельный обработчик фоновой работы
  portraits/        цельные PNG, перекраска одежды, исторические комбинаторы
migrations/         Alembic
tests/              unit и PostgreSQL integration
docs/               правила, архитектура, план, эксплуатация
assets/             цельные портреты, визуальные пробы и исторические части лиц
deploy/             настройки существующего HDC
```

## Размещение и права

[Инструкция для HDC](docs/deployment-hdc.md): Linux-контейнеры на существующем Windows
Docker host, PostgreSQL в закрытой сети и long polling. Закрытый HDC-запуск выполнен;
внешняя launch-приёмка остаётся открытой.

Деплой управляется локально: основной путь — сборка образа здесь и перенос на HDC;
альтернатива — commit/push здесь, pull и сборка на HDC. GitHub Actions выполняет проверки.
Для готового image используется deploy/compose.release.yaml поверх HDC override.

Copyright © 2026 anry88. Все права защищены. См. [LICENSE](LICENSE).
