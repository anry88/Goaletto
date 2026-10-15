# Кубок чемпионов: чистая сетка внутри группы `champions-bracket-v1`

Независимый вычислительный подпакет AND-126. Только чистые функции:
без БД, сети, часов, Telegram, worker и неявного RNG. Отбор и группы уже
зафиксированы в `champions-groups-v1`; агрегат/ET/пенальти решает общий
`knockout-v1`; деньги финала делит `neutral-final-v1`. Подробный runtime,
kickoff/jobs, выплаты и UI — вне этого модуля.

Файлы:

* `src/goaletto/domain/champions_bracket.py` — контракт и чистые функции;
* `tests/unit/test_champions_bracket.py` — unit-проверки;
* этот документ — контракт и точки подключения.

Существующие `champions_groups.py`, `cup_bracket.py`, `knockout.py`,
`neutral_final.py`, матчевый движок, ORM, миграции, application, bot, worker и
`en.json` не затронуты. Второй матчевый движок не строится; `CupTie`/`CupLeg`
остаются фиксированными раундами кубка 50 клубов.

## Вход

```python
from goaletto.domain.champions_bracket import build_champions_bracket

bracket = build_champions_bracket(group=group, tournament_id=tournament_id, seed=7)
```

* `group: ChampionsGroup` — явно замороженная группа одного qualifying tier
  (участники `8/16/32/64/128/256/512`, `R = log2(N)` раундов). Квалификация уже
  зафиксирована там: top8 не отбирается повторно, `qualifying_tier/season`
  не переписывается по текущей таблице, прошлая таблица первого сезона не
  выдумывается. Пустой/неполный вход — `ValueError`, а не изобретённые клубы.
* `tournament_id: UUID` — отдельный ID межкомплектного турнира от orchestration.
  Он выбирает namespace устойчивых tie IDs. Произвольный сезон одного участника
  как сезон всего турнира не используется.
* `seed: int` — явный жребий первого раунда, nonnegative BIGINT (`bool`
  отклоняется).

Все начинают с первого раунда, без staged entry кубка 50 и без bye. База
клубов сортируется по hex и только затем тасуется через
`stable_seed(seed, f"{policy}:{tournament}:{group}:round-1:pool")`, поэтому
результат независим от порядка `participants/group input`. Сортировка UUID —
только база перед shuffle, не пары. Дальше победители идут по зафиксированным
`winner(tie)` ссылкам, без нового случайного жребия при результате/retry.

## Сетка и структурное расписание

Для `N` участников:

* противостояний `N-1`, матчей `2N-3`, матчевых дней `2R-1` (5/7/9/11/13/15/17);
* раунд `r < R` — два матча с обменом поля в циклах `2r-1` и `2r`;
  финал (`R`) — один нейтральный матч в цикле `2R-1`;
* `champions_cycles = 1..D`, `free_cycles = D+1..18` (цикл 18 всегда свободен);
  фаза каждого матчевого дня — день 2 четырёхдневного цикла
  (`CHAMPIONS_PHASE_DAY = 2`).

| N | R | D | Ties | Matches |
|---|---|---|---|---|
| 8 | 3 | 5 | 7 | 13 |
| 16 | 4 | 7 | 15 | 29 |
| 32 | 5 | 9 | 31 | 61 |
| 64 | 6 | 11 | 63 | 125 |
| 128 | 7 | 13 | 127 | 253 |
| 256 | 8 | 15 | 255 | 509 |
| 512 | 9 | 17 | 511 | 1021 |

`ChampionsBracket` хранит `policy_version`, `tournament_id`, `group_id`,
`tier`, `seed`, канонические `participants` (sorted by hex), `ties`,
`rounds`, `match_days`, `champions_cycles`, `free_cycles`, `phase_day`.
`ChampionsTie` хранит `id`, `round_number`, `tie_index`, `champions_cycles`,
`home/away: CupSlot`, `neutral`. `ChampionsLeg` раскрывает 1–2 конкретных
матча с обменом поля, `champions_cycle` и `phase_day = 2`. Слоты
переиспользуют `CupSlot`/`club_slot`/`winner_slot`: их контракт
(`club` или `winner(tie)`) подходит без изменений.

## IDs и replay

* `tie_id_for(tournament_id, group_id, round_number, tie_index)` —
  `uuid5(CHAMPIONS_BRACKET_NAMESPACE,
  "champions-bracket-v1:<tournament>:<group>:round:<r>:index:<i>")`,
  seed-независим; меняется только состав пар, не ID.
* Первый раунд — единственный seeded shuffle; поздние раунды — фиксированное
  дерево (`tie[i]` раунда `r` питается от `2i`/`2i+1` раунда `r-1`).
  Ни время job, ни вызов `random` без seed не влияют на результат.

Для replay сохранить durable receipt:

* `policy_version = "champions-bracket-v1"`, `tournament_id`, `group_id`,
  `tier`, `seed`;
* замороженные `participants` (полный набор клубов группы);
* итоговые `ties` со слотами, раундами и циклами.

Повтор с теми же входами воспроизводит ту же сетку. Не пересчитывать draw
при retry и не подменять исторические snapshots живой таблицей.

## Прогрессия

Принимаются только окончательные winners пар от `domain.knockout`;
ничья одного матча победителем не считается.

```python
from goaletto.domain.knockout import KnockoutTie, MatchResult, decide_tie

knockout_tie = KnockoutTie(
    policy_version="knockout-v1", club_a_id=club_a, club_b_id=club_b, tie_format="two-leg"
)
decision = decide_tie(
    tie=knockout_tie,
    first_match=MatchResult(
        segment="leg1", home_id=club_a, away_id=club_b,
        home_goals=2, away_goals=0, completed=True,
    ),
    second_match=MatchResult(
        segment="leg2", home_id=club_b, away_id=club_a,
        home_goals=1, away_goals=1, completed=True,
    ),
)
assert decision.status == "decided" and decision.winner is not None
validate_winners(bracket, {tie_id: decision.winner})
```

* `legs_of(tie)` / `all_matches(bracket)` — раскрытие без kickoff/jobs.
* `final_tie(bracket)` / `tie_by_id(bracket, tie_id)` — получение пар.
* `resolve_slot(bracket, slot, winners)` — известный клуб или `None`.
* `possible_clubs(bracket, slot, winners)` /
  `possible_tie_clubs(bracket, tie_id, winners)` — условные клубы: после
  вылета ветвь проигравшего исчезает, другая сохраняется.
* `validate_winners(bracket, winners)` — чужой/вне поддерева winner,
  пропущенные предшественники, проигравший в следующей ветке и противоречие —
  `ValueError`. Финал без предков отклоняется.
* `champion(bracket, winners)` — победитель финала или `None`.
* `validate_against_group(bracket, group)` — сверка с замороженной группой:
  тот же `group_id/tier`, тот же набор клубов, те же cycles/phase.

Восстановленный граф проверяется как свежий: каждый frozen участник входит
ровно один раз, нет чужих/потерянных клубов, циклов, повторного использования
feeder в двух местах, одинаковых соперников, неверных раундов/дней и
лишнего/пропавшего финала.

Два известных финалиста передаются в существующий калькулятор:

```python
from goaletto.domain.neutral_final import NeutralFinalSnapshot, settle_neutral_final

snapshot = NeutralFinalSnapshot(
    policy_version="neutral-final-v1",
    venue_id=venue_id,
    capacity=50_000,
    ticket_price=5,
    club_a_id=finalist_a,
    club_b_id=finalist_b,
    supporters_a=2_500,
    supporters_b=2_500,
    seed=7,
    neutral=True,
)
result = settle_neutral_final(snapshot)
assert result.payout_a + result.payout_b == result.gross
```

## Рост и форма

Эта сетка сама рост не начисляет и запрета кубкового прогресса не вводит.
Целевое правило: все игровые матчи (лига, кубок, чемпионы, товарищеские)
влияют на форму и развитие за фактические минуты; у товарищеских прогресс
меньше официальных, множитель ещё не утверждён. Действуют общие caps.
Текущий допуск только лиги и отсутствие формы за матч — пробелы реализации,
а не баланс. Подключение — Codex по AND-130 в общем runtime.

## Пример

```python
from goaletto.domain.champions_bracket import (
    all_matches,
    build_champions_bracket,
    champion,
    possible_tie_clubs,
)

bracket = build_champions_bracket(group=group, tournament_id=tournament_id, seed=11)
assert len(bracket.ties) == len(bracket.participants) - 1
assert len(all_matches(bracket)) == 2 * len(bracket.participants) - 3
assert bracket.free_cycles[-1] == 18
assert champion(bracket, {}) is None
assert possible_tie_clubs(bracket, bracket.ties[-1].id, {}) == set(bracket.participants)
```

## Bootstrap

Первый сезон без прошлой таблицы и новый комплект посреди сезона не
получают выдуманных участников. API принимает только готовую
`ChampionsGroup` из завершённого cohort; пустой/неполный вход отклоняется.
Bootstrap policy (кого допускать до первого розыгрыша) — отдельное решение
перед включением турнира и здесь не выбирается.

## Подключение (осталось, делает Codex)

Чистый калькулятор готов; durable интеграция вне подпакета:

* persisted draw/slots/results, идемпотентный retry без пересчёта жребия;
* выбор совместимых завершённых сезонов и границ cohort, bootstrap-правило;
* окончательные tie results из общего knockout runtime AND-124
  (агрегат/ET/пенальти);
* резервирование тренировочных дат (официальные матчи отменяют pending
  training с outbox, товарищеские отклоняют scheduled/applied training);
* десятидневные fixtures/jobs, условные планы без выдуманных соперников/времени,
  фиксированные объявленные встречи;
* общий runtime, энергия/восстановление, минуты/развитие/форма (AND-130);
* нейтральная арена, две атомарные ledger-выплаты, outbox и UI
  (`/calendar`, экраны пар, UTC-фазы + локальный kickoff).

Не вводить скрыто: staged entry кубка 50, bye, новый жребий при результате,
пересчёт top8 из живой таблицы, kickoff вне горизонта, второй движок
агрегата/ET/пенальти, arena/ledger внутри pure-модуля.
