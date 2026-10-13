# A · Диагностика актуальных jobs, recovery и денег

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
  не трогать HDC/соседние сервисы, не сбрасывать рабочую БД.
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

Связь с Linear: AND-70 и диагностическая часть AND-69, не полное закрытие этих задач.
Ветка: codex/diagnostics-current-subsystems.

Результат: существующий goaletto-diagnostics должен корректно объяснять состояние
новых подсистем и замечать доказуемые несогласованности, оставаясь строго read-only.
Не создавай второй диагностический CLI.

Прочитай docs/runtime-diagnostics-runbook.md, docs/live-recovery.md,
docs/club-finances.md, docs/stadium.md, docs/transfer-auctions.md, docs/training.md;
изучи infrastructure/jobs.py и models.py без изменения этих файлов.
В diagnostics.snapshot.SUPPORTED_JOB_KINDS сейчас отражены календарные виды.
Не расширяй этот набор вслепую: календарные проверки и все поддерживаемые worker jobs
имеют разный scope. Сначала сопоставь существующие проверки с реальными handlers.

Сделай:
- Корректную классификацию ownership.*, training.apply, finance.weekly,
  stadium.complete, market.close, market.cancel_player и system.ping.
  Не трактуй отсутствие fixture_id у некалендарного job как повреждение матча.
  Held future jobs/rollover и отложенный расчёт аукциона при активном матче —
  нормальные состояния при выполнении их контрактов, не ложная авария.
- Добавь bounded snapshot-проекции для проверки MatchRecovery: будущее окно,
  сохранённый checkpoint/hash и соответствующий job. Recovery переносит deadline;
  сравнивай с актуальными часами, не с первоначальным kickoff без paused_seconds.
- Добавь доказуемые проверки денег/рынка: reserved не превышает cash; при полном
  scope сумма active reservations согласуется с FinanceAccount; открытый лот с
  highest bid имеет reservation правильного клуба/суммы/статуса. Не объявляй
  рассогласование при усечённом наборе строк. Не сканируй всю историю ledger ради
  одной страницы и не называй open auction после deadline ошибкой автоматически:
  settlement может ждать завершения матча.
- Отчёт с конечными reason codes, уровнем severity, routing UUID и агрегатами.
  Сохрани существующие форматы и коды; новые коды документируй в runbook.
  Если данные не позволяют доказать инвариант, покажи ограничение покрытия.

Владение файлами: src/goaletto/diagnostics/*, tests/unit/test_runtime_diagnostics.py,
новые tests/integration/test_diagnostics_current_pg.py, docs/runtime-diagnostics-runbook.md.
Не менять models, миграции, jobs handlers, деньги, recovery и sender.
Не добавляй heartbeat-таблицу или обещание, что старый completed job доказывает
живой worker. Для новой persisted telemetry достаточно отдельного предложения Codex.

Приёмка: normal/late/recovered/paused/halftime; неизвестный kind; future held finance;
open/sold/cancelled auction; неверный reserve; truncation; одинаковые timestamps;
согласованный READ ONLY snapshot. Ноль DB writes/ownership effects/сетевых вызовов.
Pure fixtures дают точные findings. PG fixture написана для одного существующего
Goaletto target; выполнение оставь Codex. Это диагностика, не автоматический repair.
