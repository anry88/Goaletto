# Outbox sender и live-табло · AND-68

Проверенная и доработанная реализация агента A. Совместимость: version 1,
`fixture_reminder`, `match_board`, `match_result`, `ownership_reminder` из текущих
`application.match_runtime` / `application.game_actions`. Новых игровых правил нет.

## Запуск и границы

Worker запускает независимые async-циклы jobs и sender. Sender получает отдельный
`Bot`, используемый только для исходящих сообщений; polling остаётся в bot process.
Telegram rate limits / сетевые ошибки не блокируют матчевый цикл.

По умолчанию `GOALETTO_OUTBOX_ENABLED=false`. После применения миграций и подготовки
игрового потока оператор может включить `GOALETTO_OUTBOX_ENABLED=true` и задать
`GOALETTO_BOT_TOKEN`. Если sender включён без токена, worker отказывается стартовать.
Не запускать второй polling-процесс с тем же токеном. Миграции и сценарии sender проверены на единственной рабочей локальной
PostgreSQL DB с fake Telegram. Основной календарь, polling и реальные отправки
не запускались.

Один шаг (`infrastructure.outbox_runner.process_one_delivery`):

1. Короткая claim-транзакция, `FOR UPDATE SKIP LOCKED`, bounded scan 25, обычно
   один message/шаг. Приоритеты уже записаны producers: result 100, board 50,
   fixture reminder 30, ownership reminder 10. Commit claim с lease 120 секунд,
   уникальным token и номером попытки.
2. Новая короткая preflight-транзакция: свежая проверка lease, владельца, адресата,
   preferences, доступности и актуальности fixture/reminder. Commit перед сетью.
3. Telegram-вызов без DB transaction. Один запрос ограничен 25 секундами,
   вся операция edit + возможный replacement send — 30 секундами.
4. Отдельная finish-транзакция с fence token + attempt + deadline, записью
   результата/истории/аналитики и освобождением lease.

`sent` / `failed` не переписываются повторным finish. После смены lease старый
sender не может изменить pending receipt новой попытки. Sweeper и finish
захватывают outbox → board → attempt history; sweep ограничен 25 строками.
Claim также восстанавливает историю истёкшей предыдущей попытки.

## Табло и порядок

`outbox_boards` содержит единственную запись на `(fixture_id, ownership_id)`:
canonical key, chat, message_id, сохранённую revision и общую delivery lease.
Одновременно подготавливается только одна отправка этого stream. Lease сохраняется
между транзакциями и защищает также первый send; одного SKIP LOCKED на разных
outbox rows для этого было бы недостаточно.

Следующие board payloads редактируют сохранённый message_id. `match_result`
использует тот же stream и обновляет то же сообщение; terminal revision 1000.
Поздний retry старого тика не откатывает счёт: меньшая/равная revision закрывается
как superseded либо stale fixture. Начальный backlog может сразу показывать
результат, а старые live payloads уже не отправляются.

Удалённое табло (`message to edit not found`) заменяется новым send под той же
lease. `message is not modified` считается успешным edit. Blocked/chat unavailable
при edit не вызывает дополнительный send. BadRequest с ошибкой форматирования
терминален и не объявляет пользователя недоступным.

Текст HTML-экранирует названия, показывает игровые минуты и перерыв.
Для abandoned явно сказано, что итоговый результат не присуждён.

## Retry и неопределённость

Transient/unknown backoff: `min(600, 5 × 2^(attempt - 1))`. `retry_after` Telegram
имеет приоритет как нижняя граница без сокращения до 600/900 секунд. Он сохраняется
в deadline строки и board stream; текущий sender также приостанавливает свои
исходящие запросы. Другие sender-процессы по независимым stream могут ещё встретить
тот же token-wide rate limit; это не глобальный распределённый Telegram limiter.

Все повторяемые ошибки, включая 429, ограничены восемью попытками на outbox row.
После исчерпания — `failed`; unknown требует ручной сверки, никогда не `sent`.
Новые тики не обходят cooldown своего board stream. Ошибки в логах — только класс;
raw response/exception text, токены и DSN не записываются.

`outbox_delivery_attempts` хранит claim/network-start/finish timestamps и bounded
outcome независимо от opt-out/retention journey. Network-start обозначает
закоммиченный preflight непосредственно перед транспортом, не доказательство
получения запроса Telegram. Crash после этого оставляет `sending`; reclaim/sweep
пишет `unknown`. Crash до preflight — `transient`, сеть ещё не вызывалась.
Эта история позволяет измерять подготовку/вызов и отделять календарное ожидание;
полные отчёты/операционные метрики AND-77 остаются отдельным этапом.

## Свежие guards и аналитика

- Период владения активен и не истёк; текущий club owner/manager/chat совпадают.
- Fixture относится к клубу периода. Fixture reminder не отправляется после старта;
  board требует live/paused; result — finished/abandoned.
- Ownership reminder требует текущие preferences, тот же club и last action epoch.
- bot_available=false подавляет сеть. Blocked/chat unavailable сохраняет отказ;
  более новый вход `/start` подтверждает bot_confirmed_at и не даёт старому
  сетевому ответу снова выключить доступность.
- result_delivered / bot_unavailable записываются атомарно с finish, origin system,
  без user session. Opt-out/internal и отсутствие coverage не мешают бизнес-дедуп.
- Sender не обновляет ownership last_game_action_at/expires_at или user session.

Guard подтверждает состояние непосредственно перед вызовом. Изменение владения
или preferences после preflight commit и уже начатый Telegram call нельзя сделать
атомарными; блокировки игровых строк на время сети не удерживаются. Старые
callbacks должны независимо проверяться application authorization.

## Ограничения и миграция

Telegram send и PostgreSQL commit не атомарны. Crash после успешного send до finish
может дать повторное новое сообщение. Пауза процесса дольше lease также может
оставить уже начатый сетевой запрос неопределённым. Stream lease/timeout/fence
сокращают эти случаи, но не обеспечивают exactly-once Telegram delivery.
Bounded retries также не гарантируют доставку каждого сообщения. `sent` означает
подтверждение API, не прочтение пользователем.

Миграция `0009_outbox_delivery`: additive outbox columns/checks/indexes,
board registry, delivery attempt history, manager availability confirmation и
backfill priority/key старых payloads. Frozen DDL не импортирует модели.
Downgrade требует предварительно reconcile/export доставленных сообщений,
lease и попыток; активная история не удаляется молча.

Тесты используют SQL recording, fake transaction contexts и fake aiogram responses.
Сеть проверяется на отсутствие **действительно открытых fake-транзакций**, а не
на константу в transport stub. Offline upgrade/downgrade и сравнение DDL выполнены.
Настоящие PostgreSQL claim/stream lease/edit и общий игровой маршрут проверены
на единственной Goaletto DB. Новые тестовые DB не создаются; reset разрешён
пользователем пока нет реальных пользователей. Telegram/HDC не проверялись.
Игровой `/start` и кнопочные переходы подключены; preferences UI остаётся AND-50.
