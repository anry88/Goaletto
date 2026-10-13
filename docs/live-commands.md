# Live-команды · AND-53 · 2026-10-13

/match и кнопка «Матч» открывают текущий live или ближайший fixture. Замены,
тактика, менталитет и индивидуальные инструкции принимаются в PostgreSQL и
применяются worker на следующем ещё не начавшемся переходе. До tick commit UI
говорит «Принято». После применения статус исчезает из конца табло: само изменение
видно в хронологии событий и текущих значениях. Завершённые receipts остаются в БД.

## Admission и границы транзакции

Одно короткое application transaction; сеть после commit. Caller передаёт Actor
с настоящим Telegram update_id и operation_id. Общий allocation xact gate затем
Fixture → Match → Ownership → Club, player row locks по UUID. Runtime берёт этот
же gate через try-lock **до** Fixture; занятый worker defer без изменения абсолютного
deadline. Это также сериализует live mutation с expiry/settings и не даёт циклов
Manager/Ownership/Club. На текущем масштабе короткие tick transactions сериализуются
этим gate; дальнейшее масштабирование требует общего пересмотра lock contract.

Проверяется текущий владелец, matching club/fixture/match, активная phase и
window, deadline и ownership expiry после ожидания locks. Selected player/Person
перечитываются под lock: current club, active/last_season, player career, incoming
injury. UUID payloads канонизируются перед сравнением; hex-форма не обходит guard.

Closed payloadv1: substitution(outgoing_id,incoming_id), tactics(tactic),
mentality(mentality), instruction(player_id,instruction). AcceptedCommand unique
operation_key и реальный update/effect сохраняют retry. Conflicting scope/payload
отклоняется; повтор не создаёт ещё одну команду/эффект. Pending команды в стабильном
accepted_at/update/effect/id порядке проецируются чистой command function для
резервирования лимитов до tick. Принятая замена нельзя повторно зарезервировать.

При обработке worker перепроверяет durable ownership и current club owner;
прежнее/истёкшее владение отменяет команду. Applied row ссылается на уже вставленный
MatchTick; halftime applied не ссылается на несуществующий tick16.
Analytics accepted/applied атомарны с соответствующими эффектами; system applied
не продлевает session/ownership. Явное управление продлевает ownership.

## Окна и лимиты

5 замен, 3 ordinary windows (несколько замен одного tick — одно окно); перерыв
15:00–18:00 бесплатный. Команды halftime на target16 применяются при second-half
transition до 18:00; после него обычное окно tick16 до 19:00. 3 смены тактики,
менталитет без отдельного лимита, 3 смены индивидуальной инструкции. Неисполненное
принятое уже резервирует лимит. Удалённый/снятый игрок не возвращается.

Callbacks до64 bytes включают **полные** ownership_id, match_id и target/half flag.
Референсы игроков — индексы immutable snapshot roster этого match, не текущего
club squad. Новый матч/владение/переход делают кнопку устаревшей; mutation не
выполняется. Никакого игрового FSM или network transaction. Табло даёт fallback
/match; клуб/меню/помощь подключены. UI показывает текущую тактику и reserved counts.

## Версионирование индивидуальных инструкций

Новые runtime matches pin regulation-v2 / match-policy-v2 / state_version2.
V1 checkpoint schema, canonical JSON и golden hash сохранены буквально; чтение
и replay v1 поддержаны, инструкции для старого live match недоступны. Все новые
поля v2 закрыты codec и persisted в state; historical starting snapshot неизменен.
Нет переписывания старых матчей для включения новой механики.

Пять инструкций: long_shots, move_left, move_right, drop_back, runs_behind. Только
полевому игроку на поле; закрепляется за person и не переносится сменщику.
Исходные коэффициенты pin в policy: 1500bps (=15%) contribution modifier,
1000bps дополнительного порога wide для long_shots. Move-left/right перемещает
15% effective attacking contribution центра на нужный фланг; drop-back убирает
такую contribution из центра и добавляет15% effective defending в центр защиты;
runs-behind добавляет15% effective pace в центр атаки. Long-shots увеличивает
вес выбора shooter на15% и снижает точность (только open play, не set pieces).
Это начальная версия баланса, не статистически откалиброванные production числа.
После смены позиции/удаления сохранённая инструкция вне поля не влияет на sectors.

## Приёмка

Golden v1 hash ae4002fb…41 не меняется; v2 checkpoint/restart/replay воспроизводимы.
Проверены instruction events/ratings/limit и full UUID callback frames. На одной
рабочей Goaletto DB: retry→один applied, stale/expiry/pause/deadline, pending
reservation limits3/3, concurrent identical substitution, former-owner cancellation,
halftime free window без future FK, свежий injury guard с UUID hex, настоящий
Dispatcher с fake Telegram и status refresh. Full match/report/standings также
проверены в общей PG-сборке; сеть вне транзакции.

Polling, реальный Telegram/HDC smoke, экономика/rollover и recovery after long
downtime не входят в эту приёмку. AND-69 ещё не реализован диагностическим CLI.

Итоговая сборка: Ruff/format214, mypy85;375 unit/offline checks.
PG набор содержит24 сценария, включая return/expiry и settings. Прямой pytest CLI проверен
с явными tests packages: устранён CI-only ModuleNotFoundError общих helpers.
