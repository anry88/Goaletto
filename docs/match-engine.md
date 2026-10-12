# Матчевый движок regulation-v1 · AND-51

Реализована чистая симуляция без Telegram, SQLAlchemy, сети и чтения часов.
`start_match`, `advance_tick`, `start_second_half` получают явные snapshots/state,
commands, seed и policy. Это работающий расчёт матчей, а не подключённый live-worker.
Календарные jobs пока остаются held; следующий слой — AND-52.

## Запуск и восстановление без БД

```bash
python -m goaletto.application.match_cli --seed 7 --output work/match.json
python -m goaletto.application.match_cli --seed 7 --ticks 17 --output work/checkpoint.json
python -m goaletto.application.match_cli --resume work/checkpoint.json --output work/resumed.json
```

После установки пакета доступна `goaletto-match`. Demo использует два фиксированных
клуба стартовой модели, симуляция выполняется сразу без 33 минут ожидания.
Никакие БД/контейнеры/боты не создаются; `.env` не читается. Параметр --ticks позволяет
остановиться на известном регламентном тике; --resume возвращает оставшуюся часть.
Отчёт содержит полный immutable snapshot/state, hash и события данного прогона.
У resumed report лог только оставшихся событий; финальный state совпадает с непрерывным.

Golden seed 7: Янтарь — Маяк 1:2, tick 30/90′, 57 событий, SHA-256:
`ae4002fb500d7769e280a9939d3f345f78e935b6c1b1be6a3f0bb792850c6841`.
Из checkpoint после тика 17 получаются те же последние 27 событий и финальный hash.

## Снимок и состояние

Frozen dataclasses хранят клубы/людей, их стартовые навыки/форму/возраст, последний
сезон, позиции и доступность, фиксированную XI/bench, тактику и менталитет.
Опционально pin appearance identity/variant/catalog; engine не читает графику и
не меняет эти поля. Возраст/пенсия/клубные навыки не обновляются от матчевых тиков.

Старт требует 11 разных доступных людей и ровно одну позицию GK. Bench и XI
полностью покрывают до 20 snapshot players; разные команды не делят одного person.
Из исходного snapshot получается immutable TeamState: lineup, unused bench,
removed people, energy/minutes/cards/injuries, statistics и командные лимиты.
Эти три группы людей всегда составляют исходный roster без дублей; снятый или
удалённый игрок не возвращается в матч. Состояние входа никогда не мутирует.

`match_codec` сохраняет закрытый JSON со всеми полями, enum/UUID и целыми счётчиками.
Unknown fields/version, неверные ranges, расходящиеся goals/shots/saves/possession,
неверные lineup/conditions отклоняются. Hash canonical JSON связывает replay/state.
Контроль version — обязательный; обновление реализации не должно молча доиграть
матч другой формулой. Текущая версия engine regulation-v1, policy match-policy-v1.

PRNG создаётся из SHA-256(version, match_id, persisted seed, tick, named stream).
Отдельные streams для количества/времени шансов, дуэлей, происшествий и игроков.
Никакого глобального random или скрытой позиции PRNG в памяти. При одинаковых
state/упорядоченных commands результат и event IDs одинаковы после рестарта.
Event IDs — UUID5(match, stage/index), command effects привязаны к command ID.

## Тактический расчёт и моменты

Рейтинги целочисленные, 1000 единиц на исходный skill point. Эффективность включает
form/8, energy, мораль и знакомство с назначенной позицией: 100%, 85% alternative, 60% unknown.
Семь секторов: midfield; center/left/right attack и defence; GK учитывается в дуэли.
Есть вклад playmaking/pace/winger и ограниченный experience bonus защитникам.

Поддержаны normal, pressing, counter, wings, defence, long_balls и пять менталитетов.
Модификаторы соответствуют направлению GDD: pressing +10% midfield/−5% defence,
wings +20% wing attack/−10% center, defence +15% defence/−15% attack; counter
получает +15% attack после сохранённого interception. Long balls использует
нейтральный chance weight вместо собственного midfield share. Wings выбирает
фланг в 70% направлений вместо стандартных 40%. Менталитет — отдельный modifier.

Нормальная λ=0.8 за тик: суммарно около 24 primary moments за 30 тиков. Старое
число 1.5/~24 из GDD относилось к прежнему количеству тиков и не переносится буквально.
Clipped Poisson даёт 0–3 primary chances. Владение по midfield делит шансы между
сторонами; обработка разбита на три внутренние игровые минуты. Это не добавляет
реальных worker ticks: один внешний переход всё ещё 60 секунд/3 game minutes.

Дуэль: attack/defence шум 80–120%; если defence выигрывает — interception.
После выигранной дуэли 13% wide, 3% post, остальное on target. GK save chance:
`clamp(4500 + effective_GK_rating/4 − quality_bps/8, 2000, 9700)` в basis points.
Это версия начального баланса, не окончательная калибровка. Shot quality зависит
от разницы powers. On-target всегда согласуется с goal либо save противника.
Из save/interception/post с 8% возможен corner и один дополнительный set-piece chance;
дополнительный corner из него не генерируется, бесконечной рекурсии нет.

## Болельщики, стадион и мораль

`HomeCrowd` фиксирует вместимость и фактическое количество домашних болельщиков
до kickoff; `0 <= home_fans <= stadium_capacity`. Вместимость ограничивает
посещаемость, сама по себе не прибавляет силу. Бонус: `12 × min(home_fans, 10000) // 10000`
пунктов морали; потолок и число болельщиков для полной поддержки находятся в policy.
2 500 дают +3, 5 000 дают +6, 10 000 и выше дают +12. Пустые трибуны дают 0.
Размер базы фанатов и прогноз посещаемости рассчитываются вне чистого engine; нельзя
приравнивать вместимость к посещаемости. Экономика/рост базы/спрос — AND-63.

У футболиста исходная morale 0–100. Матчевая мораль: `min(100, morale + support)`,
множитель эффективности: `0.75 + 0.005 × match_morale` (при 50 нейтральный 1.0).
Поддержка действует на домашнюю команду, включая GK и вышедших запасных, через навыки
и рейтинги, без присуждения голов или изменения исходной постоянной морали.
Снимок стадиона/посещаемости, policy и бонус остаются в checkpoint для точного replay.
Demo: capacity 5 000, home attendance 2 500 — явная проба баланса, не рассчитанный
спрос и не реализация строительства стадиона. По умолчанию без входного crowd
engine считает матч без зрителей.

## Усталость, карточки и травмы

Каждую сыгранную game minute расход: `max(10, 50−2×stamina)` energy basis points.
Pressing расходует +35%, ultra attack +15%. Energy ограничена снизу 10%; bench
не устаёт, новый игрок получает только фактически сыгранные минуты. Перерыв
однократно восстанавливает 2% активным людям, без сброса всего запаса.

На команду/тик: baseline yellow 3%, direct red 0.3%, injury 1%, отдельный обычный
foul 18%. Pressing повышает интенсивность, усталость немного повышает injury risk.
Две yellow дают red. Удалённого не заменять. Для injury выбирается здоровый резерв
по фиксированному порядку main position, затем alternative; без OVR оптимизации.
Такая замена расходует общие лимиты 5/3. Если нет подходящего резерва/лимита,
травмированный выходит, состав сокращается. Замены здоровых ботами не выполняются.

При недоступном GK и невозможности обычной injury replacement первый оставшийся
field slot становится emergency GK с реальным голкиперским skill/позиционным штрафом.
Это технический fallback, не выдача бесплатной замены после red. Если игроков <7,
матч переходит в abandoned с reason insufficient_players и текущим счётом поля.
Engine не присуждает 3:0, очки или победителя: административный результат отдельный.
Такой остановленный state не может продолжить симуляцию; worker/таблицы должны
обрабатывать abandoned отдельно от сыгранных 90 минут.

## Команды и таймлайн

Domain принимает уже проверенные application команды в устойчивом серверном порядке.
Он не проверяет Telegram owner/deadline — это следующий слой. Substitution/tactic/
mentality применяются только к явно указанному next tick. Command ID processed
однократно; устаревшая команда возвращает bounded rejection, без изменения состояния.
Не больше 5 замен, 3 live windows; несколько замен одного тика — одно окно.
Halftime batches не расходуют windows. Удалённый/снятый/недоступный человек не входит
в поле. Три tactic changes, mentality отдельно без такого лимита. Индивидуальные
инструкции и Telegram управление остаются в AND-53.

После tick 15 (45′) phase half_time. `advance_tick(...,16)` запрещён, пока
`start_second_half` не обработает halftime commands/rest и явный переход. Повтор
этого перехода не восстанавливает энергию снова. Worker обязан вызвать его в 18:00;
tick 16 due в 19:00, tick 30/full_time в 33:00. Domain не читает часы.
Повторный/пропущенный/out-of-order tick отклоняется; PG unique tick гарантирует
сохранение events только один раз при подключении runtime.

## Проверка

- Golden seed/replay, JSON checkpoint после 1/14/15/16/23/29 тиков.
- Full/half-time boundaries, duplicate tick, input immutability, no invalid player reentry.
- Five substitutions/three windows, halftime exemption, tactic vs mentality counters.
- Fatigue/form/position penalties, bench exposure, forced injuries/direct red/second yellow.
- Closed codec, invalid version/field/energy/score rejection.
- 200 balanced matches с 2 500 домашними болельщиками: mean goals 2.14, moments 25.22,
  home/away/draw 78/56/66, 25 goalless, максимум 7 голов.
  Та же серия без поддержки: 67/67/66, mean goals 2.13.
  С 7 500 болельщиками: 109/40/51; с 10 000: 113/33/54. Рост нелинейный: это
  начальная калибровка на одной паре команд, а не гарантированная вероятность победы.
  116 injuries, 115 automatic injury replacements.
- Extreme 18-skill vs 7-skill: 100 пробных побед сильной команды, mean total goals 7.92.
  Это регрессионный контраст, не оценка вероятности/качества финального игрового баланса.

Серии детерминированны и выполняются без БД. Runtime, сохранение результата/таблиц,
accepted commands, actual UTC times, outbox и Telegram delivery — следующие задачи.
Эта работа не запускает HDC, polling или held jobs.
