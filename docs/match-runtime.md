# Долговечный regulation runtime · AND-52

`application.match_runtime` выполняет календарные jobs в caller-owned PostgreSQL
transaction. Worker берет due pending Job через FOR UPDATE SKIP LOCKED; fixture
NO KEY UPDATE SKIP LOCKED сериализует её матч, оставаясь совместимым с FK diagnostic
attempts. Нет Telegram/network внутри транзакции. Domain по-прежнему pure.

## Активация и снимки

Мир создаёт все calendar jobs held. `goaletto-runtime --season UUID` показывает
поддерживаемые kinds без БД/.env. Только явный `--apply` включает coverage и переводит
supported jobs этого сезона в pending. Season rollover остаётся held (AND-57),
sender ещё не реализован (AND-68). На этом этапе `--apply` не запускался.

Поддерживаются season.open, match.remind/lock_lineup/start/tick/second_half_start.
Opening делает scheduled season active; UNIQUE не позволяет два активных сезона
одного комплекта. Reminder после уже прошедшего kickoff не отправляется.
Lock загружает фиксированную XI/bench, player/person/appearance metadata и skills,
form/morale/availability. При травме стартового игрока — здоровый main-position,
потом alternative reserve в сохранённом порядке; без оптимизации OVR.
Недостаток подходящих/здоровых футболистов даёт диагностическую ошибку, не выдуманный состав.

Начальная общая модель: club.stadium_capacity=5 000, supporters=2 500. На lock
первый простой attendance model берёт `min(supporters, capacity)`: все готовые к матчу
стартовые supporters считаются пришедшими. Это приближение первоначального спроса,
не экономический прогноз. Спрос по лиге/позиции/серии/цене и рост fanbase — AND-63.
Attendance/capacity входят в immutable starting snapshot; пустые трибуны дают ноль.
Seed стабилен из season seed/fixture UUID. DB trigger запрещает менять seed,
engine version, starting snapshot и fixture identity уже созданного Match.

## Время и восстановление

Start записывает фактический первый свисток actual_start_at. Если он задержался,
future job deadlines сдвигаются один раз относительно планового kickoff. Первый
тик через60с, 15-й через900с, second-half transition через1080с, 16-й через1140с,
30-й через1980с. До kickoff due ticks ждут его commit.

Срок каждого следующего перехода вычисляется от actual_start_at плюс явные паузы,
никогда от времени завершения предыдущего worker. Задержавшиеся тики выполняются
по порядку из сохранённого checkpoint; дубль completed tick ничего не меняет.
Проверяются engine/state version, UUID/seed/clubs, tick/phase и score projections.
Нельзя перескочить predecessor/halftime. Tick receipt сохраняет due_at отдельно от
committed_at, позволяя измерять lateness без подмены настоящего времени.

`pause_match` сохраняет paused_at, fixture/match projection paused, сам JSON pure
state не меняется. Worker не выбирает paused fixtures. Resume суммирует pause seconds,
сдвигает только оставшиеся deadlines и восстанавливает прежнюю phase; повтор безопасен.
Дробная пауза округляется вверх до секунды. Это явные operator use cases, не
автоматическое решение о повреждённых матчах. Долгий простой рабочего live
transition восстанавливает окно по [live-recovery.md](live-recovery.md).
`postpone_fixture` до lineup lock сдвигает schedule/lock/jobs в одной транзакции;
после lock отказывает, не стирает immutable snapshot. actual dates сохраняются отдельно.

## Команды и атомарность

AcceptedCommand rows выбираются в стабильном accepted_at/update/effect/id порядке.
Закрытые payload v1: substitution outgoing/incoming UUID, tactics enum, mentality enum.
Просроченные/unsupported/domain rejected команды cancelled; применённые applied.
Зафиксированные в перерыве команды применяются при старте второго тайма без окна;
принятые после его deadline остаются до tick16 и расходуют обычное окно.

Regulation MatchTick вставляется/flush до applied-command UPDATE с FK на этот тик.
Halftime application хранит applied_at и stage, applied_tick_number=NULL: tick16 ещё
не существует. Command-applied analytics содержит command_id/fixture_id, origin=system,
без user session. При завершении остающиеся принятые команды cancelled.
Telegram owner/deadline admission/UI и индивидуальные инструкции подключены:
[live-commands.md](live-commands.md). Новые matches regulation-v2/state_version2;
старые v1 checkpoint и replay сохранены.

Checkpoint/projections, неизменяемые tick/stage events/hash, accepted effects,
journey lifecycle, outbox и отметка Job done коммитятся вместе. Это единственная
игровая transaction; DB/serialization failures откатывают её. Domain NotReady
откатывает savepoint, job остается pending с прежним deadline и попыткой deferred;
invalid state/unsupported kind дают failed с конечным failure_class. Retry после
исправления — операторская задача; runtime не скрывает повреждённое состояние.
OperationAttempt хранит job/fixture/stage/retry/due/start/finish/outcome, без exception
text/секретов. Успешный job attempt и lifecycle используют общий operation UUID.

## Завершение и уведомления

Full-time записывает actual_finish_at и status finished, abandoned — отдельный status
с текущим счётом поля и временем остановки. Не присуждаются 3:0, очки или победитель
abandoned. Tick может закончиться внутри его трёх игровых минут; SQL допускает это.
Все будущие tick/halftime jobs закрываются без повторной симуляции.
Начисление очков/receipt/таблица AND-56 подключены атомарно к финальному тику:
[match-results.md](match-results.md). Telegram navigation пока в плане.
Сезонные переходы — AND-57, ещё не подключены.

Активные владельцы получают durable outbox requests reminder/board/result. Это
постановка, не доставка: result_delivered не записывается до Telegram accepted.
Бот-клубы не получают outbox и не образуют human activity. Sender/live-message edit,
subscription/coalescing/rate limits/reconciliation — AND-68; сейчас сеть не вызывается.

## Проверка и ограничения

Unit runs с supplied ORM objects/SQL-recording: все30 тиков/halftime и точный финальный
hash, duplicate/restart, early/out-of-order deferral, delayed kickoff vs tick lateness,
pause/resume/postpone, applied tick FK order, halftime commands, late command cancellation,
abandoned, outbox вместо delivery, controlled activation, worker savepoint/attempt outcomes.
Offline upgrade/downgrade SQL/CREATE/index/ALTER checks; real PostgreSQL locks/rollback/
triggers/competition не проверены. Тестовые БД/контейнеры по указанию пользователя
не создаются. Не запускались game seed --apply, runtime --apply, polling или HDC deployment.

## Общий gate · 2026-10-13

Runtime берёт short allocation try-lock до Fixture/Match для сериализации с
ownership expiry/admission/settings. Busy gate даёт NotReady/deferred и не
сдвигает absolute deadlines. Pending owner guards перепроверяются при apply.
