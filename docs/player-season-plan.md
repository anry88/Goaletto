# Чистый план сезона футболиста

Чистая расчётная часть AND-64/57: `src/goaletto/domain/player_season_plan.py`.
Функция только считает переход одного футболиста через границу сезонов.
Сохранение, блокировки, отмена аукционов, молодёжное пополнение и общий
rollover — ответственность вызывающего application-кода (Codex).
Шкала вероятности не дублируется: решение принимает существующая
`domain.retirement.player_last_season` (5/10/20/35/55/80/100% для 32–38);
`domain.retirement.py` не менялся. Переходы клубов считает существующая
`domain.leagues.plan_season_movements`; здесь они не затрагиваются.

## Публичный API

```python
from goaletto.domain.player_season_plan import (
    PlayerSeasonEntry,   # frozen kw-only input
    PlayerSeasonPlan,    # frozen kw-only output, он же форма receipt
    PlayerSeasonOutcome,  # Literal: continue_active | continue_last_season | retire_now | already_retired
    PlayerStatus,        # Literal: active | last_season | retired
    plan_player_season(entry, *, prior=None) -> PlayerSeasonPlan,
)
```

Вход `PlayerSeasonEntry`:

| Поле | Тип | Смысл |
|---|---|---|
| `person_id` | UUID | Футболист; echo без изменений |
| `appearance_identity_id` | str 1–100 | Внешность; echo без изменений, не переназначается |
| `completed_season_id` | UUID | Завершившийся сезон; должен отличаться от следующего |
| `next_season_id` | UUID | Следующий сезон |
| `current_age` | int 17–38 (`bool` запрещён) | Возраст, начисленный за завершённый сезон |
| `current_age_applied_for_season_id` | UUID \| None | Чей сезон покрывает возраст; у не-retired только completed/next |
| `current_status` | active/last_season/retired | Текущий `Player.status` |
| `current_last_season_id` | UUID \| None | Флаг последнего сезона |
| `current_retired_after_season_id` | UUID \| None | Сезон ухода |
| `draw` | int 0–9999 (`bool` запрещён) | Явная выборка вызывающего кода; вне продолжения активного игнорируется, но проверяется |

Статус и refs обязаны быть согласованы (как `ck_player_retirement`):
active — оба None; last_season — только `last_season_id`;
retired — оба заданы и равны. Повреждённый ввод отклоняется, молча не чинится.

Выход `PlayerSeasonPlan`: ключи (`person_id`, identity, оба сезона), `outcome`,
снимок до (`previous_*`), снимок после (`next_*`: возраст, сезон возраста,
статус, оба refs) и `decision` (`RetirementDecision` с policy version либо
`None`, когда новый draw не брался). Архивные снимки не переписываются;
события/outbox/UUID-генерация/random/часы/IO отсутствуют.

## Правила переходов

- Предупреждённый сезон завершён (`last_season`, флаг = completed):
  `retire_now` — статус retired, оба refs = completed, возраст сохранён,
  `decision=None`, нового активного сезона нет. Объявление — не пенсия.
- Остальной активный (17–37, возраст покрывает completed): +1 год,
  `player_last_season(возраст+1, draw)`. Положительное решение назначает
  **весь следующий сезон** последним (`continue_last_season`), иначе
  `continue_active`. Возраст 38 всегда последний (draw не хранится);
  активный 38/39 невозможен и отклоняется.
- Retired: `already_retired` — возраст, статус и refs не меняются,
  `decision=None`, сезон возраста не трогать (`None`).
- Владение клубом не входит во вход: owned и unowned планируют одинаково.
  Рост/деградация навыков, мораль, staff, молодёжка, портреты и отмена сделок
  не входят в функцию.

## Повторы

Свежий вызов требует pre-образ (для продолжения — возраст ещё за completed;
пост-образ без receipt отклоняется с требованием передать `prior`).
Повтор передаёт принятый план как `prior`: отличающийся `draw` игнорируется,
возвращается `prior` без изменений (`is` тот же объект), если вход совпал
с хранимым pre-образом либо с уже применённым post-образом.
Чужой person/identity/сезоны либо состояние, не совпавшее ни с тем, ни
с другим, — `ValueError`, а не мнимый успех.

## Контракт вызывающего кода (транзакционный rollover)

Чистая функция идемпотентность транзакции не гарантирует. Вызывающий обязан:

1. Держать блокировку футболиста (`player_locked`-стиль) весь переход.
2. Хранить receipt с уникальным ключом `(person_id, completed_season_id, stage)`:
   для продолжения — строки `age_applied` + `player_last_decision` за **next**
   сезон (возраст, вероятность, draw, outcome, policy version);
   для `retire_now` — строку `player_retired` за **completed** сезон.
   Подойдёт существующий `uq_person_season_stage` в `person_season_events`.
3. На retry читать сохранённый receipt, строить из него `prior` и передавать
   вместе со входом; несовпадение ключей — отказ, а не пересчёт.
4. Применять `next_*` идемпотентно: пропуск записи при совпадении с текущим
   состоянием; для `already_retired` записей нет.
5. Отмену аукционов/резервов, пополнение из молодёжки, outbox и смену календаря
   выполнять в той же транзакции отдельно — функция их не делает.
6. Никогда не перебрасывать draw ради разблокировки сделки: положительное
   решение необратимо обычным повтором.

Функция к игре не подключена; входы из БД в неё пока никто не строит.
