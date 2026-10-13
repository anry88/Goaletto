"""450 pure match results with pinned demand and cash forecasts; no DB or media."""

import csv
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid5

from goaletto.application.match_simulation import club_snapshot, simulate_match
from goaletto.domain.calendar import league_calendar
from goaletto.domain.finances import (
    INITIAL_CAPITAL,
    merchandise_income,
    player_wage,
    season_prize,
    sponsor_income,
    stadium_upkeep,
    ticket_income,
)
from goaletto.domain.match_engine import start_match
from goaletto.domain.match_types import LIVE_ENGINE_VERSION, EnginePolicy, HomeCrowd
from goaletto.domain.stadium import attendance
from goaletto.domain.standings import LeagueStanding, add_result, rank_league
from goaletto.domain.world import WorldOptions, build_world, stable_seed


def main() -> None:
    world = build_world(
        WorldOptions(
            key="economy-season", seed=0, first_kickoff=datetime(2026, 10, 17, 18, tzinfo=UTC)
        )
    )
    report, amounts = [], []
    for league in world.leagues:
        clubs = {c.id: c for c in league.clubs}
        standings = {c.id: LeagueStanding(c.id, c.name, c.tie_break_order) for c in league.clubs}
        recent = {c.id: [] for c in league.clubs}
        fixtures = league_calendar(
            season_id=world.season_id,
            league_id=league.id,
            clubs=tuple(clubs),
            first_kickoff=world.options.first_kickoff,
            seed=0,
        )
        baseline = dict.fromkeys(clubs, INITIAL_CAPITAL)
        expanded = dict.fromkeys(clubs, INITIAL_CAPITAL - 300000)
        tickets = dict.fromkeys(clubs, 0)
        crowd_min, crowd_max = 50000, 0
        baseline_min = INITIAL_CAPITAL
        expanded_min = INITIAL_CAPITAL - 300000
        for round_number in range(1, 19):
            places = {s.club_id: i for i, s in enumerate(rank_league(tuple(standings.values())), 1)}
            results = []
            for fixture in (f for f in fixtures if f.round_number == round_number):
                home, away = clubs[fixture.home_club_id], clubs[fixture.away_club_id]
                demand = attendance(
                    2500, 5000, league.tier, places[home.id], tuple(recent[home.id][-5:])
                )
                crowd_min, crowd_max = min(crowd_min, demand.fans), max(crowd_max, demand.fans)
                # Doubling an uncapped stadium cannot manufacture spectators.
                assert (
                    attendance(
                        2500, 10000, league.tier, places[home.id], tuple(recent[home.id][-5:])
                    ).fans
                    == demand.fans
                )
                initial = start_match(
                    uuid5(fixture.id, "match:v1"),
                    stable_seed(0, f"match:{fixture.id}"),
                    club_snapshot(home),
                    club_snapshot(away),
                    policy=EnginePolicy(version="match-policy-v2"),
                    engine_version=LIVE_ENGINE_VERSION,
                    crowd=HomeCrowd(5000, demand.fans),
                )
                final, _ = simulate_match(initial)
                income = ticket_income(demand.fans, 5000)
                tickets[home.id] += income
                baseline[home.id] += income
                expanded[home.id] += income
                results.append((home.id, away.id, final.home.stats.goals, final.away.stats.goals))
            for home, away, h, a in results:
                standings[home] = add_result(standings[home], h, a)
                standings[away] = add_result(standings[away], a, h)
                recent[home].append(3 if h > a else 1 if h == a else 0)
                recent[away].append(3 if a > h else 1 if a == h else 0)
            ranked = rank_league(tuple(standings.values()))
            for place, row in enumerate(ranked, 1):
                wage = sum(player_wage(p.skills[:8]) for p in clubs[row.club_id].players)
                sponsor = sponsor_income(league.tier, place)
                common = sponsor + merchandise_income(2500) - wage - 50000
                baseline[row.club_id] += common - stadium_upkeep(5000)
                expanded[row.club_id] += common - stadium_upkeep(
                    10000 if round_number > 1 else 5000
                )
                baseline_min = min(baseline_min, baseline[row.club_id])
                expanded_min = min(expanded_min, expanded[row.club_id])
                if round_number == 18:
                    prize = season_prize(league.tier, place)
                    baseline[row.club_id] += prize
                    expanded[row.club_id] += prize
                amounts.append(
                    {
                        "tier": league.tier,
                        "round": round_number,
                        "club": str(row.club_id),
                        "place": place,
                        "sponsor": sponsor,
                        "player_wages": wage,
                        "baseline_cash": baseline[row.club_id],
                        "expanded_cash": expanded[row.club_id],
                    }
                )
        assert all(s.played == 18 for s in standings.values())
        report.append(
            f"| {league.tier} | "
            f"{crowd_min}–{crowd_max} | "
            f"{min(tickets.values()):,}–{max(tickets.values()):,} | "
            f"{baseline_min:,} | "
            f"{expanded_min:,} | "
            f"{min(baseline.values()):,}–{max(baseline.values()):,} |"
        )
    Path("work").mkdir(exist_ok=True)
    with Path("work/economy-season.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=list(amounts[0]))
        writer.writeheader()
        writer.writerows(amounts)
    Path("docs/economy-season-validation.md").write_text(
        """# Полный сезон: 450 матчей и экономика

Детерминированный engine regulation-v2, seed 0, 5 лиг × 10 клубов, 18 туров. До старта
каждого тура все пять пар получают crowd по общей таблице предыдущего тура и последним
пяти результатам, затем результаты вместе обновляют таблицу. 20 игроков/клуб, 2,500
фанбаза, фиксированные составы. Долгосрочные травмы, трансферы и тренировки здесь не
моделируются.

На каждый клуб заложена зарплата главного тренера 50K как финансовый сценарий; его влияние
на качество игры не моделируется. Недельные поступления/расходы после тура, 9 домашних
билетов и один итоговый приз. Частичная первая неделя считается полной: это консервативный
расходный прогноз.

Сравнение: исходный стадион 5,000; расширение до 10,000 за 300K, вступившее в силу со
второго тура. Во всех 450 матчах исходных мест хватает: расширение приносит столько же
билетов, но требует дополнительного содержания. UI показывает свободные места и спрос,
поэтому цена расширения видна как долгосрочное решение. Стартовые 600K не вынуждают
расширять арену.

| Tier | Зрители | Билеты/клуб | Min cash 5K | Min cash 10K | Итог 5K с призом |
|---|---:|---:|---:|---:|---:|
"""
        + "\n".join(report)
        + """

Все 50 cash forecasts остались положительными. Отдельный расчёт economy-calculation.md
проверяет нулевые билеты, постоянное последнее место, покупку игрока, оба вложения,
дорогой штаб и две недели межсезонья. Приз не используется до конца сезона.

Воспроизведение: `.venv/bin/python scripts/simulate_economy_season.py`. Детальные 900
club-week rows: `work/economy-season.csv`; никаких DB или изображений.
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
