# A · Добровольный отзыв в Telegram

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

Связь с Linear: пользовательская часть AND-78; автоматические кампании вне подпакета.
Ветка: codex/voluntary-feedback.
A–D приняты и опубликованы в main 63812358. Начать от свежей origin/main. В этой волне только
этому исполнителю разрешены новые feedback models/migration и узкая UI-регистрация.

Результат: добровольный отзыв в Telegram после первого результата, с durable
кнопочными ответами и необязательным комментарием. Сначала только internal/test
cohort. Реальным внешним пользователям ничего не рассылать и не включать для них.

Прочитай docs/analytics/measurement-plan.md (§Добровольная обратная связь),
contract-v1.md, event-catalog.md, storage.md, docs/closed-telegram-test.md,
docs/final-match-notification.md, docs/languages-and-names.md,
application/game_actions.py, infrastructure/analytics.py и текущие router/middleware.

Сделай:
- Вторичную кнопку «Оставить отзыв» на итоговом игровом экране после первого
  доступного результата и ручной /feedback. Основной CTA отчёт/следующий матч
  сохраняет приоритет. После команды проверяй действительного менеджера и
  internal/test eligibility, не расширяй Telegram allowlist.
- Один prompt на manager/campaign; общий cooldown минимум7дней. Повторный callback
  не даёт второй response/событие. Skip/opt-out terminal; недоступный бот исключён.
  Ручной вход не обещает, что сообщение автоматически было delivered.
- Причины из согласованного списка: непонятно что дальше; сложно настроить состав;
  долго ждать матч; неудобное время; неинтересная игра/результат; техническая проблема;
  другое. RU/EN, кнопки ≤64B с устойчивым scope, без техподробностей и changelog.
- Комментарий только после явного выбора, окно30минут, максимум500символов,
  можно пропустить. Маршрут не поглощает /команды и обычные сообщения вне окна,
  повтор доставки/update безопасен. Durable session в PostgreSQL, не только FSM.
- Свободный текст в отдельном restricted storage, не в JourneyEvent envelope,
  логах, exception text, labels или общем dashboard. Отдельный конфиг срока
  хранения комментариев: не придумывай политику хранения; задай явный обязательный
  параметр для включения сбора. Cleanup идемпотентен, без удаления игровых данных.
  Analytics opt-out уважать; gameplay receipts/cooldown не зависят от event deletion.
- Существующие feedback_answered/feedback_skipped/feedback_prompt_delivered
  использовать по их точному контракту. Feedback — явная аналитическая активность,
  но не продление шестимесячного владения. Accepted/sent/unknown не смешивать.

Автоматические prompts через24ч незавершённого входа и после пропущенной зрелой
матчевой возможности не реализовывать: они зависят от центральной opportunity
instrumentation. Не спрашивать «почему ушёл», если человек просто ждёт календарь.
Не добавлять запуск кампаний/sender в worker и не менять transport interface.
Ручной ответ через router: network вне DB transaction; delivery recording
отдельной идемпотентной транзакцией только после подтверждения transport.

Владение: новые application/feedback.py, bot/feedback_views.py,
bot/feedback_router.py, tests/unit/test_feedback.py,
tests/integration/test_feedback_pg.py, docs/feedback.md.
Разрешены только append-only feedback models в infrastructure/models.py,
одна migration с уникальной revision от актуального head, отдельные feedback config
fields в config.py, новые RU/EN строки, узкое подключение feedback router/команды/
вторичной кнопки в main.py, commands.py, live_views.py. Не изменять существующие
модели/контракты/event enums/game_actions ради feedback. Общие подключения приложи
также отдельным integration.patch для review, без параллельной Alembic ветви.

Приёмка: duplicate click/update, stale/foreign callback, opt-out/skip, cooldown,
точные границы30мин/500символов, команда внутри comment window не перехвачена,
restart/rollback, cleaning comments, не продлён ownership, text не в events/logs,
RU/EN. Unit + fake Telegram; PG tests передать Codex, не выполнять reset.
Передай полный рабочий подпакет и его конфигурацию; AND-78 целиком не закрывай.
