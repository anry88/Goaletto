"""Reproduce economy-v2 cash, scout and daily-load scenarios without a database."""

import csv
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from goaletto.domain.calendar import league_calendar
from goaletto.domain.finances import (
    INITIAL_CAPITAL,
    POLICY_VERSION,
    STAFF_RATES,
    merchandise_income,
    player_fee,
    player_wage,
    scout_mission_fee,
    scout_signing_fee,
    season_prize,
    sponsor_income,
    stadium_upkeep,
    staff_fee,
    staff_wage,
    ticket_income,
)
from goaletto.domain.recovery import daily_recovery, recover
from goaletto.domain.training import (
    PROGRESS_KEYS,
    SKILLS,
    Focus,
    Intensity,
    TrainingPlayer,
    initial_caps,
    train_player,
)
from goaletto.domain.world import WorldOptions, build_world


def calculate() -> str:
    world = build_world(
        WorldOptions(
            key="economy-model", seed=0, first_kickoff=datetime(2026, 10, 19, 18, tzinfo=UTC)
        )
    )
    skills = (10,) * 8
    weak = staff_wage("head_coach")
    coach = staff_wage("head_coach", 5, 50)
    specialist = staff_wage("defence_coach", 5, 50)
    scout = staff_wage("scout", 5, 50)
    young_fee = scout_signing_fee(skills, 19, "MC")
    mission = scout_mission_fee(5, 4)
    # Includes a free starting weak coach as required by AND-109's future implementation.
    scenarios = (
        ("Базовый тренер", weak, 0, 0, 0, 2500, 0, 0),
        ("Специалист Q2", weak + specialist, staff_fee("defence_coach", 5, 50), 0, 0, 2500, 0, 0),
        (
            "Тренер Q3",
            staff_wage("head_coach", 9, 50),
            staff_fee("head_coach", 9, 50),
            0,
            0,
            2500,
            0,
            0,
        ),
        (
            "Тренер Q2 + специалист + игрок",
            coach + specialist,
            staff_fee("head_coach", 5, 50)
            + staff_fee("defence_coach", 5, 50)
            + player_fee(skills, 26, "MC"),
            player_wage(skills, 26, "MC"),
            0,
            2500,
            0,
            0,
        ),
        (
            "Один молодой Q10 через скаута",
            weak + scout,
            staff_fee("scout", 5, 50) + mission + young_fee,
            player_wage(skills, 19, "MC"),
            0,
            2500,
            0,
            0,
        ),
        (
            "Бывший игрок Q2 со скидкой",
            weak + staff_wage("defence_coach", 5, 36, loyal=True),
            staff_fee("defence_coach", 5, 36),
            0,
            0,
            2500,
            0,
            0,
        ),
        ("Повышение после сезона", weak, 0, 0, 0, 2500, -1, 0),
        ("Понижение после сезона", weak, 0, 0, 0, 2500, 1, 0),
        ("Базовый тренер, пустые трибуны", weak, 0, 0, 0, 0, 0, 0),
        (
            "Тренер Q2 + стадион",
            coach,
            staff_fee("head_coach", 5, 50) + 300000,
            0,
            5000,
            2500,
            0,
            0,
        ),
        ("20 молодых Q10 за оборот", weak + scout, staff_fee("scout", 5, 50), 0, 0, 2500, 0, 20),
    )
    details, table, base, wage_totals = [], [], [], []
    for league in world.leagues:
        fixtures = league_calendar(
            season_id=world.season_id,
            league_id=league.id,
            clubs=tuple(c.id for c in league.clubs),
            first_kickoff=world.options.first_kickoff,
            seed=0,
        )
        for label, staff, investment, extra, capacity, crowd, change, searches in scenarios:
            minima, endings, debts = [], [], []
            for club in league.clubs:
                wages = sum(player_wage(p.skills[:8], p.age, p.position) for p in club.players)
                wage_totals.append(wages)
                cash = INITIAL_CAPITAL - investment
                minimum, first_debt = cash, 0 if cash < 0 else None
                for cycle in range(1, 21):
                    search = 1 if cycle <= searches else 0
                    search_wage = player_wage(skills, 19, "MC") * min(cycle, searches)
                    remaining_wages = (
                        sum(
                            player_wage(p.skills[:8], p.age, p.position)
                            for p in club.players[min(cycle, searches) :]
                        )
                        if searches
                        else wages
                    )
                    out = (
                        remaining_wages
                        + staff
                        + extra
                        + search_wage
                        + stadium_upkeep(5000 + capacity)
                        + search * (mission + young_fee)
                    )
                    cash -= out
                    minimum = min(minimum, cash)
                    if cash < 0 and first_debt is None:
                        first_debt = cycle
                    tier = max(1, min(5, league.tier + (change if cycle > 18 else 0)))
                    # Conservative last-place contract, fixed fans. AND-117 persists renewal.
                    income = sponsor_income(tier, 10, 2500) + merchandise_income(2500)
                    if cycle <= 18 and any(
                        f.round_number == cycle and f.home_club_id == club.id for f in fixtures
                    ):
                        income += ticket_income(crowd, 5000 + capacity)
                    if cycle == 18:
                        income += season_prize(league.tier, 10)
                    cash += income
                    details.append(
                        dict(
                            tier=league.tier,
                            club=str(club.id),
                            scenario=label,
                            cycle=cycle,
                            expenses=out,
                            income=income,
                            cash=cash,
                        )
                    )
                minima.append(minimum)
                endings.append(cash)
                debts.append(first_debt)
            if league.tier == 5:
                table.append(
                    f"| {label} | {min(minima):,} | {min(endings):,} | "
                    f"{min((d for d in debts if d is not None), default='—')} |"
                )
            if label == "Базовый тренер":
                base.append(f"| {league.tier} | {min(minima):,} | {min(endings):,} |")
    required = (
        (
            max(player_fee(skills, 26, "MC"), 300000)
            + staff_fee("head_coach", 5, 50)
            + staff_fee("defence_coach", 5, 50)
            + 2
            * (
                max(wage_totals)
                + coach
                + specialist
                + player_wage(skills, 26, "MC")
                + stadium_upkeep(5000)
            )
        )
        * 110
        + 99
    ) // 100
    assert required <= INITIAL_CAPITAL
    Path("work").mkdir(exist_ok=True)
    with Path("work/economy-v2.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(details[0]))
        writer.writeheader()
        writer.writerows(details)
    rates = [
        f"| {role} | " + " / ".join(f"{x:,}" for x in rates) + " |"
        for role, rates in STAFF_RATES.items()
    ]
    liquidation = max(
        sum(player_fee(p.skills[:8], p.age, p.position) for p in c.players)
        for league in world.leagues
        for c in league.clubs
    )
    maximum_tier5_income = (
        20 * (sponsor_income(5, 1) + merchandise_income(2500))
        + 9 * ticket_income(2500, 5000)
        + season_prize(5, 1)
    )
    comparable_replacement = 20 * (mission + scout_signing_fee((12,) * 8, 19, "MC"))
    assert comparable_replacement > INITIAL_CAPITAL + liquidation + maximum_tier5_income
    runway = INITIAL_CAPITAL // (max(wage_totals) + weak + 3000)
    return f"""# Расчёт экономики {POLICY_VERSION}

Расчёт seed 0: 50 клубов, все 5 лиг, 20 четырёхдневных циклов (72 + 8 дней).
Все суммы целочисленные; зарплата фиксируется при подписании. Старт остаётся
**{INITIAL_CAPITAL:,}**. Достаточный запас для игрока Q10 26 лет ИЛИ стадиона 10K,
тренера Q2, специалиста Q2, двух циклов всех зарплат и 10% запаса: **{required:,}**.
Это доступные альтернативы; одновременная покупка игрока и стадиона не гарантируется.
Базовый состав получает {min(wage_totals):,}–{max(wage_totals):,} за цикл.

## Формулы новых предложений

Качество q = floor((3 × основной навык позиции + выносливость + скорость) / 5).
GK: вратарь; защитники/DM: защита; MC: пас; фланги: фланг; AMC/FC: атака.
Лидерство/лояльность не поднимают оценку. Все навыки 1..20.
Цена = max(100K, 2500 × q² × (1 + (28 − возраст) × 0.1)) × (1 + 0.15 × (позиций − 1)).
Зарплата = (100 + 15 × q²) × (1 + (28 − возраст) × 0.02) × (1 + 0.08 × (позиций − 1)).
Округление вниз после целочисленных множителей. Поддержаны 1–3 позиции;
генерация дополнительных позиций подключается в AND-108.
Q10: 26 лет — {player_fee(skills, 26, "MC"):,} / {player_wage(skills, 26, "MC"):,};
30 лет — {player_fee(skills, 30, "MC"):,} / {player_wage(skills, 30, "MC"):,}.
За 2/3 позиции: цена ×1.15/1.30, зарплата ×1.08/1.16.

| Профессия | Зарплата по уровням Q1 / Q2 / Q3 / Q4 / Q5, возраст 50 |
|---|---|
{chr(10).join(rates)}

Внутри уровня оплата одинакова. Возрастной множитель зарплаты сотрудника:
1 − (clamp(возраст, 50, 70) − 50) × 0.01. Цена найма: 4 зарплаты ×
max(0.3, 1.1 − (clamp(возраст, 50, 70) − 50) × 0.05).
Тренер Q2 в 50: {staff_fee("head_coach", 5, 50):,} / {staff_wage("head_coach", 5, 50):,};
в 56: {staff_fee("head_coach", 5, 56):,} / {staff_wage("head_coach", 5, 56):,}.
У бывшего игрока в родном клубе зарплата ×0.8; цена найма не уменьшается.
Смена кадровой карьеры и первое предложение родному клубу остаются AND-114.

## Доходы и денежный поток

Спонсор за цикл: 48K + (5 − tier) × 4K + (11 − место) × 500 + min(фанаты, 10K).
Мерч: 1 за фаната; билеты: 5 за зрителя; стадион: 2000 + вместимость / 5.
Призы мест 1..10: 100/80/65/55/45/38/32/27/23/20K × (6 − tier).
Приз приходит после 18-го цикла. Подписанный сезонный спонсор AND-117 ещё не подключён:
runtime пока использует текущую позицию; здесь консервативно последнее место весь сезон.
Продление на новую лигу в сценариях повышения/понижения — расчёт контракта AND-117.
Слабый стартовый тренер заложен в расходы; его выдача и лимиты матча — AND-109.

Проверены все 10 клубов каждого уровня, настоящий календарь 9 домашних/9 выездных,
2500 зрителей (или 0), нулевые трансферы на продажу, расходы перед доходами.
Отрицательный forecast — недостающая ликвидность, реальная игра хранит cash >= 0 и долг.

| Лига, базовый тренер | Минимум до доходов | Остаток после 20 циклов |
|---|---:|---:|
{chr(10).join(base)}

| Сценарий Tier 5 | Минимум до доходов | Остаток | Первая нехватка, цикл |
|---|---:|---:|---:|
{chr(10).join(table)}

Базовый клуб платёжеспособен даже без билетов; штаб Q3 и выше требует регулярного
дохода и резерва. Каждая покупка добавляет постоянные расходы. Рост дивизионных
доходов ограничен 4K за ступень, фанатская часть спонсора ограничена 10K;
старое умножение всей базы спонсора до ×5 убрано. Денежного cap/налога/штрафа нет:
бережливый клуб может копить; модель ограничивает скорость, не запрещает сбережения.
Консервативный запас без любых доходов: {runway} полных циклов.
Резервы ставок недоступны для покупок; поступления гасят задолженность.

## Скаут: дешёвый простой, дорогое омоложение

Уточнение пользователя: позиция и срок выбираются перед запуском. Сроки 4/8/12/16
дней, один итоговый отчёт в самом конце; еженедельных промежуточных находок нет.
Чем дольше поиск, тем больше кандидатов на выбор. Рабочий размер списка 1/2/3/4
соответственно; отдельная подпись и цена каждого кандидата. В клубе одно активное
задание; уволить/нанять скаута ради бесплатного перезапуска нельзя.
Скаутский runtime AND-116 ещё не включён; ниже финансовая модель для его реализации.

Q2/50 лет: простой {scout:,}/цикл, найм {staff_fee("scout", 5, 50):,}.
Модель цены задания: (100K + 5K × навык) × (дни / 4).
При навыке 5: 4 дня — {scout_mission_fee(5, 4):,}, 8 — {scout_mission_fee(5, 8):,},
12 — {scout_mission_fee(5, 12):,}, 16 — {scout_mission_fee(5, 16):,}.
Это оплата всего поиска до старта; подписания ещё не выбраны и оплачиваются отдельно.
Подписание:
max(100K, 2500 × q² × (1 + (21 − возраст) × 0.05)), отдельно от задания;
премия за 2/3 позиции ×1.15/1.30. Здесь q сохраняет шаг 0.2 по навыкам, без
округления до целого/трёх категорий. Например q10.0 и q10.2 имеют разные цены.
Мягкая доплата за молодость применяется один раз.
Q10/19 лет: {young_fee:,} + {mission:,} = {young_fee + mission:,}
({(young_fee + mission) * 100 // INITIAL_CAPITAL}% стартового бюджета),
затем {player_wage(skills, 19, "MC"):,}/цикл.
19 лет, скаут Q2: q6 — {scout_signing_fee((6,) * 8, 19, "MC") + mission:,} вместе с заданием;
q10 — {young_fee + mission:,}; q14 — {scout_signing_fee((14,) * 8, 19, "MC") + mission:,};
q18 — {scout_signing_fee((18,) * 8, 19, "MC") + mission:,}. Слабые и сильные находки
не стоят одинаково; качество/возраст определяют основную часть цены.
Обычный Q10/26: {player_fee(skills, 26, "MC"):,}.
Три последовательных задания без подписаний стоят {3 * mission:,}; отказ не возвращает цену.
20/25 подписаний того же качества: {(mission + young_fee) * 20:,} / {(mission + young_fee) * 25:,}
без зарплат; 25 дополнительно невозможно при последовательных заданиях:
максимум 20 кандидатов за 80 дней при списках 1/2/3/4 и одном активном поиске.
Длинный поиск выдаёт весь список в конце; старый лимит одной находки в день отчёта
не применять к пакету из 2–4, а бюджет/частоту считать за полную длительность задания.
Проверка перепродаж: даже гипотетическая продажа всех 20 исходных игроков по
оценке (максимум {liquidation:,}, включая неторгуемых пенсионеров) + 600K +
весь сезонный доход Tier 5 при первом месте ({maximum_tier5_income:,}) даёт лишь
{INITIAL_CAPITAL + liquidation + maximum_tier5_income:,} до любых зарплат.
20 находок q12, сопоставимых с основой, требуют {comparable_replacement:,} только
на задания/подписания. Обмен сильной основы на слабых q6 дешевле, но теряет качество.
В таблице массового обновления замещается зарплата старых игроков; денег от продаж
не предполагается. Это стресс стоимости, а не возможность обойти лимит состава.
Замена/повторный найм не сбрасывают лимит клуба. При старте задания фиксируются
оплаченный цикл и policy, при подписании — отдельный receipt; retries бесплатных
находок не дают. Это контракт и расчёт для AND-116: пользовательский поток скаута
ещё не включён, автоматических дорогих заданий нет.

## Нагрузка, кубки и развитие

{load_report()}
Кубковые билеты не нужны для платёжеспособности. Стресс дохода финала:
5000 × 5 / 2 = 12,500 каждому клубу. Матчи/допвремя/нейтральная касса
подключаются AND-123–126.
Выплаты кубковых призов не придуманы и в расчёт не включены.

## Переход

Новые quotes/зарплаты: economy-cycle-v2; предложение, подтверждение, signing receipt
и новый зарплатный контракт используют одну сохранённую версию и сумму. Аукционный
quote тоже хранит версию экономики независимо от auction-v1. Действующие ставки,
ledger и receipts не переписываются; старые четырёхдневные контракты продолжают
начисляться по своей ставке. Новые расчёты доходов используют v2; выполненный цикл
никогда не пересчитывается. Капитал 600K сохранён, дополнительного grant нет.
Неподписанные старые предложения сохраняют людей, возраст, лицо, прежнюю цену и v1:
это конечный legacy остаток, не новый источник молодёжи. Природные возвраты людей
сохраняются сверх лимита. Новая генерация создаёт только дефицит взрослых 26–30 лет.

Воспроизведение: `.venv/bin/python scripts/calculate_economy.py`.
Детальные 11,000 club-cycle строк: `work/economy-v2.csv`. Проверка не меняет DB.
"""


def load_report() -> str:
    rows = []
    for label, cup, champions in (("Ранний вылет", 2, 0), ("Два финала", 17, 17)):
        person_id = UUID(int=1)
        player = TrainingPlayer(
            person_id,
            27,
            "MC",
            dict.fromkeys(SKILLS, 10),
            initial_caps(person_id, dict.fromkeys(SKILLS, 10)),
            dict.fromkeys(PROGRESS_KEYS, 0),
            6,
            0,
        )
        energy, remainder, minimum, training_days = 10000, 0, 10000, 0
        for day in range(72):
            phase, cycle = day % 4, day // 4
            match = phase == 3 or (phase == 0 and cycle < cup) or (phase == 1 and cycle < champions)
            energy, remainder = recover(
                energy, remainder, 86400, daily_recovery(player.skills["stamina"], 27, 10)
            )
            if match:
                # Conservative whole-match load; every cup game includes extra time.
                energy = max(0, energy - (3574 if phase in (0, 1) else 2680))
            else:
                training_days += 1
                result = train_player(
                    player,
                    Focus.PHYSICAL,
                    Intensity.HIGH,
                    seed=f"economy:{day}",
                    bonus_bps=2000,
                    physio_skill=10,
                )
                # Ignore injuries here to upper-bound seasonal development.
                player = TrainingPlayer(
                    person_id,
                    27,
                    "MC",
                    result.skills,
                    player.caps,
                    result.progress,
                    result.form,
                    result.experience,
                )
                energy = max(0, energy - 220)
            minimum = min(minimum, energy)
        rows.append(
            f"{label}: тренировок {training_days}, прирост выносливости "
            f"+{player.skills['stamina'] - 10}, минимум энергии {minimum / 100:.2f}%."
        )
    return (
        "Каждый из 72 UTC-дней: матч либо одна тренировка; возраст 27, физиотерапевт 10, "
        "HIGH/физика, бонус 20%, скрытые потолки, дневной эффект train_player. "
        + " ".join(rows)
        + " Допвремя во всех кубковых играх — стресс, не среднее. "
        "Не гарантирует полную энергию при соседних датах: интервалы меньше 24h требуют ротации."
    )


if __name__ == "__main__":
    Path("docs/economy-calculation.md").write_text(calculate(), encoding="utf-8")
