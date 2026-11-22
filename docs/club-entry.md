# Выдача клуба и владение · backend AND-47/50

Актуальный runtime AND-47/119: [готовый запас и общий календарь](world-reserve.md).
Инициализация/исторические бот-матчи вынесены в worker, в регистрации их нет.
Описание старой поставки ниже сохраняет историю; требование новых уникальных
иллюстраций и собственного Saturday-календаря больше не действует.

`begin_game` открывает явный игровой вход: coverage, Manager/pseudonym и источник
до claim, затем собственный клуб либо предложения пятой лиги. Preview-router его
не вызывает; игровой Telegram /start ещё не подключён. Нет новых production когорт.

Claim использует общий advisory transaction lock до Manager/club/ownership locks.
Только free tier5, одно активное владение на manager/club, уникальный claim operation.
Повтор возвращает тот же период, conflicting/ended operation отклоняется.
Список и подтверждение показывают стартовый бюджет. Состав открывается после
подтверждения. Передача доступна после завершения матча с заблокированным составом.

Новый период владения начинается с 600 000, 2 500 болельщиков, стадиона на 5 000,
академии уровня 1, одного базового главного тренера и 20 стартовых футболистов.
Предыдущие действующие игроки и сотрудники выходят на рынок с прежними данными.
Футболисты, для которых объявлен последний сезон, завершают игровую карьеру.
Старые зарплаты закрываются в момент передачи, незавершённая стройка отменяется.
У академии начинается новый набор; старые наборы и подписания сохраняются.

Финансы выравниваются отдельной записью, а история болельщиков и инфраструктуры
читается с учётом даты передачи. Таблица, история матчей, клубная идентичность
и условия действующего сезонного спонсора сохраняются. Legacy owner projection
без периода не считается свободным клубом до reconciliation.

Если free tier5 нет, initializer создаёт следующий полный комплект5×10×20 с будущим
Saturday18UTC kickoff и уникальными портретами. Catalog preflight/initialization/activation
в savepoint; exhaustion возвращает no_capacity, а не частичный комплект/повтор лица.
Текущий whole банк покрывает первый комплект1000 футболистов; следующий требует
дополнительных уникальных identities/age coverage. Генерация новых картинок здесь
не выполнялась. First world также может создаваться этим кодом; CLI seed остаётся
отдельным операторским путём. Backend use case не вызывался на реальной БД.

## Долговечные действия и полгода

GameActionReceipt хранит operation/effect/manager/ownership/club/kind/time; FK
подтверждает область владения. Он independent от optional/expiring journey analytics:
opt-out/internal/retention не отменяют idempotency игры. Source/личный текст в receipt
отсутствуют. Повтор действия не переносит deadline снова. Запись/событие/lease jobs
разделяют caller transaction; нет самостоятельного commit или сетевых вызовов.

Срок: last_game_action_at +6 календарных месяцев, UTC, clamp последнего дня месяца.
Только ownership allowlist явных действий; preview/feedback/delivery/tick не продлевают.
Locked rows перечитываются populate_existing после ожидания, чтобы не работать со
старым identity-map state. При освобождении projection owner=NULL, period inactive;
accepted commands этого периода cancelled, состав/финансы/история сохраняются.
End timestamp — deadline, recorded_at — фактическое выполнение; retry release безопасен.
Свободные верхние лиги по-прежнему не предлагаются новичкам.

Lease jobs pending и durable: expiry6mo, default reminders1/3/5 календарных месяцев.
LeasePolicy позволяет задать ordered distinct intervals1..5 либо отключить reminders.
Manager reminders_enabled/bot_available останавливают новые reminder jobs/outbox,
expiry планируется всегда. Jobs проверяют сохранённый last_action_at; после возврата
stale work не напоминает. Reminder outbox dedupe ownership/epoch/interval.
Attempt counters не сбрасываются при reschedule — сохраняется уникальность diagnostics.

Lease-worker сначала пробует advisory lock, чтобы не ждать его, удерживая Job lock
против возвращающегося менеджера, который обновляет те же jobs. Busy → deferred.
Domain clock не читает wall clock/БД; application получает явное UTC время.

## Осталось до готового интерфейса

Игровой start/выбор/preview/claim и navigation подключены opt-in флагом
`GOALETTO_GAME_ENABLED`. Реальный dispatcher проверен на PostgreSQL с fake Telegram.
AND-50 backend реализован в рамках entry; settings UI/opt-out остаются AND-50.
[Sender](outbox-delivery.md) повторно проверяет owner/inactivity/preferences после
claim и перед сетью, подтверждает недоступность и сохраняет историю попыток.
Новый игровой /start сохраняет bot_confirmed_at: старый blocked response не отменяет
более новое подтверждение доступности. Sender по умолчанию выключен; реальных
отправок не было. Backend очередь не означает доставку. AND-75 start path подключён.

Проверка SQL-recording: один claim на retry, tier5 predicate/club lock, budget unchanged,
receipt idempotency без analytics,6 calendar months/end month, release retry/early return,
stale/opt-out reminders, expiry без новых reminders, exhausted expansion без activation.
Offline0008 schema/ALTER/DDL; реальный PG rollback/concurrency/triggers не проверены.
Новые БД/контейнеры, Telegram polling и production не запускались.
