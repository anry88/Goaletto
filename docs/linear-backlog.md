# Задачи Goaletto в Linear

[Проект Goaletto](https://linear.app/andrei-brazhnikov/project/goaletto-eb8cb486bc79) · 44 задачи · 5 вех.

Linear — источник актуального статуса; этот реестр — snapshot нарезки, дополненный планом аналитики 2026-10-11.
Выполненными отмечаются только завершённые foundation-работы, не запланированный gameplay.

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

### [AND-44 · Довести иллюстрированные слои Dugout до production-каталога](https://linear.app/andrei-brazhnikov/issue/AND-44/dovesti-illyustrirovannye-sloi-dugout-do-production-kataloga)

После выбора C и проверки пилота создать согласованные части в качестве ретро-концепта: отдельные молодые игроки/взрослый персонал, общие anchors, силуэты, разрешения и каталог совместимости.

**Приёмка:** Раздельные прозрачные слои, сохранённые IDs и версии; тесты 96/256/512 px и матрица сочетаний; PNG renderer/cache для карточек; каталог проверен визуально пользователем перед production.

**Зависимости:** AND-43.

## 02 · Игровой мир и вход

### [AND-45 · Разработать доменную схему игроков, сезонов и матчей](https://linear.app/andrei-brazhnikov/issue/AND-45/razrabotat-domennuyu-shemu-igrokov-sezonov-i-matchej)

Расширить начальные 5 таблиц: managers, players, staff, seasons, fixtures/matches/ticks, commands, standings; constraints и индексы.

**Приёмка:** Миграции upgrade/check проходят; Telegram ID bigint, деньги integer; правила уникальности клуба/тика/сезона; schema/ERD и транзакционные границы документированы.

**Зависимости:** AND-39, AND-40, AND-73.

**Дополнение аналитики:** Схема аналитики: managers создаются до claim клуба; контракт AND-73 задаёт псевдонимы/идентификаторы и границы application. Принимать миграции совместно с планом docs/analytics/measurement-plan.md.

### [AND-46 · Создать первый комплект пяти лиг и 50 AI-клубов](https://linear.app/andrei-brazhnikov/issue/AND-46/sozdat-pervyj-komplekt-pyati-lig-i-50-ai-klubov)

Идемпотентный seed: league_set, уровни 1–5 по 10 клубов, составы 18–20 футболистов, позиции/возраст/потенциал, бюджеты и архетипы.

**Приёмка:** Первый запуск даёт ровно 5 лиг и 50 клубов; повтор seed не добавляет копии; у каждого клуба корректный состав и вратарь; seed детерминирован для тестов.

**Зависимости:** AND-45.

### [AND-47 · Реализовать выдачу клубов только лиги 5 и расширение мира](https://linear.app/andrei-brazhnikov/issue/AND-47/realizovat-vydachu-klubov-tolko-ligi-5-i-rasshirenie-mira)

Onboarding показывает только свободные AI-клубы уровня 5 во всех комплектах. При отсутствии мест атомарно создать новый полный комплект 5×10.

**Приёмка:** Один Telegram ID — один клуб; параллельный claim последнего места не делит клуб/не создаёт лишние комплекты; верхние лиги недоступны новичкам; текущие финансы/таблица клуба сохраняются.

**Зависимости:** AND-46, AND-74, AND-75.

**Дополнение аналитики:** Приёмка аналитики: первый игровой /start и источник фиксируются до claim; club_claimed атомарен с владением, retry/race не удваивают событие. Internal/AI отделены. Полнота воронки проверяется в AND-76.

### [AND-48 · Создать Telegram-навигацию, карточки и пагинацию](https://linear.app/andrei-brazhnikov/issue/AND-48/sozdat-telegram-navigaciyu-kartochki-i-paginaciyu)

Главное меню и подменю; стабильные Назад/Главное меню, списки/карточки игроков, персонала и клубов; оформление C для изображений и текстов.

**Приёмка:** Все действия доступны кнопками/командами без Mini App; быстрый answerCallbackQuery; устаревшие callbacks безопасны; HTML-экранирование имён; показатели доступны текстом.

**Зависимости:** AND-39, AND-42, AND-74.

**Дополнение аналитики:** Приёмка аналитики: явный переход/нажатие отделены от доставки экрана. Ошибки/устаревшие кнопки имеют конечные reason_code, контекстная помощь и успешный recovery отслеживаются без raw callback или текста. Сбор по контракту AND-73; e2e AND-76.

### [AND-49 · Реализовать состав, схему и предматчевую тактику](https://linear.app/andrei-brazhnikov/issue/AND-49/realizovat-sostav-shemu-i-predmatchevuyu-taktiku)

Выбор схемы, позиции, стартовой XI, скамейки, капитана и тактики через кнопки; штрафы за неподходящую позицию; блокировка за минуту до матча.

**Приёмка:** Только владелец меняет состав; нельзя задублировать игрока/выставить травмированного; корректные 11 позиций и вратарь; сохранение в БД; автосостав при отсутствии выбора.

**Зависимости:** AND-45, AND-47, AND-48.

### [AND-50 · Реализовать AI-подхват и освобождение неактивных клубов](https://linear.app/andrei-brazhnikov/issue/AND-50/realizovat-ai-podhvat-i-osvobozhdenie-neaktivnyh-klubov)

AI-помощь после 2 недель неактивности; освобождение после 4; сроки и предупреждения из GDD.

**Приёмка:** Повтор job не освобождает активного владельца; клуб сохраняет историю; освобождённый клуб лиг 1–4 не выдаётся новичку; сообщения через outbox.

**Зависимости:** AND-47.

## 03 · Live-матчи и сезоны

### [AND-51 · Разработать детерминированный матчевый движок](https://linear.app/andrei-brazhnikov/issue/AND-51/razrabotat-determinirovannyj-matchevyj-dvizhok)

Чистые функции: секторные рейтинги, владение, шансы, исходы, выносливость, карточки/травмы; явные state/commands/seed/version.

**Приёмка:** Одинаковые входы дают одинаковые события и state; no Telegram/DB imports; golden seed/replay tests; осмысленные распределения результатов на серии симуляций.

**Зависимости:** AND-45, AND-49.

### [AND-52 · Реализовать долговечный минутный runtime матчей](https://linear.app/andrei-brazhnikov/issue/AND-52/realizovat-dolgovechnyj-minutnyj-runtime-matchej)

30 тиков по 60 секунд/3 игровых минуты, 180-секундный перерыв после тика 15; абсолютные сроки, отдельное событие старта второго тайма.

**Приёмка:** Тик 15 в 15:00, старт тайма 2 в 18:00, тик 16 в 19:00, тик 30/90′ в 33:00; повтор tick не меняет счёт дважды; state/events/jobs/outbox commit атомарен.

**Зависимости:** AND-51, AND-74.

**Дополнение аналитики:** Приёмка аналитики: ticks/fixture lifecycle — system, не user activity. События lifecycle пишутся атомарно и dedupe по fixture/stage/version. Принятие/применение команд и технические попытки коррелируются; перенос/пауза сохраняют actual times для AND-77.

### [AND-53 · Добавить live-замены, тактику и инструкции между тиками](https://linear.app/andrei-brazhnikov/issue/AND-53/dobavit-live-zameny-taktiku-i-instrukcii-mezhdu-tikami)

Команды в БД на следующий тик; серверные дедлайны и порядок; 5 замен/3 окна + перерыв, 3 смены тактики, менталитет и инструкции.

**Приёмка:** UI различает принято/применено; повтор callback не расходует лимит дважды; устаревшая команда не меняет завершённый матч; удалённого футболиста заменить нельзя.

**Зависимости:** AND-52, AND-49.

### [AND-54 · Разработать AI-менеджеров разных уровней](https://linear.app/andrei-brazhnikov/issue/AND-54/razrabotat-ai-menedzherov-raznyh-urovnej)

Состав, тренировки и live-решения simple/medium/smart; учитывать позиции, усталость, травмы, текущий счёт; не использовать LLM в обязательном runtime.

**Приёмка:** Каждый AI-клуб может пройти сезон без человека; правила/лимиты такие же, как для людей; claim отключает AI-управление; deterministic seed tests.

**Зависимости:** AND-46, AND-51.

### [AND-55 · Создать двухкруговой календарь и недельное расписание](https://linear.app/andrei-brazhnikov/issue/AND-55/sozdat-dvuhkrugovoj-kalendar-i-nedelnoe-raspisanie)

По 18 туров для каждой лиги 10 клубов; суббота 18:00 UTC; сезоны комплектов независимы; начало/межсезонье и уведомления.

**Приёмка:** Каждая пара играет дома/в гостях; 5 матчей в туре и 90 в сезоне на лигу; нет пересечений клуба по времени; повтор планирования не дублирует fixtures/jobs.

**Зависимости:** AND-46, AND-52.

### [AND-56 · Реализовать результаты, таблицы и стабильные tie-breaks](https://linear.app/andrei-brazhnikov/issue/AND-56/realizovat-rezultaty-tablicy-i-stabilnye-tie-breaks)

Матч заканчивается один раз, обновляет played/points/goals; таблица с paging; решить разность мячей/забитые/предсезонный жребий.

**Приёмка:** 3/1/0 очков начисляются один раз; до/после restart таблица одинакова; ничьи по показателям имеют стабильный порядок; отчёты открываются кнопкой.

**Зависимости:** AND-55.

### [AND-57 · Реализовать одновременное повышение и понижение 2×2](https://linear.app/andrei-brazhnikov/issue/AND-57/realizovat-odnovremennoe-povyshenie-i-ponizhenie-22)

Итоговый план всех 5 лиг: топ-2 вверх, нижние-2 вниз, граничные исключения; внутри league_set, без стыковых.

**Приёмка:** Одна транзакция/season lock; 50 уникальных клубов и 10 в каждой лиге; один уровень за сезон; повтор rollover безопасен; история сезона остаётся неизменной.

**Зависимости:** AND-56.

## 04 · Развитие клуба

### [AND-58 · Добавить недельные тренировки и возрастной прогресс](https://linear.app/andrei-brazhnikov/issue/AND-58/dobavit-nedelnye-trenirovki-i-vozrastnoj-progress)

Фокусы навыков, форма/выносливость, возрастные коэффициенты, потолок потенциала и тренерские бонусы по GDD.

**Приёмка:** Один расчёт на клуб/неделю; навык/возраст ограничены валидными диапазонами; UI показывает результат; повтор job не тренирует дважды.

**Зависимости:** AND-45, AND-47.

### [AND-59 · Создать экономику клуба и денежный ledger](https://linear.app/andrei-brazhnikov/issue/AND-59/sozdat-ekonomiku-kluba-i-denezhnyj-ledger)

Баланс integer, журнал транзакций, зарплаты/спонсоры/билеты/призы; атомарные операции, reservations под ставки и контроль повторов.

**Приёмка:** Повтор начисления не удваивает баланс; параллельные расходы не дают недопустимый остаток; финансовый экран согласуется с ledger; правила зарплат/банкротства описаны.

**Зависимости:** AND-45.

### [AND-60 · Реализовать аукционный трансферный рынок](https://linear.app/andrei-brazhnikov/issue/AND-60/realizovat-aukcionnyj-transfernyj-rynok)

Листинг на 48 часов, ставки, резервы, освобождение проигравших резервов, продление анти-снайпера, расчёт после закрытия; окна и лимиты 16–25.

**Приёмка:** Параллельные ставки/закрытие безопасны; один владелец игрока; повтор settlement не переводит деньги/игрока дважды; проверка окна/минимальной цены/остатка; уведомления через outbox.

**Зависимости:** AND-59, AND-49.

### [AND-61 · Добавить персонал клуба и скаутские задания](https://linear.app/andrei-brazhnikov/issue/AND-61/dobavit-personal-kluba-i-skautskie-zadaniya)

Тренер/скаут/physio, зарплаты и бонусы; скаутские направления, сроки и отчёты; staff portrait bank Dugout.

**Приёмка:** Найм и расчёт зарплат атомарны; один отчёт на завершённое задание; статистика скрытого потенциала раскрывается по правилам; отдельные зрелые лица персонала.

**Зависимости:** AND-58, AND-59, AND-43.

### [AND-62 · Реализовать академию и выпуск молодых игроков](https://linear.app/andrei-brazhnikov/issue/AND-62/realizovat-akademiyu-i-vypusk-molodyh-igrokov)

Уровни академии, расходы, выпуск 17-летних, скрытый потенциал, сроки и лимиты состава.

**Приёмка:** Выпуск не дублируется; корректные позиции/возраст; нельзя превысить 25 игроков; затраты попадают в ledger; объяснимый экран академии.

**Зависимости:** AND-58, AND-59.

### [AND-63 · Добавить стадион, содержание и матчевый доход](https://linear.app/andrei-brazhnikov/issue/AND-63/dobavit-stadion-soderzhanie-i-matchevyj-dohod)

Уровни, вместимость, стоимость/срок расширения, доход с учётом лиги, формы и посещаемости.

**Приёмка:** Одно улучшение/начисление за событие; нет бесплатного повторного апгрейда; экономика проверена на сезонном прогоне; финансовая запись соответствует стадиону.

**Зависимости:** AND-59.

### [AND-64 · Добавить мораль, форму, опыт и старение футболистов](https://linear.app/andrei-brazhnikov/issue/AND-64/dobavit-moral-formu-opyt-i-starenie-futbolistov)

Сезонные/недельные расчёты по возрасту, опыту и морали; документировать связь реального времени и игрового возраста.

**Приёмка:** Диапазоны и скорости согласованы; повтор расчёта безопасен; изменения влияют на sim; сезонная симуляция не даёт мгновенных пенсионеров/навыков 20.

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

## 05 · Запуск и эксплуатация

### [AND-68 · Реализовать sender уведомлений и устойчивое live-табло](https://linear.app/andrei-brazhnikov/issue/AND-68/realizovat-sender-uvedomlenij-i-ustojchivoe-live-tablo)

Outbox dispatcher с lease/retry_after, приоритетами и стабильным message_id; не держать DB transaction во время Telegram call.

**Приёмка:** Telegram rate limits не останавливают sim; рестарт возвращает взятые lease; отправки отслеживаются; задокументирован риск дубля send после crash; edit повторобезопасен.

**Зависимости:** AND-52.

**Дополнение аналитики:** Приёмка аналитики: delivery означает Telegram accepted, не read. Попытки и outcome_unknown после crash отделены от логического события, без продления user session. Время queue/render/delivery измеряется отдельно от календаря; детали в AND-77.

### [AND-69 · Проверить восстановление после сбоев HDC и БД](https://linear.app/andrei-brazhnikov/issue/AND-69/proverit-vosstanovlenie-posle-sboev-hdc-i-bd)

Crash points: тик до/после commit, rollover, claim, bid/settlement и отправка outbox; пауза live-матча вместо залпового catch-up.

**Приёмка:** Голы/деньги/клубы не дублируются; менеджерам сохраняется окно решений после простоя; degraded mode и recovery runbook; периодический reconciliation jobs.

**Зависимости:** AND-52, AND-57, AND-60.

### [AND-70 · Подготовить backup, health и наблюдаемость на HDC](https://linear.app/andrei-brazhnikov/issue/AND-70/podgotovit-backup-health-i-nablyudaemost-na-hdc)

Метрики tick lateness/queue/outbox/failed jobs/диск; ежедневные backup вне HDC и restore drill; автозапуск после reboot.

**Приёмка:** Секреты не попадают в логи; backup восстанавливается в изолированной БД; health отражает DB/worker heartbeat; monitoring интегрирован в существующий HDC без нарушения соседних сервисов.

**Зависимости:** AND-39.

### [AND-71 · Провести нагрузочные, балансные и конкурентные проверки](https://linear.app/andrei-brazhnikov/issue/AND-71/provesti-nagruzochnye-balansnye-i-konkurentnye-proverki)

Несколько комплектов, одновременные матчи/регистрации/ставки; сезонные sim-прогоны; целевые показатели определить по измеренным ресурсам HDC.

**Приёмка:** Проверены race cases и аварии; измерены p95 tick lateness/callback latency; сезон сохраняет 10 клубов в лиге; документированы bottlenecks и лимиты для запуска.

**Зависимости:** AND-57, AND-60, AND-68, AND-69.

### [AND-72 · Развернуть готовый MVP на HDC и провести smoke](https://linear.app/andrei-brazhnikov/issue/AND-72/razvernut-gotovyj-mvp-na-hdc-i-provesti-smoke)

Существующий Windows Docker host; отдельный Compose project/volumes/secrets, без публичного PostgreSQL и Mini App. Запуск bot long polling после migration.

**Приёмка:** Проверены BotFather username/token и один polling instance; /start→claim→lineup→live→result; reboot/rollback/restore; соседние сервисы не перезапущены; deploy записан в runbook.

**Зависимости:** AND-71, AND-70, AND-82.

**Дополнение аналитики:** Дополнительный gate: AND-82 подтверждает точные воронки/зрелые когорты и исключение internal/AI до первого внешнего привлечения. Зафиксировать coverage_start; операторские отчёты доступны. Эксперименты AND-81 не блокируют MVP.

**Деплой · уточнение пользователя:** Решение пользователя 2026-10-11: локально управляемый ручной деплой. Основной путь: собрать/проверить образ здесь под архитектуру HDC → docker save → SFTP/SCP → SHA-256/image ID check → docker load на HDC → Compose --no-build. Альтернатива: commit/push здесь → git pull --ff-only выбранного проверенного SHA на HDC → локальный docker build там. Оба пути используют immutable release tag/GOALETTO_IMAGE и deploy/compose.release.yaml для migrate/bot/worker. Credentials только host-only, CI выполняет проверки и не деплоит. Приёмка AND-72: проверены согласованный путь, metadata/smoke/rollback предыдущего image, миграции до запуска worker и отсутствие изменений соседних сервисов. Документация: docs/deployment-hdc.md. Настройка пути не означает выполненный HDC deploy.

## Аналитика до привлечения игроков · AND-73–AND-82

Распределены по существующим вехам; это план реализации, не завершённый сбор. [Определения и метрики](analytics/measurement-plan.md), [каталог событий](analytics/event-catalog.md).

### [AND-73 · Зафиксировать контракт событий и метрики воронок Goaletto](https://linear.app/andrei-brazhnikov/issue/AND-73/zafiksirovat-kontrakt-sobytij-i-metriki-voronok-goaletto)

Исходный план уже записан в docs/analytics/measurement-plan.md и event-catalog.md; довести до контракта реализации, не объявлять сбор готовым. Опыт Frontline AND-24/26/27 адаптирован к недельной лиге.

**Приёмка:** Утверждены versioned event schema/allowlist и определения трёх KPI с n/N, UTC-окнами и зрелостью. manager_started создаётся до claim; AI/system/operator исключены из user activity. Чётко различаются Telegram accepted, просмотр по кнопке и неизвестное чтение. D1/D3/D7 не заменяют недельные матчевые возможности. Зафиксированы retention/доступы/удаление, версии, начало покрытия и миграция контракта. Проверены продуктовые примеры, не только список имён.

**Зависимости:** AND-40.

### [AND-74 · Сохранять события менеджеров в PostgreSQL без дублей и внутренних аккаунтов](https://linear.app/andrei-brazhnikov/issue/AND-74/sohranyat-sobytiya-menedzherov-v-postgresql-bez-dublej-i-vnutrennih)

События живут в application/infrastructure, domain чистый. Основа должна появиться до игрового /start; нынешний preview не становится исторической когортой.

**Приёмка:** Миграции analytics_managers/journey_events; псевдоним до получения клуба, отдельная restricted связь с Telegram ID. Успешные события и accepted commands коммитятся атомарно; rollback не оставляет успех. Update/effect и worker/business dedupe переживают restart. Явный реестр internal/test/operator, AI исключены из default aggregates. Нет raw callback/токенов/чатов; сессии только по allowlist user-действий; сроки хранения/доступы проверены. Внешний экспорт не блокирует игру.

**Зависимости:** AND-73, AND-45.

### [AND-75 · Добавить ссылки кампаний и атрибуцию привлечения через Telegram /start](https://linear.app/andrei-brazhnikov/issue/AND-75/dobavit-ssylki-kampanij-i-atribuciyu-privlecheniya-cherez-telegram)

Python-only bot, без публичного redirect, frontend и нового web API. Ограничение: наблюдаем принятый /start, а не чтение или клик до него.

**Приёмка:** Реестр opaque start-кодов <=64 допустимых символов, source/placement/date и ссылки t.me/GoalettoBot. direct/unknown/known разделены. First-touch сохраняется до claim и неизменяем; повторный /start — касание, не новый manager. Отчёт source→claim→готовность; internal исключён. Тесты валидных/неизвестных/повторных кодов. CTR/CAC не выдумываются без данных показов/кликов/расходов.

**Зависимости:** AND-74.

### [AND-76 · Инструментировать вход, состав и восстановление после непонятных действий](https://linear.app/andrei-brazhnikov/issue/AND-76/instrumentirovat-vhod-sostav-i-vosstanovlenie-posle-neponyatnyh)

Связать аналитику с реализацией AND-47/48/49; существующие задачи создают UI и gameplay, эта задача проверяет полноту e2e событий и диагностический контекст.

**Приёмка:** События start/offer delivery/claim/lineup open/save и optional previews связаны с маршрутом и версией. Автосостав не считается ручной готовностью. Категории stale/invalid/claim race/lineup invalid и доставка помощи; recovery — успешное целевое user-действие за 30 минут. Retry/rollback не удваивают ступени. Обходные пути учитываются отдельно; отчёт не объявляет причину путаницы по кнопке Назад.

**Зависимости:** AND-74, AND-47, AND-48, AND-49, AND-75.

### [AND-77 · Измерять участие в матчах отдельно от симуляции и задержек доставки](https://linear.app/andrei-brazhnikov/issue/AND-77/izmeryat-uchastie-v-matchah-otdelno-ot-simulyacii-i-zaderzhek-dostavki)

30 тиков + 3 минуты перерыва, недельное расписание: 24 часа без второго матча не означают отвал. Без предположений о присутствии онлайн по пассивному табло.

**Приёмка:** Явные match/report open, accepted/applied command, fixture finished и result delivery имеют корреляцию/дедуп. Eligible opportunity определяется владением до lineup lock; перенос/пауза по actual times, смена владельца отдельно. AI/ticks/delivery не дают user retention. Технические попытки accepted→commit→queued→render→Telegram accepted/failed/unknown измеряются отдельно, p50/p95 и очередь не включают календарное ожидание. Restart/uncertain send сохраняют правдивый статус.

**Зависимости:** AND-74, AND-52, AND-53, AND-55, AND-56, AND-66, AND-68.

### [AND-78 · Собирать добровольные причины остановки и отзывы внутри Telegram](https://linear.app/andrei-brazhnikov/issue/AND-78/sobirat-dobrovolnye-prichiny-ostanovki-i-otzyvy-vnutri-telegram)

Основа Frontline AND-27. Сначала реализовать и проверить на synthetic/internal, не отправлять сейчас сообщения реальным людям; основной игровой CTA сохраняет приоритет.

**Приёмка:** Вторичная кнопка после первого результата; помощь для незавершённого входа через 24 ч и один опрос после пропущенной зрелой матчевой возможности. Ожидание календаря не триггер. Кампания/менеджер уникальны; skip/opt-out terminal, cooldown >=7 дней, недоступность/unknown delivery безопасны. Конечные причины и optional comment <=500 символов в явном 30-мин окне, команды не перехватываются. Restricted текст, retention cleanup, без логов/labels. Доля ответов/skip показана; причины не приписываются молча ушедшим.

**Зависимости:** AND-76, AND-77.

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

**Приёмка:** Изолированная PG fixture с точными ожидаемыми n/N: direct/campaign/unknown, drop до claim, manual/autolineup, AI/internal, retry/rollback/restart, delivery unknown, transfer ownership, перенос/межсезонье/первый матч через7д, незрелыйD7. Delivered без user action не retention. SQL/report/dashboard согласованы, доступы/cleanup/feedback caps/экспорт проверены. Runbook с coverage_start и troubleshooting; gate AND-72, без production DB.

**Зависимости:** AND-80, AND-71.
