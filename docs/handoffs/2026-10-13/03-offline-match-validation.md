# C · Офлайн-прогоны матчей, повторов и чувствительности

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

Связь с Linear: офлайн-часть AND-71.
Ветка: codex/offline-match-validation.

Результат: Python CLI для пакетных проверок матчей с воспроизводимым отчётом.
Без PostgreSQL, Telegram, загрузки PNG и изменения игрового баланса.

Прочитай docs/match-engine.md, docs/live-commands.md, docs/bot-clubs.md,
docs/matchday-squad.md, docs/stadium.md, docs/economy-season-validation.md;
application/match_simulation.py, domain/match_engine.py, match_codec.py,
match_types.py, match_commands.py и scripts/simulate_economy_season.py.
Последний уже проверяет 450 матчей и экономику: не дублируй его под новым именем.

Сделай scripts/validate_match_batches.py с явными --seed/--seeds/--matches,
--output и разумным ограничением размера. Один отчёт JSON и человекочитаемый Markdown,
при необходимости CSV. Фиксируй engine/policy version, seeds, количество матчей,
размер выборки и параметры сценариев; не используй datetime.now для симуляции.

Сценарии на существующем v2 engine:
- comparable baseline squads; отдельно пары со сменой домашнего поля;
- saved lineup без менеджера: никакой тактической ротации/тренировок/LLM;
- 11–20 выбранных игроков и различная скамейка, unavailable/injured player;
  клубный roster до25 не означает25 участников engine snapshot;
- scripted substitutions/tactics/mentality/instructions с лимитами и halftime;
  last-season player может играть, не превращай запрет торговли в запрет матча;
- checkpoint/restart на нескольких границах и replay одинаковых входов;
  сравни state hashes, события, счётчики и конечную phase;
- травмы, injury-only auto substitutions, удаления и abandoned как отдельные
  исходы. Нормальный законченный матч имеет30ticks, но не приписывай это abandoned.

Агрегаты: голы/ничьи/home-away, удары и в створ, карточки/травмы/автозамены,
использованные лимиты, доля abandoned, hashes/расхождения. По возможности
распределения/квантили и сравнение сценариев с одинаковыми seeds.
Разделяй correctness assertions и статистические наблюдения. Не требуй конкретной
частоты голов на маленькой выборке и не выдавай корреляцию за эффект игровой тактики.
Не меняй коэффициенты, retirement/aging, экономику или domain engine ради отчёта.

Владение файлами: scripts/validate_match_batches.py,
tests/unit/test_match_batch_validation.py, docs/match-batch-validation.md.
Не редактировать scripts/simulate_economy_season.py или domain/application код.
Если общего API не хватает — точный integration.patch, не второй engine.

Приёмка: быстрый deterministic smoke в unit suite; bounded CLI; одинаковый seed
даёт одинаковую содержательную часть отчёта; checkpoint/replay инварианты проверены;
средний пакет запускается офлайн и его время/размер измерены. Большие результаты
в work/, не в Git. Документируй, что это ещё не нагрузочный PG/production-прогон AND-71.
