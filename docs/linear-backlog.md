# Задачи Goaletto в Linear

[Проект Goaletto](https://linear.app/andrei-brazhnikov/project/goaletto-eb8cb486bc79) · 45 задач · 5 вех.

Linear — источник актуального статуса; реестр синхронизирован с выполненной работой 2026-10-12.
Запланированный gameplay не отмечается выполненным. [Бот-клубы и владение](bot-clubs.md),
[возраст и пенсия](player-lifecycle.md), [проверка качества портретов](portrait-feasibility.md),
[аналитика](analytics/measurement-plan.md), [ручной деплой](deployment-hdc.md).

## 01 · Основание и оформление

### [AND-39 · Создать Python-каркас, Docker Compose и CI](https://linear.app/andrei-brazhnikov/issue/AND-39/sozdat-python-karkas-docker-compose-i-ci)

Начальная структура aiogram / domain / application / infrastructure / worker; pyproject, закрытая LICENSE, README, gitignore, Alembic, lockfiles и origin git@github.com:anry88/Goaletto.git.

**Приёмка:** Запуск preview-бота и system.ping worker; ruff/mypy/pytest проходят; PostgreSQL migration upgrade/check проверены; исходный каркас опубликован в указанном репозитории.

**Зависимости:** нет.

### [AND-40 · Обновить GDD и зафиксировать правила минутных матчей и лиг](https://linear.app/andrei-brazhnikov/issue/AND-40/obnovit-gdd-i-zafiksirovat-pravila-minutnyh-matchej-i-lig)

Сохранить исходный GDD в архив; текущая версия описывает Python-only Telegram-бот, 60-секундные тики, 30+3 минуты и комплекты 5×10.

**Приёмка:** Нет старой пирамиды/стыковых/двухминутного тика как действующих правил; в таблице граничные исключения лиг 1 и 5; таймлайн заканчивается 90′ на 33:00.

**Зависимости:** нет.

### [AND-41 · Подготовить три направления оформления и возрастные примеры](https://linear.app/andrei-brazhnikov/issue/AND-41/podgotovit-tri-napravleniya-oformleniya-i-vozrastnye-primery)

Концепт-листы A Touchline, B Club Stickers и C Dugout: по 3 игрока и 3 сотрудника; сохранить исходные prompts и ссылки в документации.

**Приёмка:** Три изображения сохранены в assets/concepts, вымышленные лица, видны возраст и разнообразие; это концепты, не готовые production-слои.

**Зависимости:** нет.

### [AND-42 · Утвердить направление оформления Goaletto](https://linear.app/andrei-brazhnikov/issue/AND-42/utverdit-napravlenie-oformleniya-goaletto)

Пользователь выбрал C — Dugout 2026-10-11: ретро-футбольный журнал, бордовый, мох и тёплая бумага.

**Приёмка:** Решение записано в art direction и GDD; дальнейшие части лица и карточки развиваются в направлении C.

**Зависимости:** AND-41.

### [AND-43 · Создать и расширить пилот сборных лиц игроков и персонала](https://linear.app/andrei-brazhnikov/issue/AND-43/sozdat-i-rasshirit-pilot-sbornyh-lic-igrokov-i-personala)

Малый детерминированный SVG-комбинатор: отдельные возрастные банки, формы лица, глаза, носы, рот, волосы, одежда и детали персонала; после выбора C адаптировать палитру и добавить несколько вариантов.

**Приёмка:** 6 собранных SVG/PNG-портретов, recipes и статическая галерея без JavaScript; seed воспроизводим; старые component IDs не переиспользуются; валидны все пробные сочетания. Пилот явно отличается по детализации от иллюстрированного концепта.

**Зависимости:** AND-42.

### [AND-44 · Создать библиотеку уникальных цельных портретов Dugout](https://linear.app/andrei-brazhnikov/issue/AND-44/sozdat-biblioteku-unikalnyh-celnyh-portretov-dugout)

Пользователь выбрал цельные мужские портреты в стиле C — Dugout после отклонения качества комбинаций. Первый комплект: 50 клубов × 20 футболистов = 1 000 разных внешностей, без повторного appearance ID между людьми; отдельные уникальные сотрудники. Экспорт 256×256 / 96×96, одежда в двух текущих цветах клуба.

Подготовлен whole-v2: 1 000 портретов игроков и 80 портретов персонала, 54 исходных листа цельных людей, prompts, stable identity/variant IDs, маски одежды и локальный Python/Pillow renderer. 80 — ёмкость иллюстраций, не утверждённый размер нанятого персонала или стартового пула. Источники сохранены. Скрипт нарезает целые бюсты, сохраняет пропорции, убирает соседние фрагменты ниже плеч; лицо не собирается из черт.

После возрастного расширения 64 pytest passed, 2 PG integration skipped; план назначения покрывает всех 1 000 игроков без повторов и отказывает при исчерпании банка. Аудит всех 1 080 изображений проверяет смену цветов, alpha и неизменность пикселей вне маски. Точных дубликатов нет; галерея и ближайшие по форме пары предназначены для визуальной проверки, хеши не доказывают различимость всех лиц.

**Приёмка:**

- Визуальное согласование каталога; заменить артефакты и случаи потери личности; небольшое сходство допустимо.
- Подготовить узнаваемые возрастные варианты и player→staff варианты той же identity. Возрастные группы подготовлены частично; полный набор остаётся в работе; возраст в плане — ориентир изображения, не утверждённый seed состава.
- PostgreSQL reservation/unique constraints/locks и назначение person→appearance (AND-45/46); runtime planner не является игровой БД.
- Telegram file_id/cache и карточки (AND-48).
- Пополнение до новых комплектов и молодёжных замен; лица после пенсии не переиспользуются.

**Зависимости:** AND-43.

### [AND-73 · Зафиксировать контракт событий и метрики воронок Goaletto](https://linear.app/andrei-brazhnikov/issue/AND-73/zafiksirovat-kontrakt-sobytij-i-metriki-voronok-goaletto)

Закреплён контракт реализации v1 в application/analytics.py: закрытый envelope, name/origin/required-ref allowlist, стабильный operation/effect dedupe, UTC windows и различие человеческой активности/доставки/автоматики. Добавлен club_opened и отдельный allowlist обновления ownership; feedback и action_rejected не продлевают владение. docs/analytics/contract-v1.md фиксирует определения трёх KPI, D1/D3/D7/D30, зрелость, product n/N examples, version rollout, coverage_start, доступы и стартовую retention policy. Сбор/сессии/cleanup/SQL не реализованы; это AND-74/76/79/82.

**Приёмка:** Contract validation и временные границы проверены unit-тестами без БД. manager_started допускается до claim; origin automation/system/operator не создаёт человеческий успех; internal исключается. Delivered не read; window maturity/late arrival и ownership interruption описаны. Политика хранения: events/attempts 180 дней, comments 30 дней, агрегаты 24 месяца; конфигурируемая и versioned, cleanup ещё в плане. Метрики имеют n/N и UTC-окна; неизвестные данные не считаются нулём.

**Зависимости:** AND-40.

## 02 · Мир и вход в игру


Дополнение: 87 возрастных листов = 1 740 вариантов; 240 игроков имеют все 7 групп, ещё 20 — 3 группы. План полного расширения — 7 240 вариантов. Встроенный Imagegen достиг суточного лимита; остаётся 275 листов. Сброс 12 октября 10:19 UTC / 12:19 Белград. Список недостающих листов и prompts сохранены в whole-v2/lifecycle-v1. Локальный selector и галерея готовы; PG/карьеры/Telegram cache относятся к другим задачам. AND-44 остаётся In Progress.

### [AND-45 · Разработать доменную схему игроков, сезонов и матчей](https://linear.app/andrei-brazhnikov/issue/AND-45/razrabotat-domennuyu-shemu-igrokov-sezonov-i-matchej)

Реализовано основание данных: managers/analytics IDs/ownership, persons/appearance identities и варианты, player/staff profiles/qualifications/lifecycle events, seasons/memberships/standings, fixed lineups, fixtures/matches/ticks/accepted commands. Новая миграция 0002_game_foundation добавляет 19 таблиц, всего 24. PostgreSQL-граница назначения лица использует transaction advisory lock, unique reservation и stable creation key; exhausted catalog даёт ошибку. ERD и порядок транзакционных блокировок: docs/data-model.md. Рецепт и варианты одной identity сохраняются при старении/второй карьере; случайная подмена лица запрещена. Схема не выполняет seed, claim, кадровую механику или матчи.

**Приёмка:** Приёмка текущего этапа по указанию пользователя без тестовых БД: Ruff/format/mypy прошли; 96 unit tests passed, 2 integration deselected; offline upgrade/downgrade и соответствие нового DDL моделям проверены. Telegram ID BIGINT, деньги integer; unique club owner/season/tick/command/person-face, composite refs и append-only history задокументированы. Исполнение SQL, alembic check и PG concurrency не выполнялись и не считаются проверенными. CI не создаёт БД. Свободный staff home calendar, межкомплектное старение, staff probability policy и role eligibility остаются в соответствующих gameplay задачах.

**Зависимости:** AND-39, AND-40, AND-73.

### [AND-46 · Создать первый комплект пяти лиг и 50 бот-клубов с близкими составами](https://linear.app/andrei-brazhnikov/issue/AND-46/sozdat-pervyj-komplekt-pyati-lig-i-50-bot-klubov-s-blizkimi-sostavami)

Реализованы domain.world/build_world, application.world/initialize_world и CLI goaletto-world. Воспроизводимый комплект: 5 лиг × 10 клубов × 20 футболистов, все без владельцев; fixed 4-4-2/bench с запасным GK, сопоставимые профили навыков без tier handicap, одинаковый общий budget parameter. PostgreSQL seed использует общий allocation transaction lock, immutable receipt/config hash, 1000 уникальных person/face reservations и отказ при исчерпании age-compatible каталога. Repeat receipt возвращает прежние IDs и не сбрасывает финансы/лица/возраст/составы/владение после матчей. Стартеры 32–38 получают persisted first-season retirement decision; 38 всегда последний. Шкала staff и стартовый staff pool не вводятся.

**Приёмка:** Проверен offline preview: 5 лиг, 50 клубов, 1000 игроков/лиц, 450 fixtures и 15302 held jobs. Core skill mean 8.91875, spread 0; это proxy начального баланса, не окончательный OVR. 134 unit tests passed, 2 integration deselected; Ruff/format/mypy и offline upgrade/downgrade прошли. SQL-recording проверяет compiled batches, unique faces, held jobs и receipt последним; retry не перестраивает мир. По указанию пользователя новые БД не создаются: реальный PostgreSQL seed/concurrency/rollback и --apply не выполнялись. Документация docs/world-initialization.md. Реализованный seed не означает runtime матчей, claim или HDC deploy.

**Зависимости:** AND-45.

### [AND-47 · Реализовать выдачу клубов только лиги 5 и расширение мира](https://linear.app/andrei-brazhnikov/issue/AND-47/realizovat-vydachu-klubov-tolko-ligi-5-i-rasshirenie-mira)

Каждый новый комплект содержит 50 × 20 = 1 000 новых футболистов с новыми уникальными внешностями. Нельзя повторять лица первого комплекта или освобождать ID после пенсии. Проверить запас каталога до создания населения и пополнять его офлайн заранее; повторный запуск не создаёт частичного мира или случайных назначений. Первый whole-v2 покрывает только первый комплект; ресурс для роста мира и молодёжных замен требует отдельного пополнения.

Onboarding показывает только свободные бот-клубы уровня 5 во всех комплектах. При отсутствии мест атомарно создать новый полный комплект 5×10.

**Приёмка:** Один Telegram ID — один клуб; параллельный claim последнего места не делит клуб/не создаёт лишние комплекты; верхние лиги недоступны новичкам; текущие финансы/таблица клуба сохраняются. Приёмка аналитики: первый игровой /start и источник фиксируются до claim; club_claimed атомарен с владением, retry/race не удваивают событие. Internal/автоматика отделены. Полнота воронки проверяется в AND-76.

**Зависимости:** AND-46, AND-74, AND-75.

### [AND-48 · Создать Telegram-навигацию, карточки и пагинацию](https://linear.app/andrei-brazhnikov/issue/AND-48/sozdat-telegram-navigaciyu-kartochki-i-paginaciyu)

Главное меню и подменю; стабильные Назад/Главное меню, списки/карточки игроков, персонала и клубов, пагинация и стиль C — Dugout.

Карточки используют локальный цельный PNG, pinned appearance identity/variant/catalog version и два текущих цвета клубной формы. Размеры 256×256 и 96×96, отдельные мужские player/staff банки. Подготовленный whole-v2 содержит 1 000 игроков/80 staff, визуальная приёмка в AND-44; прежний компонентный r1 отклонён и в карточки не подключается. Для бывшего игрока нужен staff/age variant того же лица, случайная замена запрещена. Полные возрастные/карьерные варианты ещё не готовы.

**Приёмка:**

- Все действия через Telegram-кнопки/команды без Mini App; быстрый answerCallbackQuery, безопасные устаревшие callbacks, HTML-экранирование имён.
- Сила, возраст, роль, форма и статусы доступны текстом, а не только цветом или изображением.
- «Последний сезон» / «Завершит карьеру после сезона N» в карточке, составе и preview клуба до claim, весь предупреждённый сезон. Убрать CTA продажи, дать причину; stale callback отклоняется application guard.
- Retired карточка остаётся в истории без игровых действий; бывший игрок сохраняет person/history/age при новой карьере.
- Telegram file_id/cache: pinned identity + variant + catalog version + kit colors/version + size/background. Исторические snapshots сохраняют тогдашние возраст и форму.
- Смена цвета сохраняет alpha/лицо и действует только по garment mask. Нет Imagegen или сетевых генераций при показе.
- Outbox-уведомление перед последним сезоном без дублей.
- Аналитика отделяет явный переход/нажатие от доставки экрана; конечные reason_code ошибок/устаревших кнопок, контекстная помощь и recovery без raw callback/текста. Контракт AND-73, e2e AND-76.

**Зависимости:** AND-39, AND-42, AND-74.

### [AND-49 · Реализовать состав, схему и предматчевую тактику](https://linear.app/andrei-brazhnikov/issue/AND-49/realizovat-sostav-shemu-i-predmatchevuyu-taktiku)

Выбор схемы, позиции, стартовой XI, скамейки, капитана и тактики через кнопки; штрафы за неподходящую позицию; блокировка за минуту до матча.

**Приёмка:** Только владелец меняет состав; нельзя задублировать игрока/выставить травмированного; корректные 11 позиций и вратарь; сохранение в БД; автосостав при отсутствии выбора. Уточнение 2026-10-11: Выбор XI/скамейки показывает метку «Последний сезон», не запрещая ещё играющего ветерана. Retired player исключён после сезонного ухода, сервер перепроверяет статус на commit, старый callback не возвращает его в состав. Метка из сохранённого решения, не новая жеребьёвка. docs/player-lifecycle.md.

**Зависимости:** AND-45, AND-47, AND-48.

### [AND-50 · Сохранять клуб шесть месяцев неактивности, напоминать и освобождать](https://linear.app/andrei-brazhnikov/issue/AND-50/sohranyat-klub-shest-mesyacev-neaktivnosti-napominat-i-osvobozhdat)

Решение пользователя 2026-10-11: владение сохраняется шесть календарных месяцев от последнего явного игрового действия владельца; claim — начальная дата. Новое действие переносит дедлайн. Автоматические матчи, доставка и jobs не продлевают владение. Периодические уведомления «Ваш клуб ждёт вас». После истечения клуб полностью бот и доступен новичку только в лиге 5. Сроки подхвата 2 недели/освобождения 4 недели отменены.

**Приёмка:** Durable ownership period/last_game_action_at/deadline; шесть календарных месяцев, не 180 дней, конец месяца ограничивается допустимой датой. Return vs release атомарно перепроверяет owner/deadline и не отбирает активный клуб; retry не повторяет release. Состав/финансы/история сохраняются. Upper-tier released club не выдаётся новичкам; tier5 claim по обычным правилам. Reminders via outbox с dedupe period+interval, ограниченной настраиваемой частотой, opt-out/unavailable и проверкой актуальности перед отправкой; доставка не считается действием. Старые callbacks бывшего владельца запрещены. Сборка docs/bot-clubs.md проверена календарными и race-сценариями.

**Зависимости:** AND-47, AND-68.

### [AND-74 · Сохранять события менеджеров в PostgreSQL без дублей и внутренних аккаунтов](https://linear.app/andrei-brazhnikov/issue/AND-74/sohranyat-sobytiya-menedzherov-v-postgresql-bez-dublej-i-vnutrennih)

Реализован transactional collector infrastructure.analytics и миграция 0004: coverage, exclusions/tombstones, journey sessions/events, restricted mapping и default journey_export. Псевдоним создаётся до claim, preview не начинает coverage. Version/operation/effect UNIQUE; retry не обновляет session, конфликт content отклоняется. Запись в транзакции игрового caller без самостоятельного commit/экспорта; rollback откатывает успех.

Успешные allowlist user-действия образуют сессии с gap <30 минут; action_rejected не создаёт/продлевает session, background без session. Internal/test/operator/opt_out исключены; deletion удаляет mapping/events/sessions и оставляет tombstone. First start не создаётся заново после retention cleanup. Closed envelope/имена/UPDATE guard; retention helper180д, PUBLIC revoke и view без Telegram/envelope/operation refs.

Проверка: Ruff/format, mypy40, pytest166 passed/2 deselected, offline Alembic DDL. SQL-recording проверяет collector/session/dedupe/exclusions/cleanup, без тестовых БД по указанию пользователя. PostgreSQL execution/races/CASCADE/GRANT не проверены и реальный сбор не включён; before launch требуется эксплуатационная проверка. UI instrumentation, SQL KPI, maintenance jobs и фактические HDC roles остаются AND-76/77/79/82. Документ docs/analytics/storage.md.

**Приёмка:** Collector/schema/session/dedupe/exclusion/deletion/retention helpers реализованы, offline SQL и unit checks проходят. Online PG/roles не запускались по текущему запрету тестовых БД.

**Зависимости:** AND-73, AND-45.

### [AND-75 · Добавить ссылки кампаний и атрибуцию привлечения через Telegram /start](https://linear.app/andrei-brazhnikov/issue/AND-75/dobavit-ssylki-kampanij-i-atribuciyu-privlecheniya-cherez-telegram)

Backend AND-75 реализован: opaque24-char start codes/immutable campaign registry(source,placement,date), direct/unknown/known, accept_start с Manager/pseudonym до claim, immutable first-touch, per-update retry dedupe, start_accepted/manager_started/campaign_touched и session/coverage collector в одной transaction. Не хранится raw unknown payload. Internal/opt_out исключены; retention deletes touches/first-touch без новой launch cohort.

Source funnel SQL/CLI: mature24h eligible vs waiting, claim/ready того же ownership, internal exclusion, as-of recorded_at, coverage/retention clipping, bounded aggregate/count validation. Нет CTR/CAC без расходов/показов/кликов. Generated links используют официальные Telegram limits.

Ruff/format, mypy48, pytest204 passed/2 deselected; offline migration0007/SQL-recording без БД. CLI preview без подключения, --apply не запускался.

Задача остаётся In Progress: реальный игровой /start handler ещё не подключён в AND-47; preview не начинает cohort. SQL результаты/races на PG не проверены по запрету тестовых БД. Документ docs/analytics/acquisition.md.

**Приёмка:** Реестр opaque start-кодов <=64 допустимых символов, source/placement/date и ссылки t.me/GoalettoBot. direct/unknown/known разделены. First-touch сохраняется до claim и неизменяем; повторный /start — касание, не новый manager. Отчёт source→claim→готовность; internal исключён. Тесты валидных/неизвестных/повторных кодов. CTR/CAC не выдумываются без данных показов/кликов/расходов.

**Зависимости:** AND-74.

### [AND-76 · Инструментировать вход, состав и восстановление после непонятных действий](https://linear.app/andrei-brazhnikov/issue/AND-76/instrumentirovat-vhod-sostav-i-vosstanovlenie-posle-neponyatnyh)

Связать аналитику с реализацией AND-47/48/49; существующие задачи создают UI и gameplay, эта задача проверяет полноту e2e событий и диагностический контекст.

**Приёмка:** События start/offer delivery/claim/lineup open/save и optional previews связаны с маршрутом и версией. Автосостав не считается ручной готовностью. Категории stale/invalid/claim race/lineup invalid и доставка помощи; recovery — успешное целевое user-действие за 30 минут. Retry/rollback не удваивают ступени. Обходные пути учитываются отдельно; отчёт не объявляет причину путаницы по кнопке Назад.

**Зависимости:** AND-74, AND-47, AND-48, AND-49, AND-75.

## 03 · Live-матчи и сезоны

### [AND-51 · Разработать детерминированный матчевый движок](https://linear.app/andrei-brazhnikov/issue/AND-51/razrabotat-determinirovannyj-matchevyj-dvizhok)

Реализован чистый regulation-v1: frozen snapshots/state/commands, explicit seed и policy, 7 секторов + GK, владение, моменты/исходы, усталость, карточки/травмы, injury-only замены, 5/3 окон, тактика/менталитет, остановка при <7. Закрытый JSON checkpoint/hash и точный replay без Telegram/SQLAlchemy/сети/часов. Домашняя поддержка временно повышает мораль 0–100 по фактическим home_fans, ограниченным вместимостью стадиона; crowd и policy pinned в snapshot. Большая пустая арена не даёт бонус.

Проверка: golden seed7 1:2, 57 событий, checkpoint после 17 тиков даёт тот же финал; 200 seeds с 2500 болельщиками: home/away/draw 78/56/66 против 67/67/66 без поддержки. Коэффициенты предварительные. CLI goaletto-match без БД.

Приёмка: domain/replay/команды/поле реализованы и проверены; runtime/accepted-command persistence/UTC jobs/outbox/Telegram UI остаются AND-52/53/68, финансовый спрос/стадион — AND-63. PostgreSQL и HDC не запускались, тестовые БД не создавались. Документация: docs/match-engine.md.

**Приёмка:** Golden/replay, immutable inputs, события, sector/form/fatigue/morale, команды/лимиты, форсированные cards/injuries и серия баланса проверены без БД.

**Зависимости:** AND-45, AND-49.

### [AND-52 · Реализовать долговечный минутный runtime матчей](https://linear.app/andrei-brazhnikov/issue/AND-52/realizovat-dolgovechnyj-minutnyj-runtime-matchej)

Реализован durable regulation runtime, worker dispatcher и explicit season activation CLI. Fixture/Match locks, immutable starting snapshots/seed/version, absolute UTC deadlines от actual_start_at, 15/18/19/33 minute boundaries, closed checkpoint/projection validation, идемпотентные ticks/stages и outbox в одной game transaction. Поддержка трибун pinned: capacity/supporters persisted, baseline attendance=min(supporters,capacity), прогноз спроса остаётся AND-63.

Accepted commands ordered, deadline check, tick FK insert-before-apply, halftime free window без FK на будущий tick16, applied lifecycle/command correlation. System lifecycle не human activity. Operation attempts, savepoint deferral/failed codes. Late kickoff rebases один раз; mid-match lateness не сдвигает clock. Explicit pause/resume/postpone сохраняют actual dates; abandoned отдельно, без присуждения3:0/очков.

Проверка: Ruff/format, mypy42, полный offline pytest182 passed/2 deselected плюс новый snapshot pin test, 17 targeted runtime/dispatcher checks. Непрерывные30 ticks дают hash ae4002fb500d7769e280a9939d3f345f78e935b6c1b1be6a3f0bb792850c6841; retry/restart/halftime/delay/pause/command FK/deadline/abandoned/outbox проверены SQL-recording без БД. Миграция0005 offline upgrade/ALTER/DDL.

По указанию пользователя тестовые БД не создавались; real PG locks/races/rollback/trigger execution не проверялись. Seed/runtime --apply и HDC не запускались, held jobs не активированы. Sender/live Telegram edit — AND-68, command admission/UI/instructions — AND-53, standings/results — AND-56, rollover — AND-57, disaster recovery — AND-69. Документ docs/match-runtime.md.

**Приёмка:** Runtime/worker/activation, exact UTC deadlines, atomic checkpoint/events/jobs/outbox, commands and attempts реализованы и unit/offline SQL проверены. Online PG не запускался; календарь не активирован.

**Зависимости:** AND-51, AND-74.

### [AND-53 · Добавить live-замены, тактику и инструкции между тиками](https://linear.app/andrei-brazhnikov/issue/AND-53/dobavit-live-zameny-taktiku-i-instrukcii-mezhdu-tikami)

Команды в БД на следующий тик; серверные дедлайны и порядок; 5 замен/3 окна + перерыв, 3 смены тактики, менталитет и инструкции.

**Приёмка:** UI различает принято/применено; повтор callback не расходует лимит дважды; устаревшая команда не меняет завершённый матч; удалённого футболиста заменить нельзя.

**Зависимости:** AND-52, AND-49.

### [AND-54 · Реализовать фиксированные составы бот-клубов и пополнение после пенсии](https://linear.app/andrei-brazhnikov/issue/AND-54/realizovat-fiksirovannye-sostavy-bot-klubov-i-popolnenie-posle-pensii)

Простая автоматика клубов без владельцев: играют общей симуляцией сохранённой XI/скамейкой/схемой/тактикой. Никаких LLM, уровней simple/medium/smart, тренировок, трансферов, подбора тактики, ротации по счёту/усталости. Автоматически заменить только травмированного здоровым резервом по стабильному правилу. В межсезонье ушедшего на пенсию пополнить из молодёжки; полный продукт академии AND-62 для базового пополнения не требуется.

**Приёмка:** Бот-клуб играет полный сезон общей симуляцией, его базовая XI не меняется от OVR/формы/счёта. Замены только травмированных, общие лимиты и запрет замены удалённого. Приёмка фиксирует поведение без резерва/с дисквалификацией по общим правилам допуска; не вводить иммунитет ботов. Retirement/replacement по season+player идемпотентен, позиция/размер состава сохраняются; травма не генерирует молодого игрока. Claim переводит управление человеку; отсутствие человека не запускает скрытые тренировки/тактику. Pure seeded rules, no network; replay/rerun tests. Уточнение 2026-10-11: Применять тот же возраст+1/сезон и progressive last-season draw32–38 к бот-футболистам. Предупреждённые играют весь последний сезон, потом retirement и одно молодёжное пополнение в межсезонье; не генерировать replacement в момент объявления. При38 сезон обязательно последний. Отсутствие тренировок не замораживает возраст. docs/player-lifecycle.md.

**Зависимости:** AND-46, AND-51.

### [AND-55 · Создать двухкруговой календарь и недельное расписание](https://linear.app/andrei-brazhnikov/issue/AND-55/sozdat-dvuhkrugovoj-kalendar-i-nedelnoe-raspisanie)

Реализован чистый circle calendar: 18 туров, 90 матчей на лигу, 450 на комплект; пары home/away, 5 игр/тур и один матч клуба за тур. UTC Saturday 18:00, независимый kickoff комплекта; две пустые субботы после последнего тура. UUID/keys/deadlines стабильны. Seed атомарно сохраняет fixtures и 34 jobs/матч: remind -5min, lock -1min, start, 30 ticks и second-half 18:00; season open/rollover отдельно.

**Приёмка:** Календарь и clock boundaries проверены unit-тестами для нескольких seed, включая DST и tick15/16/30. Повтор world seed возвращает receipt без повторного fixtures/jobs. Все календарные jobs held, worker их не исполняет. Задача остаётся In Progress: нужны runtime handlers/recovery AND-52 и delivery AND-68, затем безопасная активация поддерживаемых jobs. События фактического старта/окончания и уведомления сейчас не отправлялись. Проверок БД и --apply не было. docs/world-initialization.md.

**Зависимости:** AND-46, AND-52.

### [AND-56 · Реализовать результаты, таблицы и стабильные tie-breaks](https://linear.app/andrei-brazhnikov/issue/AND-56/realizovat-rezultaty-tablicy-i-stabilnye-tie-breaks)

Backend и Telegram presenters реализованы: immutable result receipt, атомарные3/1/0 и оба Standing в финальном тике, points/GD/GF/persisted draw, историческая membership, idempotent replay/changed receipt guard, abandoned без очков. Presenter5/page и компактные callbacks/отчёт проверены. Реальные кнопочные handlers/authorization ещё не подключены к onboarding/navigation AND-47/48, задача остаётся In Progress. Документ docs/match-results.md. PostgreSQL не запускался, unit/offline SQL checks без тестовых БД.

**Приёмка:** 3/1/0 очков начисляются один раз; до/после restart таблица одинакова; ничьи по показателям имеют стабильный порядок; отчёты открываются кнопкой.

**Зависимости:** AND-55.

### [AND-57 · Реализовать одновременное повышение и понижение 2×2](https://linear.app/andrei-brazhnikov/issue/AND-57/realizovat-odnovremennoe-povyshenie-i-ponizhenie-22)

Новый кадровый цикл2026-10-11: staff age+1 за сезон; после player retirement возможен один persisted career transition в допустимый staff pool с сохранением person/age/history/appearance. Финальная staff retirement после полного announced last season60–70, в 70 обязательно; точная шкала и шанс смены профессии пока открыты. Уникальные transition/event keys, единый person lock, не+2 при смене роли, не двойное aging в pool/home calendar. docs/staff-lifecycle.md; gameplay не реализован. Итоговый план всех 5 лиг: топ-2 вверх, нижние-2 вниз, граничные исключения; внутри league_set, без стыковых.

**Приёмка:** Одна транзакция/season lock; 50 уникальных клубов и 10 в каждой лиге; один уровень за сезон; повтор rollover безопасен; история сезона остаётся неизменной. Уточнение 2026-10-11: Rollover согласуется с жизненным циклом: после завершения всех матчей отмеченные пенсионеры завершают карьеру, survivors получают +1, выбор последнего будущего сезона32–38 сохраняется один раз и создаёт outbox предупреждения до первого тура. При38 вероятность100%. Restart не повторяет age/draw/retirement/youth replacement; сезонные snapshots сохраняются. Общий контракт docs/player-lifecycle.md, реализация возраста в AND-64. Уточнение 2026-10-11: Rollover при объявлении последнего сезона согласован с рынком: сериализовать под player lock, отменить открытые сделки и идемпотентно вернуть резервы, записать outbox. Проверить race с settlement и повтор rollover/refund. Шкала32..38 утверждена5/10/20/35/55/80/100%.

**Зависимости:** AND-56.

### [AND-77 · Измерять участие в матчах отдельно от симуляции и задержек доставки](https://linear.app/andrei-brazhnikov/issue/AND-77/izmeryat-uchastie-v-matchah-otdelno-ot-simulyacii-i-zaderzhek-dostavki)

30 тиков + 3 минуты перерыва, недельное расписание: 24 часа без второго матча не означают отвал. Без предположений о присутствии онлайн по пассивному табло.

**Приёмка:** Явные match/report open, accepted/applied command, fixture finished и result delivery имеют корреляцию/дедуп. Eligible opportunity определяется владением до lineup lock; перенос/пауза по actual times, смена владельца отдельно. автоматика/ticks/delivery не дают user retention. Технические попытки accepted→commit→queued→render→Telegram accepted/failed/unknown измеряются отдельно, p50/p95 и очередь не включают календарное ожидание. Restart/uncertain send сохраняют правдивый статус.

**Зависимости:** AND-74, AND-52, AND-53, AND-55, AND-56, AND-66, AND-68.

### [AND-78 · Собирать добровольные причины остановки и отзывы внутри Telegram](https://linear.app/andrei-brazhnikov/issue/AND-78/sobirat-dobrovolnye-prichiny-ostanovki-i-otzyvy-vnutri-telegram)

Основа Frontline AND-27. Сначала реализовать и проверить на synthetic/internal, не отправлять сейчас сообщения реальным людям; основной игровой CTA сохраняет приоритет.

**Приёмка:** Вторичная кнопка после первого результата; помощь для незавершённого входа через 24 ч и один опрос после пропущенной зрелой матчевой возможности. Ожидание календаря не триггер. Кампания/менеджер уникальны; skip/opt-out terminal, cooldown >=7 дней, недоступность/unknown delivery безопасны. Конечные причины и optional comment <=500 символов в явном 30-мин окне, команды не перехватываются. Restricted текст, retention cleanup, без логов/labels. Доля ответов/skip показана; причины не приписываются молча ушедшим.

**Зависимости:** AND-76, AND-77.

## 04 · Развитие клуба и экономика

### [AND-58 · Добавить недельные тренировки и возрастной прогресс](https://linear.app/andrei-brazhnikov/issue/AND-58/dobavit-nedelnye-trenirovki-i-vozrastnoj-progress)

Фокусы навыков, форма/выносливость, возрастные коэффициенты, потолок потенциала и тренерские бонусы по GDD.

**Приёмка:** Один расчёт на клуб/неделю; навык/возраст ограничены валидными диапазонами; UI показывает результат; повтор job не тренирует дважды. Уточнение пользователя: Клубы без владельцев не тренируются. Не создавать автоматический тренировочный фокус за отсутствующего человека. Очередь тренировки повторно проверяет актуального владельца при применении, включая release race.

**Зависимости:** AND-45, AND-47.

### [AND-59 · Создать экономику клуба и денежный ledger](https://linear.app/andrei-brazhnikov/issue/AND-59/sozdat-ekonomiku-kluba-i-denezhnyj-ledger)

Баланс integer, журнал транзакций, зарплаты/спонсоры/билеты/призы; атомарные операции, reservations под ставки и контроль повторов.

**Приёмка:** Повтор начисления не удваивает баланс; параллельные расходы не дают недопустимый остаток; финансовый экран согласуется с ledger; правила зарплат/банкротства описаны.

**Зависимости:** AND-45.

### [AND-60 · Реализовать аукционный трансферный рынок](https://linear.app/andrei-brazhnikov/issue/AND-60/realizovat-aukcionnyj-transfernyj-rynok)

Листинг на 48 часов, ставки, резервы, освобождение проигравших резервов, продление анти-снайпера, расчёт после закрытия; окна и лимиты 16–25.

**Приёмка:** Параллельные ставки/закрытие безопасны; один владелец игрока; повтор settlement не переводит деньги/игрока дважды; проверка окна/минимальной цены/остатка; уведомления через outbox. Уточнение 2026-10-11: Правило пользователя: объявленный последний сезон полностью запрещает сделку с футболистом. Запрет на listing, bid, direct offer, exchange, settlement, обход через release/signing; футболист доигрывает в текущем клубе. Проверять durable статус под player lock при создании, ставке и settlement. Объявление/settlement сериализованы: оконченная до объявления сделка не откатывается, после объявления передачи нет. Отменить открытые лоты/предложения и вернуть все резервы один раз, уведомления outbox. Claim самого клуба меняет менеджера, не клуб игрока. Приёмка: stale callback, bid/settlement race с rollover, повтор cancellation/refund, отсутствуют игрок/double money transfers. Межкомплектный aging определить только для неотмеченных игроков. docs/player-lifecycle.md. Рынок/guard ещё в плане.

**Зависимости:** AND-59, AND-49.

### [AND-61 · Добавить персонал клуба и скаутские задания](https://linear.app/andrei-brazhnikov/issue/AND-61/dobavit-personal-kluba-i-skautskie-zadaniya)

Новые требования2026-10-11: отдельные стартовые unique staff candidates, позже пополнение из завершивших карьеру футболистов. Один person, рабочий возраст, qualification, один employment; если подходит нескольким ролям, не клонировать кандидата. Найм atomically убирает из всех поисковых пулов, увольнение не омолаживает, retired не возвращается. Вратарь может идти в GK coach; другие тренерские/скаутские пригодности отдельно от OVR, физиотерапия требует квалификации. Диапазон последнего рабочего сезона 60–70 согласован, в 70 100%; точная шкала/transition chance/размер initial pool остаются параметрами. docs/staff-lifecycle.md. Тренер/скаут/physio, зарплаты и бонусы; скаутские направления, сроки и отчёты; staff portrait bank Dugout.

**Приёмка:** Найм и расчёт зарплат атомарны; один отчёт на завершённое задание; статистика скрытого потенциала раскрывается по правилам; отдельные зрелые лица персонала.

**Зависимости:** AND-58, AND-59, AND-43.

### [AND-83 · Добавить вторую карьеру бывших игроков и пенсию персонала](https://linear.app/andrei-brazhnikov/issue/AND-83/dobavit-vtoruyu-kareru-byvshih-igrokov-i-pensiyu-personala)

Бывшие футболисты могут продолжить работу как тренеры и другой квалифицированный персонал. Это тот же person: age/history/appearance сохраняются. Персонал ежегодно взрослеет; согласован диапазон последнего рабочего сезона 60–70, в 70 обязательно последний сезон и окончательная пенсия после него. Точная шкала, доля переходов, пригодность и home calendar свободных сотрудников ещё требуют решения.

Приёмка: persisted player retirement→career decision→one staff profile; не каждый автоматически сотрудник; role skills отдельно от OVR; qualified pools без клонирования человека, one employment, atomic hire/fire, увольнение не омолаживает, окончательный retired не появляется в pool. До сезона one durable last-season decision/warning; retry без reroll и дублей, new staff не получает+2 age; last work season завершается перед окончательной пенсией. Career portrait variant сохраняет identity, missing variant не заменяет чужим лицом. Проверки rollover/retry/parallel hire/pool/home calendar/age boundaries. Документ docs/staff-lifecycle.md; механика ещё не реализована.

**Зависимости:** AND-45, AND-57, AND-61, AND-64.

### [AND-62 · Реализовать академию и выпуск молодых игроков](https://linear.app/andrei-brazhnikov/issue/AND-62/realizovat-akademiyu-i-vypusk-molodyh-igrokov)

Уровни академии, расходы, выпуск 17-летних, скрытый потенциал, сроки и лимиты состава.

**Приёмка:** Выпуск не дублируется; корректные позиции/возраст; нельзя превысить 25 игроков; затраты попадают в ledger; объяснимый экран академии. Уточнение пользователя: Базовая молодёжная замена ушедшего на пенсию бот-футболиста реализуется в AND-54 без ожидания полной пользовательской академии. Здесь расширяется пользовательская академия; не создавать второй выпуск по одному retirement.

**Зависимости:** AND-58, AND-59.

### [AND-63 · Добавить стадион, содержание и матчевый доход](https://linear.app/andrei-brazhnikov/issue/AND-63/dobavit-stadion-soderzhanie-i-matchevyj-dohod)

Уровни, вместимость, стоимость/срок расширения, доход с учётом лиги, формы и посещаемости. Домашние болельщики временно повышают morale игроков; посещаемость <= capacity. Pinned crowd в AND-51; спрос/рост фанатов/доход AND-63, пустая арена бонуса не даёт.

**Приёмка:** Одно улучшение/начисление за событие; нет бесплатного повторного апгрейда; экономика проверена на сезонном прогоне; финансовая запись соответствует стадиону.

**Зависимости:** AND-59.

### [AND-64 · Добавить мораль, форму, опыт и старение футболистов](https://linear.app/andrei-brazhnikov/issue/AND-64/dobavit-moral-formu-opyt-i-starenie-futbolistov)

Уточнение2026-10-11: окончание игровой карьеры не обязательно окончательная пенсия person. Сохранить player history, retired gameplay guard, возраст/appearance; staff profile той же личности создаётся однократно при положительном career outcome. Football pension32–38 прежняя, staff last working season60–70 отдельная политика. Не создавать второго человека и не удваивать возраст в rollover. docs/staff-lifecycle.md. Возраст: +1 игровой год за сезон. В межсезонье прогрессивное однократное решение о последнем следующем сезоне для 32–38 лет; в38= 100%. Мораль/форма/опыт отдельно, без внезапного ухода.

**Приёмка:** Диапазоны и скорости согласованы; повтор расчёта безопасен; изменения влияют на sim; сезонная симуляция не даёт мгновенных пенсионеров/навыков 20. Уточнение пользователя: Пенсия и пополнение бот-клубов выполняются в межсезонье, без внезапного retirement/replacement посреди live-матча. Согласовать общие возрастные параметры с AND-54; без повторного replacement и тренировок клубов без владельца. Уточнение 2026-10-11: Решение пользователя 2026-10-11: +1 игровой год за каждый сезон. В конце сезона уже отмеченные уходят; выжившие получают +1 для нового сезона, для его возраста32–38 проводится один persisted draw, будет ли следующий сезон последним. В32 шанс мал, монотонно растёт, в38= 100%. Утверждённая пользователем шкала:5/10/20/35/55/80/100% по возрастам32..38 (приняты пользователем). Положительное решение→метка на весь следующий сезон→уход по его завершении без повторной жеребьёвки. Нет фиксированного retirement_age при рождении и нет active39. Уникальность player+season+stage, явный seed/draw/policy version, idempotent age/announcement/retirement/outbox; seed38 сразу имеет последний сезон. Identity сохраняется после claim/допустимого transfer; последний сезон запрещает transfer; archived age/portrait не переписываются. Матч/неделя/portrait render не старят человека. Пенсионная шкала футболистов не применяется к staff. Приёмка:границы31→32/37→38, p endpoints, один полный предупреждённый сезон, retry/restart, retired исключён из состава/тренировок/ставок, ownership-independent aging. docs/player-lifecycle.md. Уточнение 2026-10-11: Шкала принята пользователем:32→5%,33→10%,34→20%,35→35%,36→55%,37→80%,38→100%. После объявления последнего сезона футболист доигрывает в текущем клубе, любая сделка запрещена с момента объявления. Не менять ранее сохранённое решение. Сам aging/retirement persistence пока не реализован; иллюстрированный renderer уже принимает текущий возраст без самостоятельного aging.

**Зависимости:** AND-58, AND-51.

### [AND-65 · Реализовать кубок, дополнительное время и пенальти](https://linear.app/andrei-brazhnikov/issue/AND-65/realizovat-kubok-dopolnitelnoe-vremya-i-penalti)

Кубок по средам, жеребьёвка, bye для некратного числа участников; extra time/penalties с отдельной спецификацией часов.

**Приёмка:** Победитель определён, нет double elimination/results; календарь не конфликтует с лигой; лимиты команд и длительности допвремени объяснены; replay tests.

**Зависимости:** AND-52, AND-55.

### [AND-66 · Создать историю матчей, статистику и отчёты](https://linear.app/andrei-brazhnikov/issue/AND-66/sozdat-istoriyu-matchej-statistiku-i-otchyoty)

История клуба/игрока, события/замены/лучший игрок, архив сезона и replay данных; генерация карточек в стиле Dugout.

**Приёмка:** Отчёт повторно открывается после restart; архив не переписывается при rollover; списки paginated; ключевые показатели доступны в тексте.

**Зависимости:** AND-56.

### [AND-67 · Добавить косметические покупки за Telegram Stars](https://linear.app/andrei-brazhnikov/issue/AND-67/dobavit-kosmeticheskie-pokupki-za-telegram-stars)

Герб/форма и косметика, цены/платежи/refunds/ledger; сначала решить влияние аналитики и скаутских слотов на fairness.

**Приёмка:** Нет продажи прямого преимущества без отдельного решения; повтор payment update не выдаёт покупку дважды; проверка суммы/валюты; описаны возвраты и поддержка.

**Зависимости:** AND-59, AND-44.

## 05 · HDC и эксплуатация

### [AND-68 · Реализовать sender уведомлений и устойчивое live-табло](https://linear.app/andrei-brazhnikov/issue/AND-68/realizovat-sender-uvedomlenij-i-ustojchivoe-live-tablo)

Outbox dispatcher с lease/retry_after, приоритетами и стабильным message_id; не держать DB transaction во время Telegram call.

**Приёмка:** Telegram rate limits не останавливают sim; рестарт возвращает взятые lease; отправки отслеживаются; задокументирован риск дубля send после crash; edit повторобезопасен. Приёмка аналитики: delivery означает Telegram accepted, не read. Попытки и outcome_unknown после crash отделены от логического события, без продления user session. Время queue/render/delivery измеряется отдельно от календаря; детали в AND-77.

**Зависимости:** AND-52.

### [AND-69 · Проверить восстановление после сбоев HDC и БД](https://linear.app/andrei-brazhnikov/issue/AND-69/proverit-vosstanovlenie-posle-sboev-hdc-i-bd)

Crash points: тик до/после commit, rollover, claim, bid/settlement и отправка outbox; пауза live-матча вместо залпового catch-up.

**Приёмка:** Голы/деньги/клубы не дублируются; менеджерам сохраняется окно решений после простоя; degraded mode и recovery runbook; периодический reconciliation jobs. Уточнение пользователя: Recovery включает last_game_action_at/ownership deadline, повтор освобождения и устаревшие reminders; нельзя отобрать вернувшийся активный клуб или отправить сообщение старому владельцу после нового claim.

**Зависимости:** AND-52, AND-57, AND-60.

### [AND-70 · Подготовить backup, health и наблюдаемость на HDC](https://linear.app/andrei-brazhnikov/issue/AND-70/podgotovit-backup-health-i-nablyudaemost-na-hdc)

Метрики tick lateness/queue/outbox/failed jobs/диск; ежедневные backup вне HDC и restore drill; автозапуск после reboot.

**Приёмка:** Секреты не попадают в логи; backup восстанавливается в изолированной БД; health отражает DB/worker heartbeat; monitoring интегрирован в существующий HDC без нарушения соседних сервисов.

**Зависимости:** AND-39.

### [AND-71 · Провести нагрузочные, балансные и конкурентные проверки](https://linear.app/andrei-brazhnikov/issue/AND-71/provesti-nagruzochnye-balansnye-i-konkurentnye-proverki)

Несколько комплектов, одновременные матчи/регистрации/ставки; сезонные sim-прогоны; целевые показатели определить по измеренным ресурсам HDC.

**Приёмка:** Проверены race cases и аварии; измерены p95 tick lateness/callback latency; сезон сохраняет 10 клубов в лиге; документированы bottlenecks и лимиты для запуска. Уточнение пользователя: Дополнительные сценарии: бот-клубы играют фиксированной XI без тренировок/тактической ротации; близкие начальные составы; injury-only substitutions; retirement+young replacement в межсезонье; шесть календарных месяцев/конец месяца/возврат одновременно с release. Никакой внешний ИИ.

**Зависимости:** AND-57, AND-60, AND-68, AND-69.

### [AND-72 · Развернуть готовый MVP на HDC и провести smoke](https://linear.app/andrei-brazhnikov/issue/AND-72/razvernut-gotovyj-mvp-na-hdc-i-provesti-smoke)

Существующий Windows Docker host; отдельный Compose project/volumes/secrets, без публичного PostgreSQL и Mini App. Запуск bot long polling после migration.

**Приёмка:** Проверены BotFather username/token и один polling instance; /start→claim→lineup→live→result; reboot/rollback/restore; соседние сервисы не перезапущены; deploy записан в runbook. Дополнительный gate: AND-82 подтверждает точные воронки/зрелые когорты и исключение internal/автоматика до первого внешнего привлечения. Зафиксировать coverage_start; операторские отчёты доступны. Эксперименты AND-81 не блокируют MVP. Решение пользователя 2026-10-11: локально управляемый ручной деплой. Основной путь: собрать/проверить образ здесь под архитектуру HDC → docker save → SFTP/SCP → SHA-256/image ID check → docker load на HDC → Compose --no-build. Альтернатива: commit/push здесь → git pull --ff-only выбранного проверенного SHA на HDC → локальный docker build там. Оба пути используют immutable release tag/GOALETTO_IMAGE и deploy/compose.release.yaml для migrate/bot/worker. Credentials только host-only, CI выполняет проверки и не деплоит. Приёмка AND-72: проверены согласованный путь, metadata/smoke/rollback предыдущего image, миграции до запуска worker и отсутствие изменений соседних сервисов. Документация: docs/deployment-hdc.md. Настройка пути не означает выполненный HDC deploy.

**Зависимости:** AND-71, AND-70, AND-82.

### [AND-79 · Построить SQL-воронки, зрелые когорты и отчёт о местах остановки](https://linear.app/andrei-brazhnikov/issue/AND-79/postroit-sql-voronki-zrelye-kogorty-i-otchyot-o-mestah-ostanovki)

Три KPI и формулы в measurement-plan.md; числовые цели после наблюдаемой базы, а не наследование неподтверждённых процентов GDD.

**Приёмка:** SQL/views: source→club→manual lineup; first/second eligible match; exact D1/D3/D7/D30; последний подтверждённый шаг, ожидание и recovery. Все n/N зрелые по UTC, cohort first start неизменяем, distinct manager/fixture, internal excluded. Показаны coverage_start/version, unknown attribution, отсутствующий календарь, ожидающие матч и смены владения. Малые выборки описательно, причины не выводятся из dropoff. Кубок/shortcuts/варианты отдельными маршрутами; late arrivals пересчитывают окна.

**Зависимости:** AND-76, AND-77.

### [AND-80 · Вывести источники, воронки, ошибки и отзывы в операторский Grafana](https://linear.app/andrei-brazhnikov/issue/AND-80/vyvesti-istochniki-voronki-oshibki-i-otzyvy-v-operatorskij-grafana)

Использовать имеющиеся PostgreSQL/Grafana, подключение проверять без воздействия на соседние сервисы. Отдельный restricted просмотр комментариев, не в общем dashboard.

**Приёмка:** Приватный dashboard existing HDC Grafana: coverage/источники, onboarding, зрелые opportunities/cohorts, ошибки/recovery, response/skip и guardrails очереди. Фильтры неделя/source/locale/variant/tier-set в restricted SQL; Prometheus только low-cardinality агрегаты без IDs/текстов. Restricted доступы и понятные n/N/зрелость/период. Dashboard provisioned/documented, неизвестные/нет данных не превращаются в ноль. Нет публичного UI/API.

**Зависимости:** AND-79, AND-78, AND-70.

### [AND-81 · Подготовить последовательные эксперименты подсказок и ожидания первого матча](https://linear.app/andrei-brazhnikov/issue/AND-81/podgotovit-posledovatelnye-eksperimenty-podskazok-i-ozhidaniya-pervogo)

Адаптация Frontline AND-37. Дневной retention — диагностика, недельные opportunities — основной результат; никакой гарантии причинности по небольшой когорте.

**Приёмка:** План первых гипотез: следующий шаг после claim, пояснение ожидания, CTA после результата. Зафиксированы primary metric/guardrails, стабильное assignment, actual delivery exposure и версия. Одно изменение за раз, internal отдельно; low traffic qualitative review без ложной статистической победы. Предусмотрены остановка/rollback и исключение unknown exposure; не менять gameplay/экономику. Реальный запуск эксперимента после baseline, runner не блокирует MVP.

**Зависимости:** AND-80.

### [AND-82 · Проверить корректность аналитики перед первым привлечением игроков](https://linear.app/andrei-brazhnikov/issue/AND-82/proverit-korrektnost-analitiki-pered-pervym-privlecheniem-igrokov)

Аналитика должна объяснять стартовую когорту с первого дня; синтетические данные не смешивать с внешними пользователями.

**Приёмка:** Изолированная PG fixture с точными ожидаемыми n/N: direct/campaign/unknown, drop до claim, manual/autolineup, бот-клубы/internal, retry/rollback/restart, delivery unknown, transfer ownership, перенос/межсезонье/первый матч через7д, незрелыйD7. Delivered без user action не retention. SQL/report/dashboard согласованы, доступы/cleanup/feedback caps/экспорт проверены. Runbook с coverage_start и troubleshooting; gate AND-72, без production DB.

**Зависимости:** AND-80, AND-71.
