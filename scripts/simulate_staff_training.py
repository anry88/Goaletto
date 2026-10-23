"""Offline upper-bound seasons using current training and all match practice rules."""

import csv
from dataclasses import replace
from pathlib import Path
from uuid import UUID

from goaletto.domain.development import GrowthBudget
from goaletto.domain.match_practice import (
    CURRENT_POLICY,
    Competition,
    develop_match_practice,
)
from goaletto.domain.recovery import daily_recovery, ready_in, recover
from goaletto.domain.training import (
    PROGRESS_KEYS,
    SKILLS,
    Focus,
    Intensity,
    TrainingPlayer,
    train_player,
)


def simulate(
    age: int,
    skill: int,
    focus: Focus,
    intensity: Intensity,
    late: bool = False,
    friendlies: bool = False,
) -> dict[str, int | str]:
    player = TrainingPlayer(
        UUID(int=1),
        age,
        "MC",
        dict.fromkeys(SKILLS, 10),
        dict.fromkeys(SKILLS, 20),
        dict.fromkeys(PROGRESS_KEYS, 0),
        6,
        0,
    )
    budget, days, matches = GrowthBudget(age), 0, 0
    appearances = dict.fromkeys(Competition, 0)
    minutes_played = 0
    energy, remainder, minimum = 10000, 0, 10000
    for day in range(80):
        # Eight offseason dates + eighteen four-day cycles in one age-season.
        phase = (day - 8) % 4
        cycle = (day - 8) // 4
        competition = None
        if day >= 8:
            if phase == 3:
                competition = Competition.LEAGUE
            elif phase == 0 and cycle < (17 if late else 2):
                competition = Competition.CUP
            elif phase == 1 and late and cycle < 17:
                competition = Competition.CHAMPIONS
            elif phase == 2 and friendlies:
                competition = Competition.FRIENDLY
        energy, remainder = recover(
            energy, remainder, 86400, daily_recovery(player.skills["stamina"], age, skill)
        )
        if competition is not None:
            matches += 1
            appearances[competition] += 1
            # Stress second legs/finals with actual 120 minutes; first legs stay 90.
            extra_time = competition in {Competition.CUP, Competition.CHAMPIONS} and (
                cycle % 2 == 1 or cycle == 16
            )
            minutes = 120 if extra_time else 90
            minutes_played += minutes
            appearance = develop_match_practice(
                player,
                competition,
                minutes,
                {"attack_coach": skill},
                budget,
                match_seed=f"calibration:{competition}:{day}",
                policy=CURRENT_POLICY,
            )
            player, budget = (
                replace(
                    player,
                    skills=appearance.skills,
                    progress=appearance.progress,
                    experience=appearance.experience,
                    form=appearance.form,
                ),
                appearance.growth.budget,
            )
            energy = max(0, energy - (3574 if extra_time else 2680))
        else:
            days += 1
            result = train_player(
                player,
                focus,
                intensity,
                seed=f"calibration:{day}",
                trainer_skill=skill,
                head_skill=skill,
                physio_skill=skill,
                budget=budget,
            )
            budget = result.budget
            player = replace(
                player,
                skills=result.skills,
                progress=result.progress,
                form=result.form,
                experience=result.experience,
                morale=result.morale,
                leadership=result.leadership,
                leadership_progress=result.leadership_progress,
            )
            # Ignore training injuries to upper-bound accumulated development.
            energy = max(
                0,
                energy - {Intensity.LOW: 80, Intensity.MEDIUM: 140, Intensity.HIGH: 220}[intensity],
            )
        minimum = min(minimum, energy)
    assert budget.used["training"] <= 20000 and budget.used["match"] <= 10000
    assert all(budget.used[k] <= 15000 for k in SKILLS)
    assert sum(budget.used[k] for k in SKILLS) <= 30000
    assert budget.used["leadership"] <= 5000 and player.morale <= 50 + skill
    return {
        "age": age,
        "skill": skill,
        "focus": focus.value,
        "intensity": intensity.value,
        "calendar": ("two-finals" if late else "early-exit")
        + ("+friendlies" if friendlies else ""),
        "training_days": days,
        "matches": matches,
        **{f"{competition.value}_matches": count for competition, count in appearances.items()},
        "minutes_played": minutes_played,
        "experience_bps": player.experience * 10000 + player.progress["experience"],
        "training_bps": budget.used["training"],
        "match_bps": budget.used["match"],
        "total_bps": budget.used["training"] + budget.used["match"],
        "leadership_bps": budget.used["leadership"],
        "morale": player.morale,
        "form": player.form,
        "minimum_energy_bps": minimum,
    }


def main() -> None:
    rows = [
        simulate(age, skill, focus, intensity, late, friendlies)
        for age in (18, 21, 26, 30)
        for skill in (1, 2, 5, 6, 9, 10, 13, 14, 17, 18, 20)
        for focus in (Focus.TEAM, Focus.PHYSICAL)
        for intensity in Intensity
        for late in (False, True)
        for friendlies in (False, True)
    ]
    output = Path("work/staff-training-calibration.csv")
    output.parent.mkdir(exist_ok=True)
    with output.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# Калибровка штаба и тренировок · AND-111/112/113",
        "",
        "Воспроизведение: `.venv/bin/python scripts/simulate_staff_training.py`.",
        f"{len(rows)} чистых сценария, без DB и сети. "
        "Все фокусы и группы отдельно покрыты unit/PG-проверками.",
        "",
        "80 дней одного возраста: 8 межсезонья и 72 сезона. Ранний вылет: 18 игр лиги "
        "+ 2 кубковые; плотный календарь: 18 лиговых + 17 кубковых + 17 чемпионских. "
        "Это верхняя нагрузка для дошедшего до двух финалов клуба нижней лиги при "
        "максимальной чемпионской группе, а не симуляция реальной сетки/сезона в БД.",
        "Отдельные сценарии добавляют 18 явно выбранных товарищеских в день 3. "
        "Они добровольны: в игре нет автоматического назначения или штрафа за отдых. "
        "Все оставшиеся даты здесь выбраны под тренировки только для оценки верхней границы.",
        "Игрок начинает с навыками 10, потолками 20, моралью 50 и лидерством 10. "
        "Травмы исключены; штрафы усталости к минутам/участию не моделируются. "
        "Энергия — иллюстрация 24-часового recovery-v1 с типовой нагрузкой 2680/3574 bps; "
        "это не прогноз травм или матчевых результатов.",
        "",
        "Все матчи используют текущую match-practice-v3: официальные — полный прогресс, "
        "товарищеские — рабочие 50% навыков/опыта, с обычной динамикой формы. "
        "Для вторых кубковых/чемпионских встреч и финала взяты 120 минут, остальных — 90; "
        "пенальти минут не добавляют. Опыт начисляется отдельно от навыков.",
        "",
        "Общие пределы возраста-сезона: 3 балла суммарно по восьми навыкам, из них м"
        "аксимум 2 тренировочных и 1 матчевый; один навык — максимум 1.5. Это потолк"
        "и, не гарантированный прирост. Один балл равен 10000 целых долей. Физическа"
        "я тренировка делит прибавку между двумя навыками. Потенциал может ограничит"
        "ь раньше. При переходе клуба/владельца бюджет сохраняется; новый возраст от"
        "крывает следующий бюджет, дроби остаются.",
        "",
        "Лидерство отдельно: максимум 0.5 балла за возраст-сезон, по 0.0005–0.01 за "
        "посещённое занятие у главного тренера 1–20. Мораль поддерживается постепенн"
        "о до 50 + навык главного тренера, максимум 70/100; более высокая исходная м"
        "ораль не понижается. Форма и опыт имеют свои отдельные изменения.",
        "",
        "Средняя интенсивность, ранний вылет, физподготовка; весь штаб одной квалифи"
        "кации. Показаны накопленные дробные баллы:",
        "",
        "| Возраст | Навык штаба | Тренировки | Все матчи | Всего | Лидерство | Мораль |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        if (
            row["skill"] in (2, 6, 10, 14, 18)
            and row["focus"] == "physical"
            and row["intensity"] == "medium"
            and row["calendar"] == "early-exit"
        ):
            lines.append(
                f"| {row['age']} | {row['skill']} | {int(row['training_bps']) / 10000:.3f} "
                f"| {int(row['match_bps']) / 10000:.3f} "
                f"| {int(row['total_bps']) / 10000:.3f} "
                f"| {int(row['leadership_bps']) / 10000:.3f} | {row['morale']} |"
            )
    lines.extend(
        (
            "",
            "Возраст 18, штаб 10, средняя физподготовка: сравнение плотности календаря.",
            "Числа — один детерминированный сценарий, не статистика игроков.",
            "",
            "| Календарь | Тренировки | Лига/кубок/чемпионы/товарищеские | Минуты | Рост | Опыт |",
            "|---|---:|---|---:|---:|---:|",
        )
    )
    for row in rows:
        if (
            row["age"] == 18
            and row["skill"] == 10
            and row["focus"] == "physical"
            and row["intensity"] == "medium"
        ):
            lines.append(
                f"| {row['calendar']} | {row['training_days']} | "
                f"{row['league_matches']}/{row['cup_matches']}/"
                f"{row['champions_matches']}/{row['friendly_matches']} | "
                f"{row['minutes_played']} | {int(row['total_bps']) / 10000:.3f} | "
                f"{int(row['experience_bps']) / 10000:.3f} |"
            )
    maximum = max(int(r["total_bps"]) for r in rows)
    lines.extend(
        (
            "",
            f"Максимум всей сетки: {maximum / 10000:.4f} балла суммарно. "
            f"Ограничения выполнены во всех {len(rows)} сценариях.",
            "",
            "Восстановление сохраняет recovery-v1: выносливость 9, возраст 27, после обы"
            "чного матча 73.2% энергии. До полного запаса:",
            "",
            "| Физиотерапевт | Часов |",
            "|---|---:|",
        )
    )
    for skill in (0, 1, 5, 10, 15, 20):
        lines.append(f"| {skill} | {ready_in(7320, 0, daily_recovery(9, 27, skill)) / 3600:.2f} |")
    lines.extend(
        (
            "",
            "Фитнес-тренер отвечает за скорость/выносливость в тренировке; физиотерапевт"
            " — за реальный отдых и риск травмы. Их бонусы не складываются в мгновенное "
            "лечение. Соседние UTC-даты не гарантируют 24 часа отдыха; длительности и за"
            "морозка участников матча проверяются существующим recovery runtime.",
            "",
            "Подробные строки: `work/staff-training-calibration.csv`.",
        )
    )
    Path("docs/staff-training-calibration.md").write_text("\n".join(lines) + "\n")
    print(f"Calibration passed: {len(rows)} scenarios, maximum {maximum}/30000 bps.")


if __name__ == "__main__":
    main()
