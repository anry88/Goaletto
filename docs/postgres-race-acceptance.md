# PostgreSQL race acceptance · AND-71 (конкурентная часть)

Обновление 2026-10-18: сценарий поздней ставки снова проверяет продление внутри
действующего трансферного окна. После перехода на четырёхдневный календарь старая
дата NOW+47ч попала уже после закрытия окна и проверяла отказ вместо гонки.
Fixture теперь начинает этот лот на сутки раньше и явно проверяет открытое окно;
ожидания блокировок, продления, единственного резерва и rollback сохранены.
Настоящая нагрузка и измерения — [launch-load.md](launch-load.md).

Ветка: `codex/postgres-race-acceptance`.
Исходный `origin/main`: `555f363704cde0b89571394ac3001ae98261e83b`.

Регрессионные кейсы реальных транзакционных гонок, которых не хватало.
Не повторяют существующие gather-тесты ради количества. PG-прогон выполняет
Codex последовательно после бэкапа единственной development DB; агент PG не запускал.

## Существующие тесты → инвариант → недостающий интерливинг

| Существующий тест | Проверяемый инвариант | Чего не хватало |
|---|---|---|
| `test_market.py::test_parallel_bids_...` (gather двух ставок) | Параллельные ставки сериализуются, побеждает одна; self-raise заменяет резерв; retry идемпотентен; трансфер атомарен | Ставка против persisted `last_season` с флагом и `prohibit_player_trades` в одной транзакции под player lock; продление дедлайна против уже взятой close job с перепроверкой `ends_at`/версии после ожидания locks |
| `test_market.py::test_last_season_flag_...` (последовательно) | Флаг → `prohibit` отменяет один раз, refund один раз, листинг блокируется | Конкурентная новая ставка против запрета: ранняя допустимая затем refunded/cancelled либо отказ поздней; нет активного резерва отменённого аукциона |
| `test_market.py::test_late_bid_extends_...` (последовательно) | Поздняя ставка продлевает один раз; нехватка денег; withdraw после ставки запрещён; expiry отменяет | Поздняя ставка с продлением против уже взятой close job: игрок не уходит раньше нового дедлайна, future job не теряется, резерв учтён один раз |
| `test_market.py::test_busy_match_defers_...` | Занятый матч откладывает settlement; fault откатывает всю сделку | Settlement против запрета в обе сериализации: объявление первым блокирует продажу; продажа первой переносит игрока и последующее объявление относится к его актуальному клубу |
| `test_market.py::test_pending_purchases_...` + `test_finances.py::test_concurrent_reserves_...` | Параллельные резервы/close — settle один раз; pending покупки держат слоты | Те же деньги, но в рыночном контексте с продлением и отменой: один refund/release, сумма active reservations сходится с `FinanceAccount.reserved`, ledger business keys без дублей |
| `test_jobs.py` (SKIP LOCKED, rollback) | Воркеры пропускают занятый job; rollback сохраняет pending | `market.close` с продлением: после ожидания locks перепроверяются `ends_at` и версия, актуальная future job не теряется |
| `test_agent_wave.py::test_outbox_parallel_stream_claim_and_edit` | Параллельный claim сериализуется board lease; edit переиспользует message_id | Lease истекла и взята другим sender; запоздалый finish старого не перезаписывает новый attempt/board/message_id; unknown между fake transport success и записью честно сохраняется |
| `tests/unit/test_outbox_delivery.py` (fake Store) | Машина состояний, backoff, sweeper, границы lease, unknown vs transient | Тот же sender-контракт, но на реальной PG с отдельными `AsyncSession`, реальным locking приложения и fake transport вне DB tx |

## Новые кейсы

Файл: `tests/integration/test_postgres_race_acceptance.py`.
Хелпер: `tests/integration/race_helpers.py` (только ожидание lock-state и bounded gather).

1. `test_bid_vs_prohibit_early_bid_then_cancelled_refunded_once` — ставка держит
   allocation + auction, анонс ждёт и затем в одной транзакции ставит флаг под
   player lock и вызывает `prohibit_player_trades`. Итог: аукцион cancelled
   `last_season`, игрок у продавца, активный резерв 0, один `release`, ни одного
   `settle`, балансы без изменений, close job done, durable `market.cancel_player`
   существует, повторный prohibit — no-op, receipt нет.
2. `test_bid_vs_prohibit_late_bid_refused_no_reserve` — анонс держит locks первым,
   поздняя ставка получает `Auction closed` без создания резерва и bid-строки.
3. `test_settlement_vs_prohibit_announce_first_blocks_sale` — при ведущей ставке
   анонс первым отменяет с refund; close сначала получает `NotReady` (try-lock),
   затем после анонса возвращает `False` без продажи. Игрок у продавца, receipt нет.
4. `test_settlement_vs_prohibit_sale_first_moves_flag_follows_buyer` — продажа
   первой переносит игрока; последующий флаг ставится на составе покупателя,
   `prohibit` возвращает 0 и не трогает sold-аукцион. Receipt один, резерв settled,
   бюджеты продавец/покупатель moved, статус покупателя `last_season`.
5. `test_late_bid_extension_vs_taken_close_job_defers_with_single_reserve` —
   поздняя ставка за час до конца продлевает на 30 минут, пока close ждёт locks.
   Close сначала defer (`NotReady` либо `True`), затем после коммита ставки видит
   новый `ends_at` и возвращает `True` с `job.due_at` на новый дедлайн. Игрок
   не уходит раньше, future job pending не потеряна, резерв ровно один, receipt
   нет. Дополнительно aborted defer откатывается без половины сделки.
6. `test_sender_lease_takeover_old_finish_preserves_unknown` — claim + preflight
   старого sender, fake transport success вне tx (id 999) без finish (crash),
   advance часов за lease, claim нового sender (старый sending → `unknown`,
   новый `claimed`, board lease у нового). Запоздалый finish старого с 999 —
   `LeaseLost` без перезаписи. Новый sender завершает 777 `sent`; старый
   `unknown` сохранён. Exactly-once Telegram send не объявляется: 999 мог
   реально уйти, дубликат при retry возможен.

Проверки в каждом рыночном кейсе: актуальный клуб игрока, статус аукциона,
балансы и сумма active reservations против `FinanceAccount.reserved`, ledger
business keys (`reserve`/`release`/`settle`) и refunds, jobs. В sender-кейсе:
durable attempt/lease/board и `telegram_message_id`. Rollback не оставляет
половину сделки. Новой политики retirement нет; целый rollover не изображается.

## Синхронизация и границы

- Отдельные `AsyncSession`/connection, реальная БД и locking приложения
  (`allocation_lock`, `FOR UPDATE`, `pg_try_advisory_xact_lock`, `NOWAIT`-проверки).
- Порядок через `asyncio.Event` и контролируемый захват реального lock;
  произвольный sleep доказательством не является (паузы в хелпере лишь
  от busy loop, доказательство — lock-state).
- Таймауты ограниченные (`wait_for` 10с, gather 15с), отменённые tasks await,
  sessions закрыты контекстами. `conftest`/`models`/`application` не менялись.
- Никакого monkeypatch БД/locking/бизнес-guards и fake repository;
  Telegram transport только fake, сеть вне DB tx.
- Product bug не найден: строгие ожидания описывают текущий корректный контракт;
  `fix.patch` не прилагается. Если PG-прогон покажет расхождение, тест должен
  падать, а не принимать дефект за правило.

## Приёмка Codex (воспроизводимо)

1. Сохранить мир/бэкап единственной development DB (`goaletto`, `development`).
2. Последовательно, без параллельных PG-раннеров:
   `GOALETTO_ALLOW_DEVELOPMENT_RESET=1 python -m pytest -q -m integration -k race_acceptance`.
3. Ожидать 6 passed при отсутствии флаков; при споре повторить только при
   реальном основании (lock-таймаут/дедлок в логе), а не для накрутки.
4. Восстановить исходный мир из бэкапа. production throughput/p95 этим набором не обещается.

## Статус проверок агентом

- `ruff check` — passed.
- `ruff format --check` — passed.
- `mypy` (конфиг репозитория) — passed, 111 файлов.
- `pytest --collect-only` нового файла — 6 tests collected.
- `pytest -q -m 'not integration'` — 508 passed.
- PG integration (`-m integration`, reset DB) — not run агентом; выполняет Codex.
