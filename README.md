# ⚽ Goaletto · Голетто

Футбольный менеджер целиком в Telegram: клуб, состав, тренировки, трансферы и live-матчи.
Python, сообщения и inline-кнопки; без Mini App и JavaScript.

**Состояние: начальный каркас, не готовая игра.** Доступны демонстрационное меню бота,
чистые правила времени и переходов между лигами, базовая миграция PostgreSQL, диагностический
worker и портретные комбинаторы: технический SVG и иллюстрированный PNG из прозрачных частей.
Регистрация, симуляция матчей, экономика и отправка
outbox-уведомлений пока не реализованы.

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
- Портреты: отдельные банки игроков и персонала. В библиотеке только небольшой пилот;
  сейчас мужские варианты, одежда в двух цветах клуба. Направление C — Dugout
  согласовано; production-каталог развивается отдельно.
- Пенсия: утверждённая шкала 5/10/20/35/55/80/100% в 32–38; заранее объявленный
  последний сезон запрещает сделки с футболистом. [Контракт](docs/player-lifecycle.md).

## Локальная разработка

Python 3.12+, PostgreSQL 17 (или совместимая проверенная версия).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
cp .env.example .env
docker compose up -d postgres
alembic upgrade head
goaletto-worker
```

В отдельном терминале задайте GOALETTO_BOT_TOKEN в .env и запустите goaletto-bot.
Для Windows PowerShell активация: .venv\Scripts\Activate.ps1.

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
pytest -q
```

PostgreSQL-проверки пропускаются без GOALETTO_TEST_DATABASE_URL.
Для полного прогона укажите отдельную тестовую БД, выполните миграции в ней и запустите pytest.
CI создаёт изолированный PostgreSQL и проверяет миграции, блокировки и повторное добавление задач.

## Предпросмотр портретов

```bash
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
Новый [растровый комбинатор](docs/portrait-system.md) хранит рецепт лица отдельно от возраста и формы.
Статическая галерея открывается как обычный HTML-файл и не использует JavaScript.

## Структура

```text
src/goaletto/
  bot/              Telegram handlers и запуск polling
  domain/           чистые clock и league movement rules
  application/      граница будущих игровых use cases
  infrastructure/   PostgreSQL models, transactions, jobs
  worker/           отдельный обработчик фоновой работы
  portraits/        детерминированные SVG/PNG-комбинаторы
migrations/         Alembic
tests/              unit и PostgreSQL integration
docs/               правила, архитектура, план, эксплуатация
assets/             визуальные пробы и малый набор частей лиц
deploy/             настройки существующего HDC
```

## Размещение и права

[Инструкция для HDC](docs/deployment-hdc.md): Linux-контейнеры на существующем Windows
Docker host, PostgreSQL в закрытой сети и long polling. Развёртывание на сервере ещё не выполнено.

Деплой управляется локально: основной путь — сборка образа здесь и перенос на HDC;
альтернатива — commit/push здесь, pull и сборка на HDC. GitHub Actions выполняет проверки.
Для готового image используется deploy/compose.release.yaml поверх HDC override.

Copyright © 2026 anry88. Все права защищены. См. [LICENSE](LICENSE).
