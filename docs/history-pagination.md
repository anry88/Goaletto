# История для большого архива · codex/history-pagination

Исходный commit: `ba16344af10f3131b7daf5f74bed64a209fbd087` (`origin/main`).
Ветка: `codex/history-pagination` в отдельном checkout `/tmp/goaletto-history-pagination`.
В `main` самостоятельно не вливать.

## Что изменено

`src/goaletto/application/history.py` — единственный продуктовый файл:

- `get_club_results`: `COUNT` + bounded `SELECT ... ORDER BY scheduled_start_at DESC, id
  LIMIT 5 OFFSET ...`. Матчи и `MatchResult` грузятся одним `IN` запросом каждый
  только для нужной страницы. Снапшот декодируется один раз на матч
  (раньше дважды). Порядок стабилен для одинаковых дат (тай-брейк `id`).
- `get_player_matches`: `COUNT` и страница через `JOIN Fixture/Match` с JSONB
  предикатом `starting_snapshot @> home/away` для текущего клуба. Затем батч:
  матчи `IN`, receipts `IN`, `stored_events_batch` (тики `IN` + стейджи `IN`).
  Участие проверяется по декодированному снапшоту именно своего клуба
  (соперник исключается), минуты — только из stored terminal `conditions`
  (скамейка без condition = 0). Retired без live `Player` остаётся читаем,
  чужой игрок — `PermissionDenied`.
- `stored_events` сохранён для одиночного отчёта; добавлен `stored_events_batch`
  (два запроса на страницу). Слияние `MatchTick + MatchStage` прежнее:
  сортировка `(tick, stage-flag)`, halftime substitutions включены,
  dedup по `event.id`.
- `get_club_seasons`: вместо N+1 `get(Season)/get(League)` — два `IN` запроса.
- `get_match_report`, `get_season_archive`: без N+1, логика прежняя.
- Frozen названия (kickoff snapshot), `state_hash`, authorization через
  `owned_club`, receipts/user effects (`club_opened/match_report_opened/
  lineup_opened`), очки/таблица из хранимых счётчиков (`rank_league`),
  без пересчёта матчей и без MVP.

`history_router.py` / `history_cards.py` / `history_callbacks.py` не менялись:
существующие `hx:` callbacks совместимы, новый page API не потребовался
(номера страниц и `HISTORY_PAGE_SIZE=5` сохранены).

Тесты:

- `tests/unit/test_history.py` — Recorder обновлён под `COUNT/LIMIT/OFFSET/IN`
  и JSONB предикат (парсит `season_id`, `person_id`, IN-списки, лимит/офсет;
  `Player`/`Person` теперь фильтруются по id). Старые 11 тестов зелёные.
- `tests/unit/test_history_pagination.py` (новый, 12 тестов): пустая история,
  границы страниц, одинаковые даты, bounded statements, минуты скамейки из
  conditions, пустая история действующего игрока, чужой/границы, retired,
  просроченное владение, батч halftime+dedup, frozen имена, батч сезонов.
- `tests/integration/test_history_archive.py` (новый, 2 теста на Goaletto DB):
  synthetic архив 10 клубов × 2 сезона × 18 finished fixtures, проверка
  bounded snapshots (5 декодов на страницу), минуты 90/0, чужой denied,
  `EXPLAIN` для COUNT/page/player.

## Сравнение запросов до/после (N=18, страница 5)

Клубные результаты, одна страница:

- До: `SELECT fixtures` все 18 строк + `SELECT matches WHERE fixture_id IN (5)`
  + 5× `GET match_results` + декод снапшотов 5×2. Фикстуры грузятся все.
- После: `SELECT COUNT` (без строк) + `SELECT fixtures ... LIMIT 5`
  (5 строк) + `SELECT matches IN (5)` + `SELECT match_results IN (5)`
  + декод 5×1. Число statements не растёт с N (проверено 1 vs 12 в unit).

История игрока, одна страница:

- До: `SELECT fixtures` все 18 + для каждого из 18:
  `SELECT match` + `GET receipt` + `SELECT ticks` + `SELECT stages`
  = ~1+18×4=73 запроса и 18 декодов снапшотов + 18× нагрузок events.
- После: `SELECT COUNT (JOIN+JSONB)` + `SELECT fixtures JOIN ... LIMIT 5`
  + `SELECT matches IN (5)` + `SELECT receipts IN (5)`
  + `SELECT ticks IN (5)` + `SELECT stages IN (5)` = 6 history-запросов
  и 5×2 декодов (starting+terminal) только страницы.

Списки сезонов (M членств):

- До: 1 + 2M `GET`.
- После: 1 + 2 `IN`.

## EXPLAIN (synthetic 18, Goaletto DB 127.0.0.1:5433)

```
=== COUNT club ===
Aggregate
  ->  Index Scan using ix_fixture_schedule on fixtures
        Index Cond: ((status)::text = 'finished'::text)
        Filter: ((home_club_id = '...'::uuid) OR (away_club_id = '...'::uuid))
=== PAGE club ===
Limit
  ->  Incremental Sort
        Sort Key: scheduled_start_at DESC, id
        Presorted Key: scheduled_start_at
        ->  Index Scan Backward using ix_fixture_schedule on fixtures
              Index Cond: ((status)::text = 'finished'::text)
              Filter: ((home_club_id = '...'::uuid) OR (away_club_id = '...'::uuid))
=== COUNT player (JSONB @>) ===
Aggregate
  ->  Nested Loop
        ->  Index Scan using ix_fixture_schedule on fixtures
              Index Cond: ((status)::text = 'finished'::text)
              Filter: ((home_club_id = '...'::uuid) OR (away_club_id = '...'::uuid))
        ->  Index Scan using matches_fixture_id_key on matches
              Index Cond: (fixture_id = fixtures.id)
              Filter: ((starting_snapshot @> '{...}'::jsonb) OR (starting_snapshot @> '{...}'::jsonb))
```

Вывод: club COUNT/page идут по `ix_fixture_schedule` с `Limit`, snapshots
не возвращаются в COUNT. Player идёт Nested Loop по fixture+match PK с
JSONB фильтром без загрузки лишних снапшотов; для сотен сезонов нужен GIN
(см. отдельный patch для Codex).

## Приёмка

- Пустая история: `total 0, pages 1, entries ()`, страница 2 — `ValueError`.
- Границы: 0, `>pages`, `>10000` — `ValueError`; 6 fixtures → 5+1.
- Одинаковые даты: порядок по `id`, детерминирован.
- Чужой игрок (away snapshot) — `PermissionDenied`, даже при live=None.
- Retired (live None, снапшоты есть) — читается, имя из frozen snapshot.
- Старое владение (expired/ended) — `PermissionDenied`, данных нет.
- Halftime: `second_half_start` substitution в общем порядке, дубликат по `id`
  выкинут; `stored_events_batch` == `stored_events` для одиночки, 2 запроса
  на любое число матчей страницы.
- Snapshot: live rename не меняет `home_name/away_name`, счёт из receipt,
  `state_hash` из receipt, possession math прежняя.

## Ограничения

- Player `COUNT` всё ещё читает JSONB всех матчей клуба для фильтра
  (без возврата payloads). Для архива в тысячи матчей нужен GIN индекс
  из proposal-патча; без него COUNT — seq-filter, но bounded по возврату.
- Редкий retired (последняя игра 100 матчей назад) на SQL-пути находится
  сразу через `@>` (bounded). Без JSONB backend (test doubles) — только
  малым окном; production — Postgres, ок.
- Сезоны возвращаются все (их мало); пагинации сезонов нет, callbacks `hx:s:1`
  сохранён.
- MVP и производные рейтинги не реализованы (нет формулы).
- Models/Alembic/dispatcher/runtime/assets не тронуты.

## Файлы

- `src/goaletto/application/history.py`
- `tests/unit/test_history.py` (двойники под новые запросы)
- `tests/unit/test_history_pagination.py`
- `tests/integration/test_history_archive.py`
- `docs/history-pagination.md` (этот файл)
- `docs/history-index-proposal.patch` — предложение Codex (не применять здесь)
- `work/history-explain.txt` — сырой EXPLAIN (gitignored, приложен в отчёте)

Проверки: `ruff check`, `ruff format --check`, `mypy`, `pytest -q -m 'not integration'`,
`GOALETTO_ALLOW_DEVELOPMENT_RESET=1 pytest -q -m integration -k history_pagination`
на единственной Goaletto DB (development, без реальных игроков).
Новых БД не создавалось.

## Интеграция Codex · 2026-10-13

Индексы приняты в общей migration0011_agent_wave2 с правильным parent
0010_telegram_portrait_files; GIN использует jsonb_path_ops. Устранён приём
страницы2 пустой истории игрока; повреждённый receipt/checkpoint не скрывается
пустыми строками. Добавлены get_club_seasons_page и кнопки hx:s:<page>: COUNT
+ joined SELECT с LIMIT5, legacy all-seasons API сохранён для совместимости.
Сценарий12 сезонов / 3 страниц проверен на рабочей Goaletto DB.
