# Турнирный блок: Долговечный runtime Кубка и матчей навылет (AND-123, AND-124, AND-125)

Документ ведёт журнал архитектурных решений, изменений в схеме БД и статуса подключения турнирного блока Goaletto.

---

## 1. Контекст и цели
В предыдущих волнах были разработаны и приняты чистые расчетные модули без БД и сети:
- `src/goaletto/domain/cup_bracket.py` (`cup-bracket-v1`): сетка Кубка 50 клубов, 49 пар, 97 матчей, поэтапный вход лиг tier 1..5 за 17 игровых циклов.
- `src/goaletto/domain/knockout.py` (`knockout-v1`): контракт двухматчевой пары и нейтрального финала, суммарный агрегат без выездного гола, переход в овертайм (10 тиков) и серия пенальти.
- `src/goaletto/domain/neutral_final.py` (`neutral-final-v1`): посещаемость нейтрального финала и равный раздел билетной выручки.

**Цель этого этапа:**
Перевести расчетные модули в долговечное состояние (PostgreSQL, календарь, воркер, финализация результатов):
1. Сохранение сетки Кубка (`cup_brackets`, `cup_ties`).
2. Генерация всех 97 `FixturePlan` на день 1 каждого четырехдневного цикла.
3. Бронирование тренировочных дат (`reserve_match_day`) для участников Кубка (прямых и условных).
4. Обобщение `Fixture` для кросс-лиговых матчей (`competition='cup'`, `stage`, `leg`, `neutral`, `league_id NULL`).
5. Поддержка материализации кубковых матчей в `calendar_planning.plan_horizon` на 10-дневном горизонте.
6. Финализация результатов матчей Кубка:
   - Обновление состояния пары в `cup_ties`.
   - Определение победителя через `domain.knockout.decide_tie`.
   - Продвижение победителя в зависимые слоты сетки и планы матчей (`FixturePlan.home_club_id / away_club_id`, очистка выбывших из `possible_club_ids`).
   - Начисление матчевой практики (`match-practice-v2`), опыта и формы участникам кубковых матчей.
   - Распределение билетного дохода финала (`AND-125`).

---

## 2. Журнал изменений схемы данных (Durable Schema)
- [x] Таблица `cup_brackets`:
  - `season_id` (PK, FK seasons.id)
  - `league_set_id` (FK league_sets.id)
  - `seed` (BigInteger)
  - `policy_version` (String, default='cup-bracket-v1')
  - `created_at` (DateTime)
- [x] Таблица `cup_ties`:
  - `id` (PK, UUID5 от season_id и structural key)
  - `season_id` (FK seasons.id, index)
  - `round_key` (String: prelim, entry-16, ..., final)
  - `round_label` (String)
  - `home_kind` ('club' | 'winner'), `home_club_id`, `home_tie_id`
  - `away_kind` ('club' | 'winner'), `away_club_id`, `away_tie_id`
  - `cup_days` (JSONB list)
  - `neutral` (Boolean)
  - `status` ('pending' | 'in_progress' | 'decided')
  - `winner_club_id` (UUID, FK clubs.id)
  - `first_match_result` (JSONB)
  - `second_match_result` (JSONB)
  - `extra_time_result` (JSONB)
  - `shootout_result` (JSONB)
  - `decided_at` (DateTime)
- [x] Модификация `fixtures`:
  - Добавление `competition` (String(16), default='league')
  - Добавление `stage` (String(80), nullable)
  - Добавление `leg` (SmallInteger, nullable)
  - Добавление `neutral` (Boolean, default=False)
  - Снятие NOT NULL с `league_id` для кросс-лиговых кубковых встреч
  - Замена FK `fk_fixture_home/away_membership` на `fk_fixture_home/away_season_club (season_id, club_id)`
  - Замена `uq_fixture_pair` на партиционированные уникальные индексы для лиги и других соревнований.
- [x] Модификация `match_results`:
  - Добавление `competition` (String(16), default='league')
  - Снятие NOT NULL с `league_id`
  - Замена FK на `(season_id, club_id)`.
- [x] Расширение `matches` и `match_ticks`:
  - `completed_tick BETWEEN 0 AND 40` (с поддержкой 10 тиков ET)
  - `phase` расширен на `extra_first_half`, `extra_half_time`, `extra_second_half`, `penalties`
  - `tick_number BETWEEN 1 AND 40`.

---

## 3. Выполненные шаги
* **2026-10-15 19:54:** Создана ветка `codex/tournament-cup-runtime` от актуального `main`.
* **2026-10-15 19:54:** Запущен полный предварительный прогон unit-тестов (1070 passed).
* **2026-10-15 20:01:** Реализована и применена миграция `0033_cup_tournament_runtime.py`. Обновлены модели в `src/goaletto/infrastructure/models.py`. Проверка `alembic check` подтвердила полное соответствие схемы.

