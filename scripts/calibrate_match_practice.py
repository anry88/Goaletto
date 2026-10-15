"""Bounded AND-130 calibration: official scale, friendly factors, form, seasons.

Pure forecast with the real domain functions only (no DB, engine copy or
network). Compares friendly 25/50/75% proposals and two 80-day calendars
with training on free UTC dates only. Results are implementation
proposals, not approved game balance and not an already-connected game.

Reproduce: ``.venv/bin/python scripts/calibrate_match_practice.py --seed 7``
"""

import argparse
from dataclasses import replace
from uuid import UUID

from goaletto.domain.development import GrowthBudget
from goaletto.domain.match_development import develop_appearance
from goaletto.domain.match_practice import (
    Competition,
    develop_match_practice,
    form_up_chance,
    practice_form,
)
from goaletto.domain.training import (
    PROGRESS_KEYS,
    SKILLS,
    Focus,
    Intensity,
    TrainingPlayer,
    train_player,
)

PERSON = UUID(int=1)
STAFF = {"attack_coach": 10}


def player(age: int, form: int = 6) -> TrainingPlayer:
    return TrainingPlayer(
        PERSON,
        age,
        "MC",
        dict.fromkeys(SKILLS, 10),
        dict.fromkeys(SKILLS, 20),
        dict.fromkeys(PROGRESS_KEYS, 0),
        form,
        0,
    )


def official_parity() -> list[str]:
    lines = ["== official 90 equals match-development-v1 =="]
    for age in (18, 22, 30):
        old = develop_appearance(player(age), 90, STAFF, GrowthBudget(age))
        new = develop_match_practice(
            player(age), "league", 90, STAFF, GrowthBudget(age), match_seed="parity"
        )
        lines.append(
            f"age {age}: v1 granted={sum(old.growth.granted.values())} "
            f"practice granted={sum(new.granted.values())} "
            f"exp {old.experience_gain}={new.experience_gain} "
            f"match={old == old and new.granted == old.growth.granted}"
        )
    return lines


def factor_table() -> list[str]:
    lines = ["== friendly factor proposal (90 min, staff 10, free budget) =="]
    lines.append("age | official skill | 25% | 50% | 75% | official exp | 25% | 50% | 75%")
    for age in (18, 21, 26, 30, 34):
        official = develop_match_practice(
            player(age), "league", 90, STAFF, GrowthBudget(age), match_seed="factor"
        )
        row = [f"{age} | {sum(official.granted.values())} |"]
        exps = [f"{official.experience_gain} |"]
        for factor in (2500, 5000, 7500):
            friendly = develop_match_practice(
                player(age),
                "friendly",
                90,
                STAFF,
                GrowthBudget(age),
                match_seed="factor",
                friendly_skill_factor=factor,
                friendly_experience_factor=factor,
            )
            row.append(f" {sum(friendly.granted.values())} |")
            exps.append(f" {friendly.experience_gain} |")
        lines.append("".join(row) + " " + "".join(exps))
    lines.append(
        "Choice proposal: 50% (5000 bps). 25% makes friendlies near-noise for "
        "veterans (30y: 30 vs 120 official); 75% closes the gap so a friendly "
        "plus training rivals an official week. 50% keeps friendlies positive "
        "but clearly below official at every age."
    )
    return lines


def extra_time_table() -> list[str]:
    lines = ["== extra time is total minutes, no double counting (age 22) =="]
    for minutes in (90, 105, 120):
        result = develop_match_practice(
            player(22), "cup", minutes, STAFF, GrowthBudget(22), match_seed="et"
        )
        lines.append(
            f"{minutes} min: skill={sum(result.granted.values())} exp={result.experience_gain}"
        )
    return lines


def form_table(seed: int) -> list[str]:
    lines = [f"== form distribution over 2000 person seeds (base seed {seed}) =="]
    lines.append("minutes/form -> chance bps | observed +1/2000")
    for minutes in (30, 90, 120):
        for form in (4, 6, 7):
            chance = form_up_chance(minutes, form)
            ups = sum(
                practice_form(
                    form,
                    minutes,
                    person_id=UUID(int=1000000 + seed * 10000 + i),
                    match_seed=f"form-cal:{seed}",
                ).form
                == form + 1
                for i in range(2000)
            )
            lines.append(
                f"{minutes} min/form {form}: chance {chance} -> {ups}/2000 ({ups / 20:.1f}%)"
            )
    lines.append(
        "Reading: 90 min at form 6 gives ~8% per match, so ~12 such matches "
        "per +1 on average; form 7 is ~3%. No guaranteed +1, no fast "
        "saturation to 8 across a season."
    )
    return lines


def simulate_season(seed: int, dense: bool, friendly_factor: int) -> dict[str, int | str]:
    current = player(22)
    budget = GrowthBudget(22)
    training_bps = match_bps = experience = 0
    form = current.form
    matches = trainings = 0
    for day in range(80):
        phase = (day - 8) % 4 if day >= 8 else -1
        cycle = (day - 8) // 4 if day >= 8 else -1
        fixture: tuple[str, int] | None = None
        if day >= 8 and phase == 3:
            fixture = ("league", 90)
        elif day >= 8 and phase == 0 and (cycle < (6 if dense else 2)):
            if dense and cycle in (4, 5):
                fixture = ("cup", 120)  # two extra-time ties in the dense run
            else:
                fixture = ("cup", 90)
        elif dense and day >= 8 and phase == 1 and cycle < 6:
            fixture = ("champions", 90)
        elif dense and day >= 8 and phase == 2 and cycle < 8:
            fixture = ("friendly", 90)  # explicit manager request, free window
        if fixture is not None:
            competition, minutes = fixture
            result = develop_match_practice(
                replace(
                    current,
                    skills=dict(current.skills),
                    progress=dict(current.progress),
                    form=form,
                    experience=experience,
                ),
                Competition(competition),
                minutes,
                STAFF,
                budget,
                match_seed=f"season:{seed}:{day}:{competition}",
                friendly_skill_factor=friendly_factor,
                friendly_experience_factor=friendly_factor,
            )
            match_bps += sum(result.granted.values())
            budget = result.growth.budget
            current = replace(current, skills=result.skills, progress=result.progress)
            form, experience = result.form, result.experience
            matches += 1
        else:
            outcome = train_player(
                replace(
                    current,
                    skills=dict(current.skills),
                    progress=dict(current.progress),
                    form=form,
                    experience=experience,
                ),
                Focus.PLAYMAKING,
                Intensity.MEDIUM,
                seed=f"season:{seed}:{day}",
                trainer_skill=10,
                head_skill=10,
                budget=budget,
            )
            training_bps += outcome.gain_bps
            budget = outcome.budget
            current = replace(current, skills=outcome.skills, progress=outcome.progress)
            form, experience = outcome.form, outcome.experience
            trainings += 1
    return {
        "calendar": "dense" if dense else "moderate",
        "factor": friendly_factor,
        "matches": matches,
        "trainings": trainings,
        "training_bps": training_bps,
        "match_bps": match_bps,
        "form": form,
        "experience": experience,
    }


def season_table(seed: int) -> list[str]:
    lines = [f"== 80-day seasons, training only on free UTC dates (seed {seed}) =="]
    lines.append("calendar | factor | matches/trainings | training | match | form | exp")
    for dense in (False, True):
        factors = (5000,) if not dense else (2500, 5000, 7500)
        for factor in factors:
            row = simulate_season(seed, dense, factor)
            lines.append(
                f"{row['calendar']} | {row['factor']} | "
                f"{row['matches']}/{row['trainings']} | "
                f"{int(row['training_bps']) / 10000:.3f} | "
                f"{int(row['match_bps']) / 10000:.3f} | "
                f"{row['form']} | {row['experience']}"
            )
    lines.append(
        "Moderate (18 league + 2 cup, 60 trainings) vs dense (18 league + "
        "6 cup incl. 2x120 ET + 6 champions + 8 friendlies, 42 trainings). "
        "Match bps stay small next to training; friendlies add rhythm without "
        "replacing official practice. Pure-model forecast, not live game data."
    )
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate AND-130 match practice.")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    if type(args.seed) is not int or args.seed < 0:
        raise ValueError("Seed must be a nonnegative int")
    sections = [
        official_parity(),
        factor_table(),
        extra_time_table(),
        form_table(args.seed),
        season_table(args.seed),
    ]
    print("\n\n".join("\n".join(section) for section in sections))


if __name__ == "__main__":
    main()
