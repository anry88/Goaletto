"""Reproducible economy-v1 cash stress run; no database or image operations."""

from datetime import UTC, datetime
from pathlib import Path

from goaletto.domain.calendar import league_calendar
from goaletto.domain.finances import (
    INITIAL_CAPITAL,
    player_wage,
    season_prize,
    sponsor_income,
    stadium_upkeep,
)
from goaletto.domain.world import WorldOptions, build_world


def calculate() -> str:
    world = build_world(
        WorldOptions(
            key="economy-model", seed=0, first_kickoff=datetime(2026, 10, 17, 18, tzinfo=UTC)
        )
    )
    clubs = world.leagues[4].clubs
    fixtures = league_calendar(
        season_id=world.season_id,
        league_id=world.leagues[4].id,
        clubs=tuple(c.id for c in clubs),
        first_kickoff=world.options.first_kickoff,
        seed=0,
    )
    wage = max(sum(player_wage(p.skills[:8]) for p in c.players) for c in clubs)
    rows = []
    for label, staff, investment, extra_wage, capacity, attendance, academy in (
        ("Состав без штаба", 0, 0, 0, 5000, 2500, 0),
        ("Главный тренер", 50000, 0, 0, 5000, 2500, 0),
        ("Тренер, игрок 100K, стадион 300K", 50000, 400000, 1000, 10000, 2500, 0),
        ("То же, пустые трибуны", 50000, 400000, 1000, 10000, 0, 0),
        ("Главный и специалист, оба вложения", 70000, 400000, 1000, 10000, 2500, 0),
        ("Будущая академия + оба вложения", 50000, 400000, 1000, 10000, 2500, 10000),
    ):
        minima, endings, arrears = [], [], []
        for club in clubs:
            cash = INITIAL_CAPITAL - investment
            minimum = cash
            first_debt = None
            for week in range(1, 21):
                # Stress ordering: all bills precede sponsor and match money.
                cash -= wage + staff + extra_wage + stadium_upkeep(capacity) + academy
                minimum = min(minimum, cash)
                if cash < 0 and first_debt is None:
                    first_debt = week
                cash += sponsor_income(5, 10) + 2500
                if week <= 18 and any(
                    f.round_number == week and f.home_club_id == club.id for f in fixtures
                ):
                    cash += attendance * 5
                if week == 18:
                    cash += season_prize(5, 10)
            minima.append(minimum)
            endings.append(cash)
            arrears.append(first_debt)
        rows.append(
            f"| {label} | {min(minima):,} | {min(endings):,} | "
            f"{min((n for n in arrears if n is not None), default='—')} |"
        )
    reserve = 2 * (wage + 50000 + 1000 + stadium_upkeep(10000))
    required = (400000 + reserve) * 110 // 100
    return (
        f"""# Расчёт экономики economy-v1

Стартовый капитал: **{INITIAL_CAPITAL:,}** игровых единиц на клуб. Это баланс закрытого
теста, коэффициенты подлежат пересмотру по результатам игр.

Базовый состав: 20 футболистов, зарплата **{wage:,}/неделю**. Контракт = 100 + 10 × сумма
восьми игровых навыков; ставка фиксируется при подписании. Лидерство и лояльность не
увеличивают зарплату. Главный тренер 50K, специалист 20K, физиотерапевт 30K — из GDD;
скаут 20K — временный параметр.

Спонсор Tier 5: 70K + 1–10K за место. Нижняя граница выводится из расходов тренер+базовый
состав+стадион, а не подгоняется под стартовую сумму: 16,270 + 50,000 + 3,000 − 2,500 мерч
− 1,000 бонус места = **65,770**. Округление вверх до 70K сохраняет положительный
недельный поток даже без домашних билетов. Для tiers 4..1 базовая часть умножается на
2..5.

Билеты: 5 за зрителя из зафиксированной матчем посещаемости, только хозяевам; расширение
само по себе не создаёт болельщиков. Мерч: 1 за болельщика в неделю. Содержание стадиона:
2,000 + вместимость/5. Призы мест 1..10: 250/180/130/100/80/60/50/40/30/20K × (6−tier),
однократно после полного завершения лиги. Эти отсутствовавшие в GDD коэффициенты — единая
версия economy-v1.

Капитал покрывает игрока по минимальной цене 100K, первое расширение за 300K, две недели
зарплат состава+нового игрока+главного тренера и содержания: резерв **{reserve:,}**. Плюс
10% запас: **{required:,}**, округление вверх до **600K**. Найм и рынок пока не дают UI
расходования этих вложений; они включены в расчёт будущей стратегии, а не списаны с
клубов.

Проверены все десять клубов Tier 5, фактический двухкруговой календарь (9 домашних, 9
выездных), 18 недель сезона и две межсезонья, постоянно последнее место, расходы перед
доходами. Стартовая фанбаза 2,500; посещаемость постоянная или нулевая в стресс-сценарии.
Отрицательная цифра здесь означает недостающую ликвидность: в игре она отражается
отдельной задолженностью, cash остаётся неотрицательным.

| Сценарий | Минимум до доходов | Остаток после 20 недель | Первая нехватка, неделя |
|---|---:|---:|---:|
"""
        + "\n".join(rows)
        + """

Полный дорогой штаб вместе с двумя вложениями требует выбрать приоритеты, дополнительный
доход или отложить покупку. Академия в последней строке — прогноз: её содержание не
начисляется до появления работающей академии. При её запуске модель капитала должна быть
пересчитана.

В игре поступления одной недели применяются до обязательных счетов. Нехватка создаёт
задолженность, новые поступления гасят её; резервы ставок не расходуются на счета. Нет
автоматического отъёма клуба или неутверждённых штрафов морали. Призы не используются как
гарантия платёжеспособности до окончания лиги.

Воспроизведение: `.venv/bin/python scripts/calculate_economy.py`.
"""
    )


if __name__ == "__main__":
    Path("docs/economy-calculation.md").write_text(calculate(), encoding="utf-8")
