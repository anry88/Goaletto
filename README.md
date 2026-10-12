# ⚽ Goaletto · Голетто

Футбольный менеджер целиком в Telegram: клуб, состав, тренировки, трансферы и live-матчи.
Python, сообщения и inline-кнопки; без Mini App и JavaScript.

**Состояние: собрана первая игровая вертикаль, полная игра ещё в разработке.**
Доступны Telegram-вход и получение клуба, карточки, предматчевый состав, история/отчёты и таблицы,
чистые правила времени и переходов между лигами, миграции основания PostgreSQL,
контракт и транзакционный collector аналитических событий, инициализация мира 5×10×20, двухкруговой календарь,
граница назначения внешностей, диагностический worker и портретные инструменты.
Пользователь выбрал цельные иллюстрации вместо
сборки деталей: первый комплект 50 × 20 требует 1 000 разных футбольных внешностей,
без повторов, и отдельный банк персонала. [Проверка подходов](docs/portrait-feasibility.md),
[библиотека цельных людей](assets/portraits/whole-v2/README.md), [вторая карьера](docs/staff-lifecycle.md).
Экспорт 256/96px, одежда перекрашивается локально в цвета клуба. Новый каталог на
визуальной проверке. Создана одна локальная рабочая БД Goaletto; PostgreSQL-проверки выполняются на ней.
Чистый [матчевый движок](docs/match-engine.md) симулирует 30 тиков, поддерживает
checkpoint/replay и события. Durable [live-worker](docs/match-runtime.md) реализован, но календарь ещё не активирован.
Очки и таблицы начисляются атомарно с финальным тиком. Backend регистрации/выдачи клуба/6-месячного владения реализован:
[club-entry.md](docs/club-entry.md). Игровой `/start` и кнопки подключены при `GOALETTO_GAME_ENABLED=true`.
Live-команды менеджера, rollover и экономика ещё в разработке. [Outbox sender](docs/outbox-delivery.md) подключён отдельным
циклом worker; по умолчанию выключен (`GOALETTO_OUTBOX_ENABLED=false`).

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
- Места 1–2 поднимаются, 9–10 опускаются в соседнюю лигу внутри комплекта.
  Из первой лиги нет повышения, из пятой нет вылета; стыковых нет.
- Новичок получает только свободный бот-клуб пятой лиги.
  Если свободных мест в нижних лигах нет, создаётся новый независимый комплект 5×10.
- Бот-клубы: близкие базовые составы, без тренировок и нейросетей; замены только
  травмированных, пополнение после пенсии из молодёжки в межсезонье.
- Владение сохраняется шесть календарных месяцев с последнего игрового действия;
  периодические напоминания, затем освобождение. [Правила](docs/bot-clubs.md).
- Портреты: отдельные мужские банки игроков и персонала, одежда в двух цветах клуба.
  Направление C — Dugout согласовано; цельный каталог проходит визуальную проверку,
  каждому человеку своя внешность.
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
на PostgreSQL с fake Telegram; реальные отправки и polling не запускались.
Локальная БД использует постоянный volume и порт 5433; 5432 занят другим проектом.
Для игры включите `GOALETTO_GAME_ENABLED=true`; календарь активируется отдельно.

Весь стек локально:
1. В .env задайте URL с хостом **postgres**, а не localhost:
   postgresql+asyncpg://goaletto:YOUR_PASSWORD@postgres:5432/goaletto.
2. Установите такой же POSTGRES_PASSWORD и собственный токен.
3. Выполните docker compose up -d --build.
Миграция должна успешно завершиться до старта bot и worker.
Не запускайте одновременно локальный polling и контейнер bot с одним токеном.

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
уникальные возрастные лица, фиксированные составы, 450 матчей и сроки тиков.
Команда по умолчанию не подключается к БД:

```bash
goaletto-world --key initial --seed 0 --first-kickoff 2026-10-17T18:00:00Z \
  --output work/initial-world-preview.json
```

Запись в игровую БД требует явного `--apply`. Основной локальный мир подготовлен;
календарные jobs пока held.
Все календарные задачи исходно `held`. Явная активация supported jobs описана
в [match-runtime.md](docs/match-runtime.md); основной календарь ещё не активирован.

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
Docker host, PostgreSQL в закрытой сети и long polling. Развёртывание на сервере ещё не выполнено.

Деплой управляется локально: основной путь — сборка образа здесь и перенос на HDC;
альтернатива — commit/push здесь, pull и сборка на HDC. GitHub Actions выполняет проверки.
Для готового image используется deploy/compose.release.yaml поверх HDC override.

Copyright © 2026 anry88. Все права защищены. См. [LICENSE](LICENSE).
