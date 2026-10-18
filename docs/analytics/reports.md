# Операторские отчёты аналитики · AND-79

Обновление 2026-10-18: [игровое подключение и PG-приёмка AND-75/76/79](../launch-measurement.md).
Описание прежней внешней поставки ниже — история; текущие SQL last-steps/recovery/
match-opportunities подключены к операторскому CLI. Добавлены
[технические latency](../runtime-observation.md), [внутренние опросы и сводка feedback](../feedback.md).
Полный [gate HDC](launch-gate.md) ещё требует dashboard/access.

Статус: Python report API/CLI и bounded PostgreSQL-запросы реализованы в
отдельном checkout `codex/analytics-reports` от `origin/main` `ba16344a`.
Общие `models`/Alembic/collector не изменены. Публичный API, Grafana
provisioning и новые события не добавлялись. Реальный сбор данных и
инструментирование AND-76/77 остаются за Codex.

Файлы:

* `src/goaletto/application/analytics_reports.py` — определения метрик,
  валидация, чистые synthetic-оценщики `evaluate_*` (зеркало SQL);
* `src/goaletto/infrastructure/analytics_reports_queries.py` — read-only SQL
  `entry_funnel`, `retention`, `delivery`;
* `src/goaletto/application/analytics_reports_cli.py` — операторский CLI
  (`goaletto-analytics-reports`), без записи состояния;
* `tests/unit/test_analytics_reports.py` — synthetic n/N без БД;
* `tests/integration/test_analytics_reports_pg.py` — runnable PG-проверки
  на единственной Goaletto dev DB (только по согласованному времени).

## Определения метрик

Все времена UTC, интервалы `[начало, конец)`, `T` — зафиксированное время
отчёта (`--at`). Повторные операции дедуплицируются: `n/N` считает distinct
manager/message, а не строки событий/попыток. `recorded_at` не подменяет
`occurred_at`: as-of отчёт видит только `recorded_at <= T`; поздние строки
показываются как `unknown_late` и пересчитывают окна при большем `T`.

### 1. Воронка входа: start → claim → ручное сохранение состава

* Когорта: `acquisition_first_touches` внешних менеджеров
  (`NOT is_internal`, нет `analytics_exclusions`), `started_at` в
  `[available_from, T]`.
* Зрелость: `started_at + 24h <= T` → `eligible_N`; иначе `waiting`.
  Незрелые никогда не подменяют знаменатель.
* `claimed_n`: существует `journey_events` `club_claimed`, `origin=user`,
  `version=1`, `occurred_at` в `[started_at, started_at+24h)`,
  `recorded_at <= T`.
* `ready_n`: существует пара `club_claimed` + `lineup_saved` (`origin=user`)
  того же `ownership_id` (сравнение `envelope->>'ownership_id'`),
  `saved.occurred_at >= claim.occurred_at`, оба в первых 24h,
  оба `recorded_at <= T`. Автосостав (`origin=system`) не даёт readiness.
* `unknown_claimed_late` / `unknown_ready_late`: occurred в окне, но
  `recorded_at > T` (станут `claimed`/`ready` при позднем `T`).
* Покрытие: `available_from = max(requested_from, coverage_started_at,
  T - retention_days)`; `coverage_limited` при клиппинге. Отсутствие старых
  данных — coverage gap, не нулевая конверсия.
* Пример: `T=12 Oct 12:00Z`, старты 11 Oct 12:00 ×3 (зрелые) + 11 Oct 13:00
  (waiting). A: claim+manual save → claimed+ready. B: claim+system save →
  claimed, не ready. C: без claim. Итог: `claimed 2/3`, `ready 1/3`;
  повторный save A не меняет `1/3`; internal/opt-out исключены.

### 2. D1/D3/D7/D30 по явным действиям менеджера

* Для `k ∈ {1,3,7,30}` окно менеджера
  `[started_at + k·24h, started_at + (k+1)·24h)`.
  `N` — только созревшие: `started_at + (k+1)·24h <= T`; остальные `waiting`.
* `n` — хотя бы один `journey_events` с `origin=user`,
  `user_activity=true`, `version=1`, `occurred_at` в окне,
  `recorded_at <= T`. Повторы не удваивают.
* Не retention: `action_rejected` (`user_activity=false`), все
  `system`/`automation` — автоматические матчи, отправка сообщений,
  `fixture_finished`, `result_delivered`, `match_command_applied`,
  пассивное табло. Feedback (`feedback_answered`) — явное действие и
  засчитывается, но не продлевает ownership (отдельный allowlist).
* `unknown_late`: occurred в окне, `recorded_at > T`.
* Границы: `D7` старта 11 Oct 12:00 — `[18 Oct 12:00, 19 Oct 12:00)`.
  Действие 18 Oct 11:59 — не D7; ровно 19 Oct 12:00 — уже вне окна.
  Отчёт 19 Oct 11:59 даёт `eligible 0, waiting 1` для D7.

### 3. Доставка: попытки и известные/неизвестные исходы

Источник: `outbox_messages` + `outbox_delivery_attempts`.
Окно: `due_at`/`started_at` в `[window_from, T]`.

* Сообщения `N=total`: `delivered_n` (`status=sent`),
  `failed_known` (`failed`, `last_error != 'unknown_outcome'`),
  `failed_unknown` (`failed`, `last_error='unknown_outcome'`),
  `pending_waiting` (`pending`). Сумма сходится к `N`.
* `messages_unknown`: не-`sent` с `last_error='unknown_outcome'` или хотя
  бы одной попыткой `outcome='unknown'`. Отсутствующий исход никогда не
  превращается в успех/неуспех.
* Попытки: разбивка по `outcome`, `attempts_unknown` (`outcome='unknown'`),
  `attempts_waiting` (`claimed`/`sending`, `finished_at IS NULL`).
  `sent`/`edited`/`duplicate` — известный успех (подтверждение Telegram API,
  не прочтение). `blocked`/`chat_unavailable`/`invalid_request`/
  `skip_stale`/`skip_unavailable` — известный терминальный неуспех.
  `rate_limited`/`transient` — повторяемые. `unknown` — требует сверки.
* `network_started_at` — закоммиченный preflight непосредственно перед
  транспортом, не доказательство получения Telegram. `sending` с
  `network_started_at`, но без `finished_at` — waiting, не delivered.
* Retry: несколько попыток одного `outbox_id` дают один message-исход;
  `attempts_total` считает попытки отдельно.

## Privacy и retention: разные правила

* Journey-отчёты (`entry_funnel`, `retention`): только агрегаты по
  псевдонимам (`analytics_managers`), без имён, Telegram ID, `command_id`,
  `ownership` refs, текстов. Internal/test/operator/opt-out исключены;
  opt-out каскадно стирает journey/sessions/mapping и оставляет tombstone.
  Хранение — `analytics_coverage.retention_days` (по умолчанию 180 дней);
  `cleanup_events` удаляет по `recorded_at`; истёкшие окна показывают
  `coverage_limited`/`available_from`, а не ноль. Экспортные агрегаты без
  индивидуальных refs — отдельно до 24 месяцев; комментарии — 30 дней
  (AND-78, здесь не имитируются).
* Operational delivery history: `outbox_delivery_attempts` независима от
  journey retention/opt-out (см. модель: «Operational history independent
  of journey retention/analytics opt-out»). Не исключаем internal/opt-out,
  не применяем journey-клиппинг; храним `outbox_id`/исходы для операций.
  Агрегаты CLI не выводят `chat_id`, токены, DSN или raw-ошибки Telegram.
* Доступы: операторский CLI — restricted read-транзакция; обычный экспорт —
  отдельно; реальные HDC-роли/GRANT — AND-82.

## CLI

Без `--apply` — preview без подключения к БД:

```bash
python -m goaletto.application.analytics_reports_cli entry-funnel \
  --cohort-from 2026-10-11T00:00:00+00:00 --at 2026-10-12T12:00:00+00:00
python -m goaletto.application.analytics_reports_cli retention \
  --cohort-from 2026-10-11T00:00:00+00:00 --at 2026-10-20T12:00:00+00:00
python -m goaletto.application.analytics_reports_cli delivery \
  --window-from 2026-10-10T00:00:00+00:00 --at 2026-10-12T12:00:00+00:00
```

С `--apply` те же команды читают настроенную game DB
(`goaletto-analytics-reports <subcommand> ... --apply` после reinstall).
Запись состояния отсутствует. Ошибки печатают только класс исключения.

## Ожидаемые synthetic-примеры

См. `tests/unit/test_analytics_reports.py`: ручные `n/N` выше совпадают с
чистыми оценщиками, которые зеркалят SQL. PG-раннер
`tests/integration/test_analytics_reports_pg.py` повторяет те же числа на
реальной схеме (запуск только по согласованному времени с
`GOALETTO_ALLOW_DEVELOPMENT_RESET=1`; фикстура сбрасывает единственную dev DB).

## Ограничения и недоступное

* Живой сбор AND-74 включён только через `start_coverage`; preview/demo
  эффекты не начинают покрытие. Без `analytics_coverage` отчёты отвечают
  `CoverageNotStarted` (unavailable), а не нулём.
* Календарь/возможности матчей (AND-77): `actual_start_at`/`actual_finish_at`,
  `lineup_lock_at`, смена владения до lock, переносы/паузы и второе окно —
  в этих трёх отчётах не считаются; зрелые матчевые когорты остаются за
  Codex (AND-76/77/79 full). Отчёт показывает `waiting`/coverage, а не ноль,
  когда возможности ещё не закрыты.
* Ошибки/recovery (`action_rejected` → успех за 30 минут, доставка подсказки)
  и добровольная обратная связь (AND-78) здесь не агрегируются; отзывы не
  имитировались.

## Исторические пробелы внешней поставки (до приёмки AND-76/77)

Поиск `user_effect`/`record_event` показывает, что collector готов, но
продуктовые эмиссии неполны:

1. `club_offer_delivered` (`system`, `outbox_id`) — нет эмиттера; воронка
   «предложение доставлено» недоступна, показываем только
   start→claim→save с coverage-примечанием.
2. `next_fixture_opened`, `match_opened`, `match_command_accepted`,
   `next_action_opened`, `help_delivered` — контракт есть, игровых вызовов
   вне частичных history/lineup view нет или они не покрывают live-маршрут;
   матчевое участие/retention второго матча нельзя посчитать до AND-77.
3. `fixture_started`/`fixture_finished`/`result_finalized` lifecycle —
   частично в `match_runtime`, но фактические `actual_*` календаря и
   `match_command_applied` корреляция через `command_id` требуют проверки
   Codex на реальной PG-схеме.
4. `experiment_exposed`, `feedback_*`, `bot_unavailable` — только delivery
   пишет `result_delivered`/`bot_unavailable`; опросы/эксперименты (AND-78/81)
   не инструментированы — намеренно не имитируем.
5. `club_ownership_ended` пишется только при expiry; добровольный/операторский
   разрыв и смена владельца в окне доступности матча нуждаются в явных
   тестах Codex, иначе матчевые N будут смещены.
6. `lineup_opened`/`club_opened` пишутся view-слоем, но preview-маршрут
   (`preview_club`) не должен продлевать срок — Codex должен подтвердить
   ownership-guard на PG перед использованием этих событий в KPI владения.

Просьба к Codex: подтвердить/добавить вышеперечисленные эмиссии в игровых
транзакциях (caller-owned, без коммита коллектора), затем прогнать
`tests/integration/test_analytics_reports_pg.py` на единственной dev DB и
проверить зрелые матчевые окна отдельно.

## Приёмка и границы текущей сборки · 2026-10-13

SQL проверен на единственной рабочей Goaletto DB. CLI читает server READ ONLY
REPEATABLE READ snapshot; concurrent worker не меняет знаменатели между SQL.
Системные события в PG fixtures не имеют пользовательского session_id и не
исправляются UPDATE после записи: история append-only.

Journey entry/retention honours recorded_at as-of. Delivery показывает **текущее
состояние** строк/попыток в одном snapshot с фильтром текущего due_at/started_at
по окну. Историческое восстановление outcome на --at недоступно: due_at/status
могли измениться после него; поле historical_outcomes_available=false явно это
указывает. Не интерпретировать этот отчёт как delivery outcome as-of прошлого
времени. Исходный due_at и все переходы статуса пока не инструментированы.
