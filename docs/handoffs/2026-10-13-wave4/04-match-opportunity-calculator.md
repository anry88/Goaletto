# D · Чистый расчёт участия в первой и второй матчевой возможности

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

Связь с Linear: расчётная часть AND-77/79/82. SQL projection/emission/wiring
будут центральной интеграцией Codex после приёмки. Ветка: codex/match-opportunity-calculator.

Результат: typed pure calculator и hand-count acceptance cases двух недельных
KPI: участие в первом доступном матче и возврат ко второму. Не считать доставку
результата активностью и не выдавать синтетические числа за отчёт текущего мира.

Прочитай docs/analytics/measurement-plan.md, contract-v1.md (§Три KPI),
event-catalog.md, reports.md, acceptance-matrix.md, application/analytics.py,
application/analytics_reports.py и ownership/fixture models (только чтение).
Переиспользуй require_utc, match_activity_window, window_is_mature; не дублируй
текущие entry/Dk/source/delivery evaluator и не расширяй EVENT_SPECS.

Создай application/match_opportunities.py, immutable DTO входных проекций и
результата. Время report_at передаётся явно, без ORM/network/текущих часов.
Поля и предикаты eligibility должны быть явно документированы для будущего SQL:
manager/internal/analytics-enabled, ownership_id/club_id/started/ended,
fixture/type/status/actual_start/actual_finish/lineup_lock и eligible user effects
с fixture/ownership, origin, occurred_at/recorded_at и stable effect key.
Передавай явное состояние покрытия календаря/истории владения/events. Недостаточное
покрытие не равно доказанному отсутствию действий.

Сделай:
- Claim до или ровно на lineup lock допускает матч; claim после — следующий.
  Владение должно сохраняться до конца окна. Потерю владения показать отдельной
  группой, не считать продолжение новым владельцем. Ownership refs проверяются.
- Календарь и фактические времена различать. Перенос/пауза меняют окно
  [actual_start−24h, actual_finish+24h). Незавершённые/будущие кандидаты waiting;
  нельзя перескочить более раннего ещё не завершённого кандидата ради зрелого
  позднего матча. Нет календаря/покрытия — явные waiting/coverage/unknown.
- Боты/internal/opt-out/отменённые fixtures исключаются. Не изобретай смысл
  неизвестных финальных статусов (например abandoned без доказанного official
  result): явно unavailable/unsupported до центрального mapping, без ложного KPI.
  Поддерживаемый путь только лига; кубок/товарищеские не подмешивать.
- n первого: distinct managers с match_opened, match_report_opened либо
  match_command_accepted собственного fixture/ownership внутри зрелого окна.
  N первого: допустимые первые возможности с actual_finish+24h≤report_at.
- N второго: только участники первой возможности с закрывшейся второй.
  n второго: такие же явные действия во второй. Остальные — waiting/отдельные
  группы; не делить на всех стартовавших или на всех участников первой раньше срока.
- Automation/system, result_delivered, passive board updates и applied после
  accepted не активность. Повтор effect/update не увеличивает n. Late recorded_at
  после report_at не видим как успех; явно unknown_late. Boundary UTC [start,end),
  строгие проверки входа и reconciliation счётчиков; stable deterministic output.

Coverage model должен отличать полную историю от truncated/retention-limited.
Не объявляй первый наблюдаемый матч первой исторической возможностью, если более
ранний мог отсутствовать. Документируй, какие факты должен доказать SQL reader.
При противоречии исходных документов приведи точные ссылки/фрагменты и выбери
явный unresolved result вместо незаметного изменения правила.

Владение: src/goaletto/application/match_opportunities.py,
tests/unit/test_match_opportunities.py, docs/analytics/match-opportunities.md.
Не менять models/migrations/events/readers/CLI, не подключать к dashboards,
не создавать jobs/кампании. Codex позже подключит реальные проекции и SQL,
проверит parity на PostgreSQL; эта задача сама не даёт готовую instrumentation.

Приёмка hand-count fixtures: первое участие2/4 (остальным только delivery или
тишина); второе1/1 +1waiting; first fixture через7дней; claim ровно lock/после;
перенос/пауза; cancelled и pending predecessor; ownership interruption/reclaim;
wrong fixture/ownership; internal/opt-out; recorded_at late; exact24h boundaries;
нулевой зрелый N не превращается в0%; неполное покрытие. Минимум один кейс из
каждой ветки eligibility, ожидаемые n/N вручную, а не той же функцией.
