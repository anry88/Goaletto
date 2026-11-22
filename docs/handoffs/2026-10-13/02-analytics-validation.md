# B · Точные сценарии проверки аналитики

Ты внешний разработчик Goaletto — Python-футбольного менеджера в Telegram.
Репозиторий: https://github.com/anry88/Goaletto (закрытый, лицензию не менять).
Работай только над указанным подпакетом и передай готовый код Codex для review.
Не запускай других агентов и не отправляй сообщения от имени пользователя.

Перед началом:
- Прочитай AGENTS.md, README.md, docs/game-rules.md, docs/match-clock.md,
  football-manager-bot-gdd.md и документы ниже. Архивный GDD v1 не источник правил.
- Получи актуальный origin/main и создай отдельную ветку codex/<имя-задания>.
  Запиши исходный SHA. Не продолжай старую агентскую/integration-ветку.
  Игровой код на момент подготовки проверен на 6863b109; это справка,
  а не разрешение начинать новую ветку с устаревшего commit.
- Сначала проверь текущую реализацию: исторические статусы в документах/Linear
  могут отставать. Не реализуй повторно уже работающую функцию.

Общие границы:
- Только Python для приложения, Telegram, PostgreSQL. Без Mini App, JavaScript,
  нового публичного API, LLM в симуляции и новых игровых правил.
- Domain без aiogram/SQLAlchemy/сети/часов; время и seed передаются явно.
- Обычный матч: 30 тиков по 60 секунд, 3 игровых минуты на тик,
  перерыв 180 секунд, всего 33 реальные минуты плюс зафиксированные остановки.
- Изображения/портреты/каталоги/appearance IDs не менять и не генерировать.
- Не трогай файлы вне своего списка. Нужное изменение общей границы оформляй
  отдельным точным integration.patch и объясняй его. Исключения перечислены в задании.
- PostgreSQL владеет durable состоянием. Не подменяй его FSM/памятью процесса.
  Системные события и доставка не продлевают владение клубом.
- Не запускать настоящие polling/sender, не использовать Telegram-аккаунты,
  не трогать production/соседние сервисы, не сбрасывать рабочую БД.
  Новые локальные/CI тестовые БД и PostgreSQL services запрещены.
  PG-тесты можно написать; исполнение на единственной Goaletto DB выполняет Codex
  последовательно после сохранения мира. Отсутствие доступа не заменять SQLite.
- Не читать/копировать секреты в результат; не печатать токены, DSN, raw exceptions
  с credentials, тексты приватных сообщений. Используй fake transport/runner.
- Тексты игры RU/EN; без технических ID, версий, тиков и объявлений о твоих изменениях.

Сдача:
1. Commit/patch или PR, исходный SHA, список изменённых файлов.
2. Реальное поведение и границы подпакета; никаких обещаний готовности целого MVP.
3. Ruff check, ruff format --check, mypy и релевантный pytest. Для миграций —
   offline SQL upgrade/downgrade и пояснение схемы. Честно отдели passed от skipped.
4. Короткий воспроизводимый сценарий приёмки Codex и оставшиеся ограничения.
5. Не merge/push main и не закрывай Linear. Codex проведёт review, PG/native проверки,
   сведёт принятую работу в main и обновит задачи по фактически выполненному объёму.

## Задание

Связь с Linear: реализуемая сейчас часть AND-82.
Ветка: codex/analytics-acceptance-fixtures.

Результат: воспроизводимая матрица сценариев с рассчитанными вручную n/N,
которая проверяет текущие report API и SQL и выявляет смысловые ошибки.
Это отдельная проверка существующей аналитики, не новая instrumentation и не dashboard.

Прочитай docs/analytics/contract-v1.md, measurement-plan.md, reports.md, storage.md,
acquisition.md, event-catalog.md; application/analytics_reports.py,
infrastructure/analytics_reports_queries.py и существующие tests аналитики.
Не повторяй уже имеющиеся три простые PG-проверки.

Сделай таблицу named cases и проверки:
- direct/campaign/unknown attribution; исключённый internal manager и opt-out;
- зрелый/незрелый D1/D3/D7/D30, точные границы UTC [start,end), coverage_start
  и retention cutoff; первый матч позже семи дней не выдаётся за пропущенный матч;
- system ticks, auto-lineup, applied command и пассивная доставка не user retention;
  явный match/report open — только согласно текущему EventSpec;
- claim и manual lineup_saved одного ownership; другой период владения не
  достраивает готовность предыдущего; replay/rollback/retry не удваивают n;
- occurred_at/recorded_at и unknown_late; история as-of у journey отличается
  от текущего delivery snapshot: не обещай исторические outcomes доставки;
- delivery sent/edited/duplicate, retryable/blocked/stale, network started без
  подтверждения, crash между send и commit; unknown остаётся unknown;
- одинаковые даты, пустая выборка, нулевой зрелый N, удалённое mapping.

Для каждого case зафиксируй вход, observation T, ожидаемые numerator/denominator,
waiting/unknown и обоснование из контракта. Ожидания не вычисляй той же функцией,
которую тестируешь. Для поддерживаемых метрик сопоставь hand count, evaluate_* и
настоящий SQL reader. Недоступные feedback/opportunity/report метрики явно
помечай как ещё не реализованные; не создавай фиктивные эмиттеры ради зелёного теста.

Владение файлами: новые tests/unit/test_analytics_acceptance_matrix.py,
tests/integration/test_analytics_acceptance_pg.py, docs/analytics/acceptance-matrix.md.
При необходимости общий fixture helper — только внутри этих файлов.
Не менять tests/integration/conftest.py, models, collector, reports или SQL readers.
Если обнаружена ошибка продукта, приложи минимальный failing case и отдельный
fix.patch. Не меняй определения метрик и не ослабляй assertion.

Приёмка: понятные точные n/N; unit pass; PG-тесты collect и используют существующий
sessions fixture, запуск в сдаче честно помечен «ожидает Codex». Нет новых БД,
личных данных/комментариев в fixtures, реальных аккаунтов или writes вне test setup.
AND-82 целиком не закрывать: dashboard/access/feedback зависят от следующих частей.
