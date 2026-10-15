# Кубки чемпионов: чистые отбор и группировка `champions-groups-v1`

Независимый вычислительный подпакет AND-126. Только чистые функции:
без БД, сети, часов, Telegram, worker и неявного RNG. Подробная сетка,
extra time/пенальти, venue, kickoff/jobs, выплаты и UI — вне этого модуля.

Файлы:

* `src/goaletto/domain/champions_groups.py` — контракт и чистые функции;
* `tests/unit/test_champions_groups.py` — unit-проверки;
* этот документ — контракт и точки подключения.

Существующие `cup_bracket.py`, `league_allocation.py`, `neutral_final.py`,
`knockout.py`, матчевый движок, ORM, миграции, application, bot, worker и
`en.json` не затронуты.

## Вход

```python
from goaletto.domain.champions_groups import build_champions_groups
from goaletto.domain.league_allocation import LeagueSnapshot

result = build_champions_groups(snapshots, seed=7)
```

* `snapshots: Sequence[LeagueSnapshot]` — по одному frozen снимку на лигу
  совместимого завершённого cohort. Тип переиспользован чтением/import из
  `league_allocation`; его семантика (cohort/season/league/set IDs, tier
  1..5, frozen порядок десяти club IDs) подходит без изменений.
* `seed: int` — явный жребий, nonnegative BIGINT (`bool` отклоняется).
* `cohort_key` — явный ключ совместимости; смешанные ключи отклоняются.
  Совместимость дат и завершение турниров проверяет orchestration, а не
  произвольная строка ключа.
* Внутри комплекта ровно одна вертикаль tier 1..5 и один qualifying season;
  один season ID не может принадлежать разным комплектам.
* Дубликаты клубов/лиг, неполные вертикали, несбалансированные уровни,
  неверные числа/UUID и пустой cohort отклоняются `ValueError`.
  Отсутствие данных — это отсутствие отбора, а не повод выдумать клубы.

## Отбор

Для каждого tier 1..5 отбираются верхние восемь клубов каждой лиги
(места 1..8 из `order[:8]`). Места 9..10 не участвуют. Уровни не
смешиваются. `qualifying_tier` и `qualifying_season_id` остаются в каждом
`ChampionsParticipant` вместе с `qualifying_place`, `source_league_id` и
`source_league_set_id`: повышение/понижение после отбора не меняет допуск
задним числом. Текущие владельцы и живая таблица не являются входом
повторного draw — replay обязан загрузить те же frozen snapshots.

## Группировка

`split_group_sizes(N)` делит N совместимых комплектов на группы-степени
двух, максимум 64 комплекта в группе:

| N | Группы |
|---|---|
| 1 | 1 |
| 2 | 2 |
| 3 | 2+1 |
| 4 | 4 |
| 6 | 4+2 |
| 7 | 4+2+1 |
| 65 | 64+1 |
| 129 | 64+64+1 |

При N ≤ 64 это бинарное разложение (жадно крупнейшая степень двух, минимум
групп); при N > 64 — несколько групп по 64 и остаток тем же способом.
Каждый комплект входит ровно в одну группу каждого уровня, каждый
отобранный клуб — в один турнир своего qualifying tier.

Жребий seeded и воспроизводим, независим от порядка входа: база set IDs
сортируется по hex и только затем тасуется через
`stable_seed(seed, f"{policy}:{cohort_key}:tier:{tier}:sets")`.
Первые UUID не закрепляются в большой группе. У каждого уровня свой явный
ключ (`...:tier:1:sets`, ..., `...:tier:5:sets`), поэтому составы групп
могут различаться между уровнями при одном seed.

## Группы и структурное расписание

Для группы из G комплектов:

* участников `8G`, раундов `log2(8G)`, матчевых дней `2*log2(8G)-1`;
* G=1/2/4/8/16/32/64 → участники 8/16/32/64/128/256/512,
  раунды 3/4/5/6/7/8/9, дни 5/7/9/11/13/15/17;
* `champions_cycles = 1..D`, `free_cycles = D+1..18` (цикл 18 всегда
  свободен); фаза каждого такого дня — день 2 четырёхдневного цикла
  (`CHAMPIONS_PHASE_DAY = 2`);
* до финала по два матча (дома/в гостях), финал одиночный нейтральный.
  Это структурные числа, а не kickoff/jobs.

`ChampionsGroup` хранит `id`, `tier`, `group_index`, `league_set_ids`,
`participants`, `participant_count`, `rounds`, `match_days`,
`champions_cycles`, `free_cycles`, `phase_day`.
`ChampionsTierDraw` хранит проверяемый `ordered_set_ids` (тасованный
порядок), `group_sizes`, `group_ids`.
`ChampionsGroupsResult` хранит `policy_version`, `cohort_key`, `seed`,
`groups` и `draws`.

Второй общий knockout engine здесь не строится; 50-клубная сетка со своим
staged-entry форматом не меняется.

## IDs и replay

* `group_id_for(cohort_key, tier, group_index)` —
  `uuid5(CHAMPIONS_NAMESPACE, "champions-groups-v1:<cohort>:tier:<t>:group:<i>")`,
  seed-независим; меняется только состав, не ID.
* `participant_id_for(cohort_key, tier, club_id)` —
  `uuid5(..., "champions-groups-v1:<cohort>:tier:<t>:club:<hex>")`,
  seed-независим.
* Ни время job, ни вызов `random` без seed не влияют на результат.

Для replay сохранить durable receipt:

* `policy_version = "champions-groups-v1"`, `cohort_key`, `seed`;
* полные frozen snapshots (set/league/season IDs + порядок десяти клубов);
* per-tier draws (`ordered_set_ids`, `group_sizes`, `group_ids`);
* итоговые `groups` с pinned qualifying season/tier/place.

Повтор с теми же входами воспроизводит те же группы. Не пересчитывать draw
при retry и не подменять исторические snapshots живой таблицей.

## Пример

```python
from goaletto.domain.champions_groups import (
    build_champions_groups,
    champions_cycles_for,
    split_group_sizes,
)

assert split_group_sizes(7) == (4, 2, 1)
assert champions_cycles_for(4) == (1, 2, 3, 4, 5, 6, 7, 8, 9)

result = build_champions_groups(snapshots, seed=11)
tier1 = [g for g in result.groups if g.tier == 1]
assert sum(len(g.league_set_ids) for g in tier1) == len(snapshots) // 5
assert tier1[0].phase_day == 2
assert tier1[0].free_cycles[-1] == 18
```

## Bootstrap

Первый сезон без прошлой таблицы и новый комплект посреди сезона не
получают выдуманных qualifying results. API принимает только явно
переданный полный завершённый cohort; пустой/неполный вход отклоняется.
Bootstrap policy (кого допускать до первого розыгрыша) — отдельное решение
перед включением турнира и здесь не выбирается.

## Подключение (осталось, делает Codex)

Чистый калькулятор готов; durable интеграция вне подпакета:

* выбор совместимых завершённых сезонов и границ cohort, bootstrap-правило;
* persisted eligibility/groups/draw receipts, идемпотентный retry без
  пересчёта жребия;
* подробная сетка каждой группы (пары, legs, обмен полем, нейтральный
  финал) поверх общего knockout runtime AND-124 (агрегат/ET/пенальти);
* fixtures/jobs только в десятидневном горизонте, условные планы без
  выдуманных соперников/времени, резервирование тренировочных дат,
  фиксированные объявленные встречи;
* общий knockout runtime, энергия/восстановление, minutes/development;
* выплаты/деление дохода, спонсорство по destination tier, outbox и UI
  (`/calendar`, экраны пар, UTC-фазы + локальный kickoff).

Не вводить скрыто: выдуманных участников первого сезона, смешивание tiers,
задний пересмотр допуска после переходов, постоянные пары комплектов,
второй staged-entry формат или назначение kickoff вне горизонта.
