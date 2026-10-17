# Матчевые возможности: первое участие и возврат ко второму · AND-77/79/82 (расчёт)

Обновление 2026-10-18: [игровое подключение и PG-приёмка AND-75/76/79](../launch-measurement.md).
Описание прежней внешней поставки ниже — история; текущие SQL last-steps/recovery/
match-opportunities подключены к операторскому CLI. Технические latency и
автоматические опросы остаются отдельными задачами.

Статус: чистый типизированный калькулятор в `application/match_opportunities.py`,
hand-count приёмка в `tests/unit/test_match_opportunities.py`. SQL
projection/emission/wiring, реальные проекции и подключение к dashboards —
отдельная центральная интеграция Codex после приёмки. Эта задача сама не даёт
готовую instrumentation и не отчитывается за текущий мир синтетикой.

Границы: только `src/goaletto/application/match_opportunities.py`,
`tests/unit/test_match_opportunities.py`, этот документ. Models, миграции,
события, readers и CLI не менялись, jobs и кампании не создавались,
`EVENT_SPECS` не расширялся, entry/Dk/source/delivery оценщики не дублировались.
Переиспользуются `require_utc`, `match_activity_window`, `window_is_mature`.

## Определения

Все времена UTC, интервалы `[начало, конец)`, `T` — явный `report_at`.
Повторы дедуплицируются по стабильному `effect_key`
(`journey:v1:{operation_id}:{effect_number}`); `n/N` считают distinct managers.

- Возможность — матч лиги собственного клуба, `claimed_at <= lineup_lock`
  (claim ровно на lock допускает; claim после — следующий матч).
  Владение должно сохраняться до конца окна (`ended_at IS NULL` или
  `ended_at >= actual_finish+24h`). Потеря — отдельная группа `interrupted`,
  продолжение новым владельцем не засчитывается.
- Окно — по фактическим временам: `[actual_start−24h, actual_finish+24h)`.
  Календарное `scheduled_start` и фактические времена различаются;
  перенос и пауза сдвигают окно через `actual_*`. Незавершённые и будущие
  кандидаты — `waiting`. Более ранний незавершённый кандидат блокирует поздний
  зрелый: перескакивать нельзя.
- Только лига (`fixture_type="league"`). Кубок и товарищеские — отдельный
  маршрут, пропускаются при поиске (не блокируют). `cancelled` пропускается.
  `abandoned` и неизвестные финальные статусы/типы без доказанного официального
  результата — `unsupported`/`unavailable` до центрального mapping, без ложного KPI.
- `n` первого: distinct managers с `match_opened`, `match_report_opened` либо
  `match_command_accepted` (`origin=user`, `version=1`) своего
  fixture/ownership внутри зрелого окна. `N` первого: допустимые первые
  возможности с `actual_finish+24h <= T`.
- `N` второго: только участники первой возможности с закрывшейся второй.
  `n` второго: такие же явные действия во второй. Остальные —
  `waiting`/отдельные группы; делить на всех стартовавших или на всех
  участников первой раньше срока нельзя.
- Не активность: automation/system, `result_delivered`, пассивные board updates,
  `match_command_applied` и повтор effect/update. `recorded_at > T` — явно
  `unknown_late`, не успех; поздний `T` пересчитывает окно. `recorded_at` не
  подменяет `occurred_at`.
- Нулевой зрелый `N` даёт `rate=None`, никогда `0%`. Вывод детерминирован
  (сортировка по `manager_id.hex`, затем `lineup_lock/scheduled/fixture_id`).

## Входные проекции (immutable DTO)

- `ManagerProjection`: `manager_id`, `is_internal`, `analytics_enabled`,
  `is_bot`. Исключён (`excluded`) при `is_internal`, `not analytics_enabled`
  (opt-out/test/operator, tombstone) или `is_bot`. Боты, internal и opt-out
  никогда не входят в `N`.
- `OwnershipProjection`: `ownership_id`, `manager_id`, `club_id`,
  `claimed_at`, `ended_at|None`. refs эффектов проверяются против этих периодов;
  для fixture выбирается последний claim `<= lock` того же клуба.
- `FixtureProjection`: `fixture_id`, `club_id`, `fixture_type`, `status`,
  `scheduled_start` (календарь), `lineup_lock`, `actual_start|None`,
  `actual_finish|None`, `has_official_result` (несётся для будущего mapping,
  пока любой `abandoned` — `unsupported`).
- `UserEffectProjection`: `effect_key`, `manager_id`, `fixture_id`,
  `ownership_id`, `name`, `origin`, `occurred_at`, `recorded_at`, `version=1`.
- `CoverageState`: `calendar_complete`, `ownership_history_complete`,
  `events_complete`. Неполное покрытие не равно доказанному отсутствию действий:
  без полной истории первый наблюдаемый матч нельзя объявлять первой
  исторической возможностью (группа `coverage`, не ложный `N`).
- `report_at` передаётся явно; ORM, сеть и текущие часы запрещены.

## Выход

`MatchOpportunityReport`: `report_at`, `coverage_limited`,
`total_managers`, `excluded_managers`, `first`/`second` (`OpportunityCounts`:
`eligible_N`, `participated_n`, `waiting`, `interrupted`, `coverage`,
`unsupported`, `unavailable`, `no_calendar`, `unknown_late`, `rate`) и
`second_no_first` (не участники первой, вне знаменателя второй), плюс
`details` по каждому менеджеру (`first/second_fixture_id`, статусы,
`participated`, `unknown_late`). Счётчики строго сверяются: первая группа +
`excluded` = `total`; вторая группа + `second_no_first` = `total`;
`participated <= N`; `unknown_late <= N-participated`.

## Что должен доказать SQL reader (обязательства Codex)

- Менеджеры: `managers.is_internal`, отсутствие строк `analytics_exclusions`
  (internal/test/operator/opt-out, tombstone), bot-флаг; preview/demo эффекты
  покрытие не начинают.
- Владение: полные `ownership_periods` (`ownership_id/manager/club/claimed/ended`),
  без truncated/retention-пропусков; `ended` до конца окна — `interrupted`.
- Календарь: тип соревнования (лига против кубка/товарищеских), `status`,
  `scheduled` против `actual_start/finish`, `lineup_lock`, официальный результат
  для `abandoned`; отсутствующие `actual_*` у `finished` — `unavailable`.
- Эффекты: `journey_events` `version=1`, `origin=user`, имя из трёх допустимых,
  refs `fixture_id/ownership_id`, `occurred/recorded`, стабильный dedupe-ключ,
  видимость только при `recorded_at <= T`.
- Покрытие: `calendar_complete` (все туры/переносы периода на месте),
  `ownership_history_complete` (ранний claim не мог отсутствовать),
  `events_complete` (отсутствие эффекта — доказанное отсутствие, не retention-gap).

## Несоответствия исходников

Проверены `measurement-plan.md` (§Три показателя, окно и зрелость),
`contract-v1.md` (§Три KPI и зрелость, фрагменты «Возможность — завершённый
матч лиги… Claim после lock пропускает fixture» и «N… actual_finish+24h ≤ T»),
`event-catalog.md` (три имени участия — `user_activity=true`; `applied`,
`fixture_finished`, `result_delivered` — system), `reports.md` (as-of по
`recorded_at`, `unknown_late`, coverage), `acceptance-matrix.md` (G1: матчевые
когорты явно не реализованы), `application/analytics.py`
(`match_activity_window`, `window_is_mature`) и модели `OwnershipPeriod`
(`claimed_at/ended_at`) / `Fixture`
(`scheduled/lineup_lock/actual_*/status scheduled/live/paused/finished/
abandoned/cancelled`, без поля competition). Прямого противоречия правил окна,
зрелости и трёх имён участия не найдено. Зафиксированные пробелы (не смена
правил): у `fixtures` нет `fixture_type`/competition и флага официального
результата — поэтому кубок/товарищеские различаются только проекцией, а любой
`abandoned` остаётся `unsupported` до центрального mapping; выбран явный
`unsupported/unavailable` вместо додумывания смысла.

## Hand-count приёмка (ожидания вручную)

- Первое участие 2/4: отчёт и валидная команда считаются, только delivery и
  тишина — нет; повтор и applied не удваивают.
- Второе 1/1 + 1 waiting: делить на обоих участников первой раньше срока нельзя.
- Первый fixture через 7+ дней: незрелое окно — waiting, не пропуск.
- Claim ровно lock допускает; claim на секунду позже — следующий (`no_calendar`).
- Перенос/пауза: окно по `actual`, отчёт до `finish+24h` — waiting.
- Cancelled пропускается; pending-предшественник блокирует поздний зрелый.
- Потеря владения — `interrupted`; reclaim тем же клубом новым менеджером —
  его собственная первая возможность, не продолжение.
- Чужой fixture/ownership в эффекте игнорируется.
- Internal/opt-out/bot — `excluded` даже с действиями.
- `recorded_at` позже `T` — `unknown_late`; поздний `T` — успех.
- Границы `[start,end)`: ровно `start−24h` внутри, ровно `finish+24h` вне;
  зрелость при `T == finish+24h` включительна.
- Нулевой зрелый `N` — `rate=None`, не `0%`.
- Любой неполный флаг покрытия — зрелые уходят в `coverage`, не в ложный `N`.
- `abandoned` без mapping — `unsupported`; кубок пропускается к следующей лиге.
- System/automation/delivery/applied и чужие имена — не активность.
- `finished` без `actual_*` — `unavailable` и блокирует пропуск.
- Строгие входы (дубли, naive время, чужой ownership) — `ValueError`;
  порядок входа не меняет вывод.

## Ограничения

Синтетические числа — не отчёт текущего мира. Паритет с PostgreSQL, реальные
проекции, эмиссии AND-76/77 и SQL-ридер проверяет Codex на единственной Goaletto
DB последовательно после сохранения мира. Доставка результата активностью не
считается.
