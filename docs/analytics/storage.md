# Транзакционный сбор · AND-74

`infrastructure.analytics` сохраняет закрытый JourneyEvent в транзакции вызывающего
игрового use case; сам не commit/rollback, не вызывает сеть/экспорт. Откат игрового
действия откатывает запись успеха. `record_event` требует активную транзакцию;
`version/operation_id/effect_number` имеют UNIQUE в PostgreSQL. На retry изменённый
logical effect отклоняется, recorded_at/event_id могут отличаться. Идентификатор
операции должен закрепляться за update либо business stage, не за попыткой worker.

## Покрытие и идентичность

Миграция 0004 создаёт coverage/exclusion/session/journey таблицы и `journey_export`.
Она не начинает сбор и не делает исторический seed. Перед запуском игрового входа
вызывается `start_coverage(session, UTC_at)`; повтор не двигает начало покрытия.
Preview-бот не вызывает collector. Без coverage событие отклоняется явно.

`pseudonym_for` создаёт UUID-псевдоним зарегистрированного Manager до выдачи клуба.
Ссылка `analytics_managers → managers → telegram_id` restricted и не входит в
export view. Доступ к Telegram mapping нужен только игровому bot/worker.
`manager_started` сохраняется один раз; `first_game_start_at` не позволяет создать
новую launch cohort после retention cleanup.

`exclude_manager` принимает internal/test/operator/opt_out, блокирует manager,
сохраняет явный реестр и удаляет mapping. FK CASCADE удаляет journey и sessions.
Opt-out оставляет tombstone: последующие импорты/события не восстанавливают аналитику;
игровой manager/клуб/вымышленные футболисты не удаляются. Internal flag также исключает
запись. Операторские действия не маскируются как действия внешних игроков.

## Сессии

Manager row lock сериализует сбор, создание псевдонима и удаление аналитики.
Сессия создаётся/обновляется только allowlist успешных user-действий с gap <30 минут.
При ровно 30 минутах создаётся новая. Collector заменяет предложенный session UUID
на сохранённый; повтор logical effect проверяется до обновления сессии.
`action_rejected` прикрепляется к имеющейся сессии, а при её отсутствии имеет NULL;
не создаёт сессию и не продлевает её. System/automation запрещают session_id.
Продление владения — ответственность проверенного игрового use case по отдельному
ownership allowlist; collector сам не объявляет просмотр законным действием владельца.

## Хранение и доступ

Closed envelope содержит только contract fields/UUID/finite enums, без callback,
Telegram ID, имени, текста комментария или exception. DB дополнительно проверяет
allowlist имён/полей, timestamps, effect range, FK сессии/псевдонима и запрещает UPDATE
событий. DELETE разрешён для retention/opt-out. Изменение смысла — новая версия.

Default `journey_export` — security-barrier view только внешних псевдонимов;
не показывает envelope, operation/command/ownership refs и Telegram mapping.
Бот-клубы/системные события без человеческого псевдонима туда не входят. Они могут
остаться restricted для диагностики матча, но не являются участием человека.
PUBLIC не получает права. Выдать аналитическому read-only пользователю только
USAGE schema и SELECT journey_export; не grant tables/all schemas или write privileges.
Настройка реальных HDC ролей не выполнялась и должна проверяться перед привлечением.

`cleanup_events` использует persisted retention_days (по умолчанию 180), удаляет rows
с recorded_at раньше границы, пустые истёкшие sessions, acquisition touches
и истёкший first-touch; first_game_start_at не позволяет повтор launch cohort. Ровно на границе данные
сохраняются. Вызывать в отдельной короткой maintenance transaction через
`python -m goaletto.application.analytics_cleanup_cli --at <UTC ISO> --apply`.
Без `--apply` подключения нет; используется сохранённый retention.
Расписание на HDC остаётся частью развёртывания. [Gate/runbook](launch-gate.md).
Комментарии/опросы и их сроки — AND-78.
Внешний экспорт запускается отдельно и не блокирует игровую транзакцию.

## Проверка и границы

Unit SQL-recording выполняет collector и проверяет dedupe, конфликты, sessions,
internal/opt-out, отсутствие launch cohort reset, SQL lock/FK/cleanup; offline Alembic
сопоставляет таблицы/индексы. Это не эмуляция PostgreSQL и не доказательство гонок,
серверного rollback/GRANT/CASCADE. По указанию пользователя тестовые БД не создаются.
Production БД/HDC не менялись. UI-инструментирование — AND-76/77, отчёты — AND-79.
