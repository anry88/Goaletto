# Stars, VIP и PostgreSQL на D · 2026-10-20

## Правила

`/settings`: одна доступная попытка переименования, либо покупка одной за 50 Stars,
если запас пуст. Все попытки, включая первоначальную, используются только в
межсезонье. Отказ не расходует попытку. Сохраняются ограничения 2–30 символов,
латиница, цифры, пробел, дефис, апостроф, точка и локальная проверка запрещённых
слов. Предпросмотр и подтверждение обязательны. Каждое изменение имеет отдельный
immutable receipt; прошлые имена в матчевых документах не переписываются.

VIP клуба стоит пока **1 Star**, действует **один календарный месяц** от успешной
оплаты. День месяца сохраняется, при коротком месяце берётся последний день
(31 января → 28/29 февраля). Продлений/автосписаний нет; новую покупку можно
сделать после окончания статуса. В межсезонье активный VIP пополняет пустой запас
один раз на клуб+сезон. Уже выданная попытка сохраняется после окончания VIP.
Запас не превышает одну попытку. Имя и исход матча, финансы, характеристики,
рост, подбор соперника и другие игровые правила VIP не меняет.

Карточка клуба и матч показывают после названия `👑 VIP`, таблица — только `👑`.
Статус вычисляется по текущему времени, без записи короны в immutable match state.
Описание в настройках объясняет публичный статус и поддержку проекта.

## Платежи и поддержка

Telegram invoice: XTR, пустой provider token, ровно один продукт. Перед
pre-checkout проверяются плательщик, сумма, валюта, текущее владение, доступность
товара, срок заказа и allowlist. Только `successful_payment` выдаёт право.
Повтор одного charge идемпотентен; каждый отдельный фактический платёж сохраняется.
Поздний/несоответствующий платёж не пропадает: receipt `unfulfilled` доступен
через поддержку. Telegram IO выполняется после commit и вне транзакции.

`/paysupport` показывает собственные покупки без выполненного возврата с пагинацией.
`/paysupport PAYMENT_UUID причина` создаёт один durable запрос. Отправка идёт в
**тот же админ-чат, что у RiverKing**: числовой ID перенесён на HDC из его host-only
конфигурации, токены и другие настройки не копировались. Команды администратора:

- `/refund REQUEST_UUID` — одобрить возврат;
- `/reject REQUEST_UUID причина` — отказать;
- `/ask REQUEST_UUID вопрос` — запросить уточнение;
- покупатель отвечает `/answer REQUEST_UUID ответ`.

Личный админ-чат требует совпадения sender/chat; групповой — текущего статуса
администратора Telegram. Worker фиксирует намерение до `refundStarPayment`.
При timeout/потере ответа исход `unknown`: повторного автоматического вызова нет.
Администратор проверяет исход и может явно повторить `/refund` с тем же charge;
`CHARGE_ALREADY_REFUNDED` подтверждает уже состоявшийся возврат. Сервисное событие
`refunded_payment` также подтверждает его идемпотентно. Возврат отключает VIP и
аннулирует неиспользованные права этого платежа; использованные переименования
и историю не откатывает. Заявка доступна прежнему плательщику после потери клуба.

Нужны `GOALETTO_PAYMENTS_ENABLED=true`, host-only
`GOALETTO_PAYMENTS_ADMIN_CHAT_ID`, запущенный worker и
`GOALETTO_OUTBOX_ENABLED=true`. Без admin chat новые invoices не выдаются.
Таблицы 0049 сохраняют заказы, charge receipts, права, переименования и поддержку.
Реальный production платёж, **включая внутренний тест**, устанавливает защиту
данных: development reset после него запрещён. Downgrade с использованной новой
историей отказывает. Секреты и charge IDs в operational stdout не выводятся.

Первичные API-контракты: [Telegram Stars](https://core.telegram.org/bots/payments-stars),
[refundStarPayment](https://core.telegram.org/bots/api#refundstarpayment),
[RefundedPayment](https://core.telegram.org/bots/api#refundedpayment).

## Фактический перенос на D

Goaletto PostgreSQL остановлен вместе с его bot/worker, PGDATA скопирован с
сохранением файлов и прав из `goaletto_postgres_data` в
`D:/HomeDataCenter/services/goaletto/data/postgres`. Добавлен последним
`deploy/compose.data-d.yaml`. Проверены PG_VERSION=17, global/base/pg_wal,
сохраняемые UID/GID 70:70 и mode 700; Windows Docker Desktop account получил
права только на каталог данных. `.env` остаётся приватным.

После старта inspect подтвердил **bind** с этого пути D на
`/var/lib/postgresql/data`. Сразу после переноса сохранены 100 клубов, 2100 людей,
270 матчевых тиков, схема 0044. Сброс не потребовался. Соседние контейнеры
сохранили ID и StartedAt. Старый named volume сохранён как исходная копия и
больше не обслуживает работающую БД. Это не перенос всего диска Docker Desktop:
image layers соседних проектов остаются в его прежнем хранилище.

## Проверки реализации

Ruff check/format, mypy (209 application modules), offline migration SQL прошли.
1154 unit/offline теста; 14 PostgreSQL-сценариев на единственной локальной
Goaletto development DB: точные invoice terms, pre-checkout без выдачи, повторы,
лимит попыток, смена владельца, календарный месяц, реальные aiogram handlers,
админ-права/уточнение/ответ, успешный и неизвестный refund, live-корона без
изменения immutable match snapshot или бюджета. Telegram API заменён заглушкой.

Отдельно старый код заполнил 0044: 50 клубов, 1000+ людей, контракты, ledger,
долг и завершённый 30-тиковый матч. Upgrade до 0049 сохранил исходные колонки
85 таблиц, кроме согласованного debt conversion. `alembic check` не нашёл
расхождений; pending-support index установлен. Добавленный fake payment receipt
запретил downgrade с потерей истории, transactional DDL сохранил head и receipt.
Новую тестовую БД не создавали. **Реальные Stars не покупали и не возвращали**:
личная платёжная приёмка владельца за 1 Star остаётся отдельным действием.

## HDC release и backup

Развёрнут main `42db6df339782bb376134f3d838b205ce962fee3`,
`goaletto:42db6df3-hdc` (linux/amd64, runtime target). Архив 76 319 232 bytes,
SHA-256 `69ca640aab13e1c1cc94929778046ef70341d6d4f1261c91c7ab082b7180f525`,
config digest `sha256:8f43f04f978e2d2c295f0f22fb64c84e820423f4407f441bec2fd4948b1d36a7`.
[CI реализации](https://github.com/anry88/Goaletto/actions/runs/39421516903) — success.

При остановленных только bot/worker применён upgrade 0044→0049. Online
`alembic check` прошёл в том же image на HDC. PostgreSQL не пересоздавался во
время app rollout; D mount сохранился. После upgrade: 100 клубов, 2100 людей,
270 тиков, 0 платежей. Все 19 соседних контейнеров независимо сравнены с
baseline до переноса: ID/StartedAt не изменились. Bot/worker используют новый
image, running=true, restarts=0; PostgreSQL и worker healthy.

Read-only Telegram getMe/getMyCommands проверяет `/settings` и `/paysupport`
в `all_private_chats`, без invoice или сообщений. Private Prometheus endpoint
bot отвечает, health CLI worker проходит. Allowlist сохранён, внешнего запуска
и реальных денежных операций не было. Host-only support chat настроен.

Свежая off-host копия схемы0049 забрана на этот Mac:
`~/Documents/Goaletto Backups/goaletto-20261020T122436Z-9fbbc8f8.dump` и manifest,
1 573 188 bytes. Проверены PGDMP, размер и SHA-256; прежние копии сохранены.
Пользователь может лично проверить VIP за1Star через `/settings`, затем
`/paysupport`; это не было подменено fake-платежом на HDC.
