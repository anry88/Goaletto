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
| Worker held по контракту | `JOBS_HELD_FUTURE_NORMAL` | Held `ownership.*`/`training.apply`/`finance.weekly`/`stadium.complete`/`market.close`/`market.cancel_player`/`system.ping` с будущим due: недельный понедельник, дата расширения, дедлайн аукциона. Отсутствие `fixture_id` у них — норма, не повреждение матча |
| Перерыв матча | `MATCH_HALFTIME_WAIT_NORMAL` | Фаза `half_time`, transition второго тайма ещё не просрочен (нормальные 180с перерыва) |
| Пауза/перенос | `MATCH_PAUSED_OPERATOR`, `JOBS_DEFERRED_BY_PAUSE` | Явный операторский `pause_match`/`postpone_fixture`; worker пропускает paused fixtures штатно |
| Восстановленное окно | `MATCH_RECOVERED_WINDOW_NORMAL` | Recovery перенёс deadline; сравнение идёт по `match.next_due_at`/`recovery.next_due_at` с учётом `paused_seconds`, не по исходному kickoff |
| Отложенный расчёт аукциона | `MARKET_CLOSE_DEFERRED_BY_MATCH` | Открытый лот past `ends_at`, но матч продавца/покупателя locked и не закончен: settlement ждёт финала штатно |
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
Календарные проверки не применяются к worker jobs: `finance.weekly` несёт
`club_id`, `training.apply` — `plan_id`, `market.close` — `auction_id`,
ownership — `ownership_id`; у них нет `fixture_id` по контракту.

### JOBS_UNKNOWN_KIND (warn)
Pending/held job с kind вне календарных, worker и `system.ping`/`season.rollover`.
Проверить `job_ids` и версию продюсера; failed с тем же корнем даёт
`JOBS_FAILED_UNSUPPORTED_KIND` (alarm). Исправление — чинить продюсер и
перепостановка предложением Codex, не здесь.

### MATCH_TICK_LATE (alarm)
Live-матч past `next_due_at + порог`. Проверить: связку с `JOBS_PENDING_OVERDUE`
по тем же `fixture_ids` (общий корень — вставший worker), фазу матча, отсутствие
паузы. После recovery сравнивать с перенесённым `next_due_at` (включая
`paused_seconds`), не с исходным kickoff. Исправления: восстановление worker;
догон тиков — только по процедуре восстановления (Codex), не залпом.

### MATCH_RECOVERED_WINDOW_NORMAL (info)
Live-матч с recovery и будущим перенесённым окном (`recovery.next_due_at`
и `match.next_due_at` в будущем в пределах порога). Норма: worker сохранил
checkpoint/hash и сдвинул `tick`/`second_half_start`/`finalize_result` jobs.
Проверить `match_ids`/`fixture_ids`; новый простой past окно даст обычный
`MATCH_TICK_LATE`.

### MATCH_RECOVERY_MISMATCH (alarm)
Recovery не согласуется с матчем при полном покрытии (`recoveries`, `matches`,
`jobs`, `fixtures`, ticks): пустой hash, `checkpoint_tick` вне 0–29 или впереди
`completed_tick`, `next_due_at <= previous_due_at/recovered_at`, чужой
`fixture_id`, live-матч без календарного job на перенесённое окно. Проверить
`match_ids`, checkpoint и `match_recoveries` read-only; чинить — Codex.

### FINANCE_RESERVE_EXCEEDS_CASH (alarm)
`FinanceAccount.reserved > clubs.budget` (cash). Доказывается одной строкой,
полнота не нужна. Проверить `club_ids`; сверка ledger не сканируется целиком.

### FINANCE_RESERVATION_MISMATCH (alarm)
Сумма активных `money_reservations` != `FinanceAccount.reserved` при полном
покрытии обоих scopes. При усечении (`finance_accounts`/`reservations` в
`DIAGNOSTICS_INCOMPLETE_TRUNCATED`) вывод подавлен: повторить с большим
`--limit`. Проверить `club_ids`.

### MARKET_RESERVATION_MISMATCH (alarm)
Открытый лот с `highest_bid_id` без активного резерва правильного клуба/суммы:
нет bid, чужой `auction_id`, резерв не `active`, клуб или сумма не совпадают.
Требует полного покрытия (`auctions`/`bids`/`reservations`), иначе подавлен.
Sold/expired/cancelled — терминальные, не проверяются. Проверить `auction_ids`.

### MARKET_CLOSE_JOB_MISSING (alarm)
Открытый лот без pending/held `market.close` job при полном покрытии jobs и
рынка. Проверить `auction_ids`; перепостановка — предложением Codex.

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

* Sender scopes (outbox/boards/attempts), финансы (`finance_accounts`,
  `reservations`) и рынок (`auctions`/`bids`) всегда глобальные; `--season`
  сужает только календарь (jobs/fixtures/matches/ticks/results/seasons/recoveries).
* `MATCH_TICK_LATE` и `JOBS_PENDING_OVERDUE` могут указывать на один корень;
  чинить worker, а не коды по отдельности.
* Открытый аукцион past `ends_at` сам по себе не авария: settlement ждёт финала
  матча (`MARKET_CLOSE_DEFERRED_BY_MATCH`). Ledger целиком не сканируется.
* Диагностика не выполняет restore, deployment, polling и отправку сообщений
  и не заменяет проверки AND-72/AND-82 перед допуском людей.
* Старый completed `system.ping` не доказывает живой worker; heartbeat-таблица
  не вводится этим изменением — только отдельное предложение Codex.
* Интеграционная проверка на живой Goaletto DB — read-only прогоном CLI по
  согласованному времени; новые БД запрещены; read-only диагностика не выполняет reset. Если доступа к DB нет,
  интеграционный прогон передаётся Codex.

## Приёмка Codex · 2026-10-13

Тики загружаются по match_id, а не fixture_id; пустой scope не читает чужие ticks.
CLI использует server READ ONLY REPEATABLE READ snapshot. На PostgreSQL полный
30-tick матч не даёт ложного TICK_GAP; concurrent writer не меняет read snapshot,
а сервер отклоняет попытку записи. Параметр --limit применяется и к сезонам.

## Восстановление доступного live transition

Worker с [live recovery](live-recovery.md) переносит просроченное окно, сохраняя
checkpoint. Диагностический CLI по-прежнему ничего не изменяет. Неподдерживаемые
или повреждённые состояния, отсутствующие jobs и неизвестная доставка требуют
отдельного разбора; это не разрешение на залповый catch-up или ручную отметку sent.

Приёмка Codex: recovery hash сравнивается с bounded checkpoint hash из MatchTick
или начального/halftime MatchStage. Проверяется точный следующий job/deadline,
а deferred auction использует именно highest_bid_id, не историческую ставку.

### Фильтр сезона и глобальный рынок

Финансовые счета, резервы и аукционы глобальны. При `--season` календарь и jobs
ограничены выбранным сезоном, поэтому проверки пропавшего `market.close` и
отложенного расчёта рынка недоступны: отчёт явно показывает
`MARKET_SCHEDULE_SCOPE_LIMITED`. Проверки денежных резервов продолжают работать.
Для проверки расписания рынка повторить диагностику без фильтра сезона.

Уточнение 2026-10-13: каталог лиц переиспользуется. `used/free` считает разные
иллюстрации (не количество людей); предупреждение сравнивает размер повторно
используемого каталога с порогом разнообразия, по умолчанию 14. Полностью
назначенные 1 000 лиц не блокируют создание новых людей.
