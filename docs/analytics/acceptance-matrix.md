# Матрица приёмки аналитики · AND-82 (часть)

Обновление 2026-10-18: [игровое подключение и PG-приёмка AND-75/76/79](../launch-measurement.md).
Описание прежней внешней поставки ниже — история; текущие SQL last-steps/recovery/
match-opportunities подключены к операторскому CLI. Технические latency и
автоматические опросы остаются отдельными задачами.

Проверка существующей аналитики, а не новая instrumentation и не dashboard.
Источник ветки: `origin/main` `d3a5185b829c176d45e2a0893c4dd1c61b6a3dd3`;
рабочая ветка: `codex/analytics-acceptance-fixtures`.
Файлы: `tests/unit/test_analytics_acceptance_matrix.py`,
`tests/integration/test_analytics_acceptance_pg.py`, этот документ.
Общих границ (`models`, collector, reports, SQL readers,
`tests/integration/conftest.py`) не меняли. `fix.patch` не приложен:
новых смысловых ошибок продукта сверх описанных в `reports.md`
(отсутствующие эмиссии AND-76/77) матрица не выявила; при падении кейса
считайте его минимальным failing case.

Ожидания ниже — вручную рассчитанные `n/N`. Их нельзя вычислять той же
функцией, которую проверяют. Для поддерживаемых метрик сопоставлены три
стороны: hand count → `evaluate_*` (unit) → настоящий SQL reader (PG).
`T` — зафиксированное время отчёта, все интервалы `[start,end)` UTC.

## Поддержанные и отсутствующие метрики

Поддержаны: воронка входа `start → claim → ручной lineup_saved` (24ч),
`D1/D3/D7/D30` по явным действиям, delivery по `outbox_messages` +
`outbox_delivery_attempts`, attribution `direct/unknown/known`
(`source_funnel`). Явно **не реализованы** (не создавать фиктивные
эмиттеры ради зелёного теста): участие в первом доступном матче и возврат
ко второму (`match_opportunity`, `second_match_return`), агрегаты
добровольной обратной связи (`feedback_summary`), восстановление после
ошибок (`error_recovery`), участие в матче (`match_participation`).
Историческая граница поставки; теперь `g1` проверяет CLI preview трёх новых
отчётов без подключения, а `test_launch_measurement.py` — их реальные SQL и UI.

## Матрица named cases

Легенда: `N` — зрелый знаменатель, `n` — числитель, `W` — waiting
(незрелые), `U` — `unknown_late` (occurred в окне, `recorded_at > T`).

| Case | Вход | T | Ожидание (hand) | Обоснование |
|---|---|---|---|---|
| A1 · direct/unknown/known | first touches: direct×1, unknown×1, known×1 (кампания `community/launch`); internal и opt-out отдельно | 12 Oct 12:00Z | source rows: каждый kind `starts 1, eligible 1`; entry `starts 3` | `acquisition.md`: absent/empty→direct, известный код→known, прочее→unknown; internal/opt-out не получают attribution; `SOURCE_FUNNEL_SQL` группирует по kind/campaign |
| A2 · internal/opt-out исключены | 1 внешний + 1 internal + 1 excluded, у всех claim в окне | 12 Oct 12:00Z | entry `starts 1, eligible 1, claimed 1/1, ready 0/1` | contract-v1: внутренние исключены по operational flag; opt-out каскадно стёрт; SQL: `NOT m.is_internal`, `NOT EXISTS analytics_exclusions` |
| A3 · удалённое mapping | когорта пуста (mapping удалён/CASCADE) | 12 Oct 12:00Z | entry `0/0`, retention все `0/0`, delivery `0` | storage.md: opt-out оставляет tombstone, journey/sessions удалены CASCADE; пустая выборка — не нулевая конверсия |
| B1 · зрелость входа | старты ровно `T−24h` и `T−24h+1s` | 12 Oct 12:00Z | `starts 2, eligible 1, W 1` | contract: `started+24h ≤ T`; граница включительна |
| B2 · зрелость Dk | 1 старт 11 Oct 12:00 | 13 Oct 12:00Z → D1 зрелый; 19 Oct 11:59 → D7 ещё `W 1` | D1 `1/1`, D3/D7/D30 `W 1` | `retention_window`: окно k — `[start+k·24h, start+(k+1)·24h)`, `N` только при `start+(k+1)·24h ≤ T` |
| B3 · полуоткрытые границы | D7 = `[18 Oct 12:00, 19 Oct 12:00)`; действия 11:59 и ровно 19 Oct 12:00 | 19 Oct 12:00Z | `retained 0/1`; действие ровно 18 Oct 12:00 → `1/1` | contract-v1 пример D7; верхняя граница исключена |
| B4 · coverage_start | запрошен 11 Oct, покрытие с 12 Oct 00:00 | 12 Oct 12:00Z | `available_from 12 Oct 00:00`, `coverage_limited`, `starts 0` | reports.md: `available_from = max(requested, coverage, T−retention)`; отсутствие данных — gap, не ноль |
| B5 · retention cutoff | retention 90д, старты 01 Jul и 11 Oct | 12 Oct 12:00Z | выживает только свежий: `starts 1, eligible 1` | хранение journey 180д по умолчанию; истёкшие окна показывают clipping |
| B6 · первый матч позже 7 дней | `actual_start` = start+9д, `actual_finish` +33мин | `finish+1h` | окно `[start−24h, finish+24h)` незрелое → **waiting, не пропуск** | measurement-plan: отсутствие действий между матчами не доказывает уход; перенос/пауза — по actual times; текущая SQL-приёмка в test_launch_measurement.py |
| B7 · одинаковые даты | 2 менеджера, claim и save в один миг | 12 Oct 12:00Z | `claimed 2/2, ready 2/2` | `saved.occurred_at ≥ claim.occurred_at`; счёт distinct manager, не строк |
| C1 · не retention | D1-окно: `fixture_finished`, `result_delivered`, `match_command_applied`, `match_tick_committed`, system `lineup_saved`, `action_rejected` | start+2д | `retained 0/1, U 0`; +`club_opened` → `1/1` | event-catalog/contract: доставка, tick, applied и автоматика — не user activity; `action_rejected` (`user_activity=false`) не даёт retention |
| C2 · явные match/report open | `match_opened`, `match_report_opened`, `match_command_accepted` — `user_activity=true`; `match_command_applied` — system/false | start+2д | accepted в D1 → `1/1` | сверка строго по текущему `EventSpec`; accepted≠applied |
| C3 · одно владение | claim(ownA)+save(ownB) | 12 Oct 12:00Z | `claimed 1/1, ready 0/1`; чужой период не достраивает готовность | reports.md: пара claim+save того же `ownership_id`, `saved ≥ claim`, оба в 24ч |
| C4 · replay/retry без удвоения | 1 менеджер: claim×2 + save×2 одного ownership | 12 Oct 12:00Z | `claimed 1/1, ready 1/1` | dedupe `journey:v1:{operation}:{effect}`; `n/N` — distinct manager |
| D1 · occurred/recorded | claim occurred 13:00, recorded `T+1h` (+save так же) | T → `0/1, U 1/1`; `T+2h` → `1/1, U 0` | `recorded_at` не подменяет `occurred_at`; as-of видит только `recorded_at ≤ T`; late пересчитывает окна |
| D2 · late retention | D1-действие occurred в окне, recorded `T+5д` | start+2д | `retained 0, U 1` | то же правило для Dk |
| D3 · journey as-of ≠ delivery snapshot | journey SQL содержит `recorded_at ≤ :report_at`; delivery `historical_outcomes_available=false` | 12 Oct 12:00Z | journey — as-of; delivery — **текущий snapshot**, исторических outcomes нет | reports.md 2026-10-13: `due_at/status` могли измениться; не читать delivery как as-of прошлого |
| E1 · sent/edited/duplicate | 1 `sent`-сообщение, попытки sent+edited+duplicate | 12 Oct 12:00Z | `delivered 1/1`, `attempts 3`, `unknown 0, waiting 0` | подтверждение Telegram API (не прочтение); `sent/edited/duplicate` — известный успех |
| E2 · retryable/blocked/stale | 4 сообщения: blocked, chat_unavailable, skip_stale, unknown_outcome; попытки +rate_limited/transient | 12 Oct 12:00Z | `failed_known 3, failed_unknown 1, messages_unknown 1` | `rate_limited/transient` — повторяемые; `blocked/.../skip_*` — терминальные; unknown не превращается в успех/неуспех |
| E3 · network_started без подтверждения | попытка `sending` с `network_started_at`, `finished_at NULL` | 12 Oct 12:00Z | сообщение `pending`, `attempts_waiting 1`, `delivered 0` | preflight перед транспортом — не доказательство получения |
| E4 · crash между send и commit | исход `unknown`, `last_error unknown_outcome` | 12 Oct 12:00Z | `failed_unknown 1, unknown 1, delivered 0, failed_known 0` | `outcome_unknown` — до reconciliation не success; unknown остаётся unknown |
| E5 · retry не удваивает | transient→sent одного `outbox_id` | 12 Oct 12:00Z | сообщений `1`, `delivered 1`, попыток `2` | несколько попыток — один message-исход; `attempts_total` считает отдельно |
| F1 · пустая выборка | нет стартов/сообщений | 12 Oct 12:00Z | entry `0/0`, retention `0`, delivery `total 0, attempts 0` | валидаторы принимают нули; пусто ≠ провал |
| F2 · нулевой зрелый N | 2 старта моложе 24ч | 12 Oct 12:00Z | `starts 2, eligible 0, W 2, n 0` | незрелые остаются в waiting, никогда в N |
| F3 · валидаторы | подмена знаменателя (`ready 2` при `eligible 1`, `waiting 2` при `starts 1`) | — | `ValueError` | зрелые когорты нельзя подменять незрелыми/чужими числами |

PG-расширения (реальный SQL reader, отличные от трёх простых проверок):
`pg_attribution_*` (kinds + opt-out + campaign), `pg_same_ownership_*`
(чужой ownership + system-save + дубли), `pg_late_recorded_*` (два `T`),
`pg_retention_d7_*` (точные границы + system-шум), `pg_delivery_full_*`
(edited/duplicate/retryable/stale/in-flight, 5 сообщений / 8 попыток),
`pg_empty_zero_mature_and_removed_mapping` (0 → waiting → CASCADE 0).

## Приёмка Codex

```bash
ruff check tests/unit/test_analytics_acceptance_matrix.py \
  tests/integration/test_analytics_acceptance_pg.py
ruff format --check tests/unit/test_analytics_acceptance_matrix.py \
  tests/integration/test_analytics_acceptance_pg.py docs/analytics/acceptance-matrix.md
mypy
python -m pytest -q -m 'not integration' tests/unit/test_analytics_acceptance_matrix.py
python -m pytest -q --collect-only -m integration \
  tests/integration/test_analytics_acceptance_pg.py
GOALETTO_ALLOW_DEVELOPMENT_RESET=1 python -m pytest -q -m integration \
  tests/integration/test_analytics_acceptance_pg.py
```

Последняя команда — только Codex последовательно после сохранения мира,
на единственной Goaletto DB. В этой сдаче PG-исполнение честно помечено
**«ожидает Codex»**; новых БД, SQLite-подмен, реальных аккаунтов и записей
вне test setup нет. В fixtures нет имён, Telegram ID, payload, callback,
сообщений, исключений с секретами и текстов комментариев — только
синтетические UUID/время/kind/outcome.

## Оставшиеся ограничения

- Матчевые когорты (первая/вторая возможность, `actual_start/finish`,
  `lineup_lock`, смена владения, переносы) не считаются: нужен AND-76/77.
- Обратная связь, тексты причин ухода, опросы, error recovery — AND-78;
  намеренно не имитируются.
- Delivery — текущий snapshot; исторической реконструкции outcome на `--at`
  нет (`historical_outcomes_available=false`).
- AND-82 целиком не закрывать: dashboard/access/feedback зависят от
  следующих частей.
