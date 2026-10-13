# Runbook runtime-диагностики · codex/runtime-diagnostics

Read-only CLI: `goaletto-diagnostics`. Не меняет строки, не захватывает
работу, не продлевает ownership, не создаёт journey/user effects, не зовёт
view use cases с receipts. Mutating recovery (перепостановка jobs, ручной
reconcile outbox, backfill результатов) — отдельное предложение Codex и в
этом runbook не выполняется: ниже только проверка и направления исправлений.

## Быстрый старт

```bash
goaletto-diagnostics --list-codes
goaletto-diagnostics --late-threshold-seconds 180 --format human
goaletto-diagnostics --late-threshold-seconds 180 --format json
goaletto-diagnostics --season <SEASON_UUID> --format json
```

Порог задержки — явный параметр `--late-threshold-seconds` (default 180с):
pending job старше `due_at + порог` — просрочен, live-матч с
`next_due_at + порог < now` — опаздывает. Порог всегда печатается в отчёте.
Запас внешностей — `--appearance-low-threshold` (default 100 свободных
identity на банк). Покрытие ограничено `--limit` строк на scope (default
2000); при усечении отчёт ставит `DIAGNOSTICS_INCOMPLETE_TRUNCATED` и
подавляет выводы, требующие полноты, вместо объявления повреждения.

Вывод содержит только конечные reason codes, агрегаты и необходимые UUID
(job/fixture/match/outbox/season). Нет токенов, DSN, Telegram IDs/chat IDs,
message IDs, содержимого сообщений и raw exceptions. Ошибка подключения
печатает только имя класса исключения.

## Нормальные состояния (не аварии)

| Состояние | Коды (info) | Пояснение |
|---|---|---|
| Календарь не активирован | `JOBS_HELD_AWAITING_ACTIVATION`, `JOBS_HELD_ROLLOVER` | Мир создаёт jobs held; pending переводятся только явным `goaletto-runtime --apply`. Rollover всегда held |
| Перерыв матча | `MATCH_HALFTIME_WAIT_NORMAL` | Фаза `half_time`, transition второго тайма ещё не просрочен (нормальные 180с перерыва) |
| Пауза/перенос | `MATCH_PAUSED_OPERATOR`, `JOBS_DEFERRED_BY_PAUSE` | Явный операторский `pause_match`/`postpone_fixture`; worker пропускает paused fixtures штатно |
| Abandoned без результата | (нет finding) | Abandoned никогда не получает очки/3:0; отсутствие receipt — норма |
| Stale/superseded outbox fails | (нет finding, только агрегаты) | `skip_stale`/`skip_unavailable`/`superseded` — штатная гигиена sender |

Неполный диагностический запрос (`DIAGNOSTICS_INCOMPLETE_TRUNCATED`) —
не доказательство повреждения: увеличьте `--limit` и повторите.

## Findings: проверка и возможные исправления

### JOBS_PENDING_OVERDUE (alarm)
Просроченные pending jobs старше порога (паузы исключены).
Проверить: `... --format json`, сверить `job_ids`/`fixture_ids` и
`max_age_seconds`; убедиться, что worker запущен и календарь активирован,
что fixture не paused. Исправления: поднять/перезапустить worker (отдельная
операция, не диагностика); пропущенные окна календаря разбирать по
`docs/match-runtime.md` — тики не проигрывать залпом.

### JOBS_FAILED_INVALID_STATE / JOBS_FAILED_UNSUPPORTED_KIND / JOBS_FAILED_OTHER (alarm)
Терминальные fails. Проверить: класс `last_error` в агрегатах, kind jobs по
`job_ids`; `invalid_state` — handler отверг содержимое (сверить payload-ключи
с `job_refs` в логах worker по имени класса, без печати текста); unsupported —
вид без handler (часто новый kind после отката). Исправления: чинить продюсер/
версию календаря, затем перепостановка — предложением Codex, не здесь.

### JOBS_HELD_AFTER_ACTIVATION (warn)
Supported held jobs при active season. Проверить: `season_ids`, статус сезона;
сверить с `RUNTIME_KINDS`. Возможно, jobs добавлены после `--apply`.
Исправление: повторная явная активация — решением оператора, не диагностикой.

### MATCH_TICK_LATE (alarm)
Live-матч past `next_due_at + порог`. Проверить: связку с `JOBS_PENDING_OVERDUE`
по тем же `fixture_ids` (общий корень — вставший worker), фазу матча, отсутствие
паузы. Исправления: восстановление worker; догон тиков — только по процедуре
восстановления (Codex), не залпом.

### FIXTURE_LIVE_WITHOUT_MATCH (alarm)
Kickoff закоммичен, строки матча нет. Проверить: был ли lock_lineup (ищите job
`match.lock_lineup` для fixture в агрегатах jobs), не откатилась ли транзакция
лока. Исправление — операторское восстановление (Codex).

### OUTBOX_LEASE_EXPIRED (alarm)
Pending outbox с истёкшим lease. Проверить: `outbox_ids`, долю с unknown
latest attempt (указана в summary) — их сначала сверить вручную. Исправления:
sweeper/reclaim sender; при unknown — ручная сверка с Telegram, никогда не
ставить `sent` без подтверждения API.

### OUTBOX_BACKLOG_OVERDUE (warn)
Pending без активного lease старше порога. Часто следствие вставшего sender
или выключенного `GOALETTO_OUTBOX_ENABLED`. Проверить: запущен ли sender,
нет ли массового `rate_limited` (пауза sender). Не слать сообщения вручную.

### OUTBOX_UNKNOWN_NEEDS_RECONCILIATION (alarm)
Исчерпанные попытки с unknown outcome. Правило: unknown требует ручной
сверки, никогда не `sent`, историю не удалять. Проверить фактическую доставку
в чате средствами Telegram-клиента, затем решение — предложением Codex.

### OUTBOX_FAILED_TERMINAL (warn)
Терминальные fails с известным классом (`blocked`, `chat_unavailable`,
`invalid_request`, ...). Проверить класс по `outbox_ids`; `blocked` — ждать
нового `/start` (флаг `bot_available` чинит только свежий вход). Повторная
отправка — только через sender, не вручную.

### OUTBOX_BOARD_ORPHAN_LEASE (warn)
Активный lease stream без pending сообщений — блокирует поток табло.
Проверить: ключ stream, retry_at/board lease; убедиться, что покрытие полное
(нет `DIAGNOSTICS_INCOMPLETE_TRUNCATED`). Исправление — разблокировка lease
предложением Codex.

### FIXTURE_FINISHED_WITHOUT_RESULT (alarm)
Finished без immutable `MatchResult`. Проверить: фазу матча (должен быть
`full_time`), затем идемпотентный `match.finalize_result` — только
предложением Codex, не диагностикой.

### MATCH_FULLTIME_FIXTURE_NOT_FINISHED / FIXTURE_FINISHED_MATCH_NOT_FULLTIME (alarm)
Расхождение проекций fixture/match. Проверить обе стороны по UUID, историю
`match_stages`/`operation_attempts` read-only запросами. Чинить — Codex.

### TICK_GAP_OR_COUNT_MISMATCH (alarm)
`completed_tick` не равен последовательности хранимых тиков (пропуск/дубль).
Проверить номера тиков матча read-only; дубли completed tick runtime считает
no-op, пропуск — повреждение. Восстановление — Codex.

### APPEARANCE_STOCK_LOW (warn)
Свободных identity меньше порога. Проверить агрегаты `appearance_stock` по
банкам. Исправление единственное: опубликовать новые проверенные лица;
повторное использование identity между людьми запрещено.

### DIAGNOSTICS_INCOMPLETE_TRUNCATED (warn)
Scope упёрся в `--limit`. Действие: повторить с большим `--limit` (или
`--season` для сужения календаря). Выводы с требованием полноты подавлены.

## Ограничения

* Sender scopes (outbox/boards/attempts) всегда глобальные; `--season`
  сужает только календарь (jobs/fixtures/matches/ticks/results/seasons).
* `MATCH_TICK_LATE` и `JOBS_PENDING_OVERDUE` могут указывать на один корень;
  чинить worker, а не коды по отдельности.
* Диагностика не выполняет restore, deployment, polling и отправку сообщений
  и не заменяет проверки AND-72/AND-82 перед допуском людей.
* Интеграционная проверка на живой Goaletto DB — read-only прогоном CLI по
  согласованному времени; новые БД запрещены; read-only диагностика не выполняет reset. Если доступа к DB нет,
  интеграционный прогон передаётся Codex.

## Приёмка Codex · 2026-10-13

Тики загружаются по match_id, а не fixture_id; пустой scope не читает чужие ticks.
CLI использует server READ ONLY REPEATABLE READ snapshot. На PostgreSQL полный
30-tick матч не даёт ложного TICK_GAP; concurrent writer не меняет read snapshot,
а сервер отклоняет попытку записи. Параметр --limit применяется и к сезонам.
