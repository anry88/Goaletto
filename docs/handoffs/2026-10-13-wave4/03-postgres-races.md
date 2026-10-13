# C · Недостающие гонки рынка и sender на PostgreSQL

Ты внешний разработчик Goaletto — Python-футбольного менеджера в Telegram.
Репозиторий: https://github.com/anry88/Goaletto (закрытый, лицензию не менять).
Работай только над указанным подпакетом и передай готовый код Codex для review.
Не запускай других агентов и не отправляй сообщения от имени пользователя.

Перед началом:
- Прочитай AGENTS.md, README.md, docs/game-rules.md, docs/match-clock.md,
  football-manager-bot-gdd.md и документы ниже. Архивный GDD v1 не источник правил.
- Получи актуальный origin/main и создай отдельную ветку codex/<имя-задания>.
  Запиши исходный SHA. Не продолжай старую агентскую/integration-ветку.
  Код на момент подготовки проверен на main 63812358; это справка,
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

Связь с Linear: конкурентная часть AND-71; не нагрузочный benchmark HDC.
Ветка: codex/postgres-race-acceptance.

Результат: воспроизводимые PG regression cases реальных транзакционных гонок,
которые ещё не покрыты. Не повторять existing gather tests ради количества.
Сначала составь таблицу existing test→invariant→missing interleaving.

Прочитай docs/transfer-auctions.md, docs/club-finances.md, docs/live-commands.md,
docs/outbox-delivery.md и src/application/{market,money,outbox_delivery}.py,
src/infrastructure/{jobs,locks}.py; пути считать относительно src/goaletto.
Изучи tests/integration/test_market.py, test_finances.py, test_agent_wave.py,
test_jobs.py, test_live_commands.py и tests/unit/test_outbox_delivery.py.

Целевые новые interleavings (проверь, какие ещё отсутствуют):
1. Новая ставка против запрета trades после persisted last_season. Флаг +
   prohibit_player_trades должны ставиться в одной транзакции под реальным
   player lock. Либо допустимая ранняя ставка затем refunded/cancelled, либо
   отказ поздней. Нет активного резерва отменённого аукциона; один refund.
2. Settlement против такого же запрета. Проверь обе сериализации: объявление
   первым блокирует продажу; продажа первой переносит игрока, последующее
   объявление относится к его актуальному клубу. Не изображай целый rollover.
3. Поздняя ставка с продлением против уже взятой close job: после ожидания locks
   перепроверяются ends_at и версия; игрок не уходит раньше нового дедлайна,
   актуальная future job не теряется, резерв учитывается один раз.
4. Sender lease истекла и взята другим sender; запоздалый finish старого sender
   не перезаписывает новый attempt/board/message_id. Симулируй unknown outcome
   между fake transport success и записью; не объявляй доказанным exactly-once
   Telegram send. Durable состояние должно честно сохранять unknown.

Используй отдельные AsyncSession/connection, реальную БД и locking приложения.
Синхронизируй порядок через asyncio.Event/контролируемый захват реального lock;
произвольный sleep не доказательство гонки. Таймаут ограниченный, отменённые tasks
await и sessions закрыты. Не monkeypatch БД/locking/бизнес-guards и не заменяй
целевой механизм fake repository. Telegram transport fake; network вне DB tx.

Assertions проверяют весь результат: актуальный клуб игрока, status аукциона,
балансы/сумму active reservations, ledger business keys/refunds, jobs и durable
attempt/lease/board. Rollback/fault injection не должен оставлять половину сделки.
Если нашёл product bug, сохрани строгое ожидание и предложи минимальный fix.patch;
не принимай текущий дефект за правило. Race с retirement не реализует новую политику.

Владение: tests/integration/test_postgres_race_acceptance.py,
docs/postgres-race-acceptance.md; при необходимости новый scoped helper
в tests/integration/race_helpers.py. Общий conftest/models/application не менять.
Тесты используют существующий sessions fixture, не создают новую DB/service.
Агент не запускает reset/PG-проверки: их выполнит Codex последовательно с backup
на единственной development DB. В отчёте PG честно указать not run, а не passed.
Запуск collection/ruff/mypy и безопасных unit допустим; standalone load CLI не нужен.

Приёмка Codex: реальные PG interleavings в обе стороны, bounded completion,
repeat чтобы выявить флаки только при реальном основании; restore исходного мира.
Не обещай подтверждённый HDC throughput/p95 по локальному regression suite.
