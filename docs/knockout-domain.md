# Кнут-аут пары: чистый контракт `knockout-v1`

Независимый вычислительный подпакет AND-124. Только чистые функции:
без БД, сети, часов, Telegram, worker и неявного RNG. Исходы матчей и ударов
приходят явными входными данными; вероятность не симулируется.

Файлы:

* `src/goaletto/domain/knockout.py` — контракт и три чистые функции;
* `tests/unit/test_knockout.py` — unit-проверки;
* этот документ — контракт и инструкция подключения.

Существующие `match engine/state/codec/policy`, `cup_bracket.py`,
`neutral_final.py`, ORM, миграции, application, bot, worker и `en.json`
не затронуты.

## Форматы и ориентация

`KnockoutTie(policy_version="knockout-v1", club_a_id, club_b_id, tie_format)`
держит ровно два разных клуба и формат `"two-leg"` / `"single"`.

* `"two-leg"`: сегменты `leg1` (дом A), `leg2` (дом B), `leg2-et` (дом B,
  продолжение ответного матча).
* `"single"`: сегменты `final` и `final-et`, оба в ориентации `A дома / B в гостях`.
  Нейтральность едет отдельно через `neutral_final` (`neutral=True`,
  `home_advantage=0`), а не перестановкой сторон.

Второй матч всегда обратный: `A–B 2:1` + `B–A 1:0` даёт агрегат `A–B 2:2`.
Суммировать home-голы обоих матчей запрещено. Правила выездного гола нет.

## Входы

```python
MatchResult(segment, home_id, away_id, home_goals, away_goals, completed)
PenaltyKick(kick_number, club_id, scored)  # kick_number явный, с 1
PenaltyShootout(club_a_id, club_b_id, first_kicker_id, kicks)
```

* Счета — точный `int >= 0` (`bool`/`float` отклоняются).
* `completed` / `scored` — точный `bool`.
* `kick_number` — точный `int >= 1`, подряд `1..N` без пропусков.
* Очередь строго чередуется от явного `first_kicker_id`.
* Незавершенный/прерванный матч (`completed=False`) не решает пару.

## Публичные функции

```python
decide_shootout(shootout: PenaltyShootout) -> ShootoutResult
apply_penalty_kick(shootout: PenaltyShootout, kick: PenaltyKick) -> PenaltyShootout
decide_tie(
    *,
    tie: KnockoutTie,
    first_match: MatchResult | None = None,   # leg1 или final
    second_match: MatchResult | None = None, # leg2; всегда None для single
    extra_time: MatchResult | None = None,    # leg2-et / final-et, только свои голы
    shootout: PenaltyShootout | None = None,
) -> TieDecision
```

`ShootoutResult` хранит `score_a/score_b`, `taken_a/taken_b`,
`status="awaiting"/"decided"`, `winner` и следующий
`next_kicker_id/next_kick_number` (или `None` после решения).

`TieDecision` хранит раздельно `regulation_a/b`, `extra_a/b`,
`aggregate_a/b (= regulation + extra)`, `penalty_a/b`, плюс
`status`, `winner`, `next_segment` и pinned входы для replay.
Пенальти никогда не входят в обычные голы/агрегат/таблицы.

Маршрутизация `decide_tie`:

* `await_match` + `next_segment in (leg1/leg2/leg2-et/final/final-et)` —
  ждать матч/окончание сегмента;
* `await_extra_time` + `next_segment in (leg2-et/final-et)` — начинать ET
  (только при равном агрегате/ничьей; ничья первого матча ET не запускает);
* `await_penalties` + `next_segment="penalties"` — начинать пенальти
  (только после завершенного ничейного ET);
* `decided` + `winner` + `next_segment=None` — один победитель.

ET без ничейного основания, пенальти без завершенного ничейного ET,
второй матч без первого, ET без регуляции, пенальти без ET, чужие/дубли
клубов, неверный home/away, пропуски и противоречивая очередность —
`ValueError`. Удары после решения отклоняются; повтор идентичного удара
возвращает тот же transcript; конфликт того же номера — `ValueError`.
Полный replay по immutable transcript воспроизводится; восстановленные
результаты с чужими суммами отклоняются в `__post_init__`.

Серия: по 5 ударов каждому, досрочное завершение при недостижимости
равенства после каждого удара; дальше пары sudden death без лимита и без
случайного победителя. В sudden death решение только после равного числа
ударов пары.

Часы (информационно, не назначается здесь): регуляция 30 тиков по
3 игровые минуты + 3 минуты перерыва = 33 минуты; ET ещё 10 тиков (5+5).
Паузы, kickoff/jobs, замены и дедлайны live-команд модуль не назначает.

## Пример

```python
from goaletto.domain.cup_bracket import validate_winners
from goaletto.domain.knockout import (
    KNOCKOUT_POLICY,
    KnockoutTie,
    MatchResult,
    decide_tie,
)

tie = KnockoutTie(
    policy_version=KNOCKOUT_POLICY,
    club_a_id=club_a,
    club_b_id=club_b,
    tie_format="two-leg",
)
decision = decide_tie(
    tie=tie,
    first_match=MatchResult(
        segment="leg1",
        home_id=club_a,
        away_id=club_b,
        home_goals=2,
        away_goals=1,
        completed=True,
    ),
    second_match=MatchResult(
        segment="leg2",
        home_id=club_b,
        away_id=club_a,
        home_goals=1,
        away_goals=0,
        completed=True,
    ),
)
assert decision.status == "await_extra_time"  # агрегат 2:2
# После ET/пенальти и decision.status == "decided":
validate_winners(bracket, {tie_id: decision.winner})
```

`validate_winners` принимает winner только после определения обоих
участников и всех предшественников: одиночный финал без предков
отклоняется; см. тест `test_knockout_winner_feeds_cup_bracket_validate_winners`.

## Подключение (осталось для AND-124, делает Codex)

Чистый калькулятор готов; durable runtime вне этого подпакета:

* durable фазы/checkpoint на сегмент (`leg1/leg2/ET/penalties`), идемпотентные
  receipt-ключи на fixture+сегмент, retry без повторного счета;
* применение ET к движку: +10 тиков, минуты 91–120, энергия/восстановление,
  рост/минуты развития, травмы/карточки, лимиты состава;
* окна live-команд в ET и серии (дедлайны, доступность футболистов,
  выбор бьющих/вероятности — будущая интеграция матча, не этот модуль);
* календарь: условные `possible_club_ids` очищаются для выбывших;
  kickoff/jobs только в 10-дневном горизонте; объявленные встречи фиксированы;
* нейтральный финал: `settle_neutral_final` + две ledger-выплаты атомарно;
* outbox-уведомления о ET/пенальти/вылете; PostgreSQL-транзакции и retry
  с теми же snapshot-ключами; откат не оставляет частичных выплат;
* UI `/calendar` и экраны пар: UTC-фазы + локальный kickoff, без выдуманных
  соперников/времени для неизвестных победителей.

Не вводить скрыто: секундные паузы, новые замены, лимит серии со случайным
победителем, правило выездного гола, пересчет агрегата из home-голов,
зачет пенальти в голы игроков/таблиц.
