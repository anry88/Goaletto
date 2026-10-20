# HDC: закрытый запуск и эксплуатационная приёмка · 2026-10-20

Реальный Windows HDC, Linux Docker x86_64, Compose 5.1.4. Сервис размещён в
`D:\HomeDataCenter\services\goaletto`, отдельные сеть `goaletto_default` и volume
`goaletto_postgres_data`. БД `goaletto` не имеет опубликованного порта. Один bot
polling, отдельный worker jobs/sender, доступ только двум прежним внутренним
аккаунтам. Автоматический feedback выключен; внешнее привлечение не включалось.

## Релиз и изображения

Приложение собрано локально из чистого `5b82d89e51f610f82019f90c5b314efd7dad80f9`,
`--platform linux/amd64 --target runtime`, tag `goaletto:5b82d89e-hdc`.
Архив image: 75 900 416 bytes,
SHA-256 `d5960b2041faa5a1db4d2e4b46ab93aa7cad47d3968131fa5bda9d6ec9ca4552`.
Canonical config digest:
`sha256:bc1c803606ee0c9e8ca6f20fc72731d5bcc0efcc16bb5aa913eeefcfc030bc9d`.
Локальный containerd image/index ID отличается от classic Docker config ID:
`sha256:0b111627837d19724631684529fc65c9a85e67e90ab48306d7b4d1c70db2cd30`.
Оба записаны в release manifest; на приёмнике проверены архив, config и архитектура.

Существующие 1 080 лиц / 6 860 вариантов упакованы отдельно: 13 722 файла,
639 580 160 bytes, SHA-256
`0be227ec8236b5d2fcfcb16c325eb2ca3c49f4d7e20983ae808bdc36b2fff138`.
Каталог на D: `releases/portraits-0be227ec8236b5d2/assets/portraits/whole-v2`,
подключён read-only через `deploy/compose.assets.yaml` во все три app-сервиса.
Все image/mask refs проверены; локальный amd64 image отрисовал 96/256px.
Исходники и отклонённые эксперименты сохранены в Git, в runtime-пакет не входят.
Лица/маски и игровые коэффициенты не изменялись, новой генерации не было.

Системный диск C: имел около 1,4 GB свободного места. Пакеты/портреты/backup
лежат на D:, большая копия исходников в Docker layers не создаётся. Лимиты
postgres/bot/worker: 512 MiB / 512 MiB / 1 GiB, CPU 1/1/2. Это ограничение
изолированного сервиса, не заявление о нагрузочной ёмкости HDC.
Внутренний матч дал текущий снимок 61,4 / 197,5 / 195,5 MiB соответственно;
это один наблюдаемый запуск. Все три контейнера имеют `unless-stopped`,
публичных портов нет. Это конфигурация restart, а не доказательство host reboot.

Каталог сервиса имеет приватный Windows ACL для администратора/SSH и SYSTEM.
Фактическому пользователю Docker Desktop даны чтение атрибутов родительских
каталогов и RX на пакет изображений; это не наследуется на `.env`.
Host-only HDC `.env` собран из нужного bot token/allowlist и нового пароля БД;
локальный `.env` целиком не переносился, секретов в release/Git/выводе нет.

## Backup с HDC на этот Mac

Пользователь выбрал папку `~/Documents/Goaletto Backups` на рабочем Mac.
`scripts/backup_hdc.py` по SSH alias `hdc` проверяет единственный запущенный
postgres контейнер проекта Goaletto, имя БД и схему, делает custom pg_dump,
проверяет pg_restore --list, затем забирает архив через SCP. PostgreSQL наружу
не открывается; пароль не попадает в argv, stdout или manifest.

Локальная публикация пары `.dump` / `.manifest.json` происходит только после
сверки PGDMP header, размера и SHA-256 с HDC. Папка 0700, архив/manifest 0600;
частичный transfer не оставляет успешную пару. Готовые имена не перезаписываются,
автоматического удаления старых архивов нет. `.backup.lock` исключает overlap.

Установлен пользовательский LaunchAgent `local.goaletto.hdc-backup`:
`~/Library/LaunchAgents/local.goaletto.hdc-backup.plist`. Проверка при входе,
в 03:30 местного времени и ежечасно для повторной попытки; новая копия нужна,
если нет проверенной локальной пары моложе 20 часов. Журналы — `backup.log` и
`backup-error.log` в той же приватной папке. Для работы Mac должен быть включён,
пользовательская сессия доступна, HDC/SSH достижимы. Сон/отсутствие LAN не являются
успешным backup; следующая попытка повторяет его, retention прошлых копий сохраняется.

Проверка агента:

```bash
launchctl print gui/501/local.goaletto.hdc-backup
.venv/bin/python scripts/backup_hdc.py
```

Для изменения расписания менять только этот plist, перезагружать только его
через launchctl bootout/bootstrap. Репозиторий и `.venv` должны оставаться по
указанному в plist пути; перенос требует обновления ProgramArguments.

После завершённого матча создана свежая проверенная off-host пара
`goaletto-20261020T084105Z-19a4706b.dump` / `.manifest.json`, **1 497 077 bytes**.
Она уже содержит финальный результат и receipts; предыдущие архивы сохранены.

## Restore и обрыв БД

Настоящий restore выполнен в единственную Goaletto application DB на HDC.
Перед ним остановлен только worker, polling ещё не работал. Проверенная копия
`goaletto-20261020T074818Z-75916c2b.dump` забрана на Mac, затем именно эта копия
возвращена на HDC; SHA-256 повторно проверен. `pg_restore --single-transaction
--exit-on-error --clean --if-exists --no-owner` успешно восстановил данные.
Количество строк и md5 содержимого каждой из 85 таблиц полностью совпали.
Это проверка конкретного backup, не требование сохранять disposable dev-мир.

Ранний операторский seed пропустил стартовые 600K у первого комплекта; до
выдачи клубов мир пересоздан с правильным капиталом. Новые backup снимаются
уже с исправленного мира. Оба комплекта прошли настоящие прошлые bot jobs,
запас достиг 20 свободных tier-5 клубов, preparing_sets=0 до включения polling.

На HDC остановлен и поднят только PostgreSQL Goaletto. Health при отключённой
БД отказал. Тот же worker boot восстановил jobs/sender без перезапуска;
приёмка потребовала **новых** successful timestamps после старта PostgreSQL,
а не просто свежести старого heartbeat в пределах 120 секунд. Наблюдаемые
success_age после проверки: jobs 2,10 с, sender 2,53 с. Другие БД не затронуты.

## Живой матч, recovery и rollback

В Telegram выполнены `/start` → выбор/claim Sakura → `/lineup` → ручное сохранение
капитана → `/match`. Единственная ownership внутренняя; внешних менеджеров нет.
Первый настоящий league fixture `685e96e7-f34a-57c1-bc43-49f67e1a5c9a`
(Teranga — Sakura) оператором перенесён в пределах назначенной UTC-даты на
2026-10-20 08:05:17 UTC. Обычные 60-секундные тики не ускорялись.

Настрой «атака» принят через Telegram и применён ровно в tick 5. После остановки
только worker checkpoint tick 5, счёт 0:0 и hash
`e67250604e2a3abbd64067b0412dc699caaf5932d254edbf183e4c2cc65e662e`
не изменились. Worker остановлен около 08:10:22 и возобновлён около 08:11:48.
Recovery receipt в 08:11:54.373873 сохранил этот hash; следующий тик назначен на
08:12:55.159370 — более 60 секунд на решение. Не было залпового catch-up или
повторного применения команды. Telegram доставил уведомление о восстановлении.

Второй immutable image `goaletto:b4df7333-hdc` собран из
`b4df7333bcdaf0d518a604f2cfcc77b2fb71d165`: 75 900 928 bytes, SHA-256
`cf88230583c129ea5e9b2c4a24b89f6939753d5d87eadde094511f0d5d98c638`,
config digest `sha256:0a6320c0aeb4cb9f6c35394b7ce3833c6db44a02f88dc1377794952372737b04`.
Проверены загрузка, переход bot/worker на второй image и возврат к `5b82d89e`.
Матч продолжился, health jobs/sender успешен, failed_jobs=0. Это проверка
механики двух релизов с одинаковыми application-кодом/схемой, не обещание
совместимости произвольных schema downgrade. Оба образа сохранены на HDC.

Матч завершён в **08:39:55.784884 UTC**, Teranga **1:2** Sakura. Независимый
replay из starting_snapshot и принятых commands подтвердил **все 30 tick hashes,
события каждого тика и финальный checkpoint**. Final hash
`ce847604d2c332cb28f79ab29ec8c0a81d42e728b0f29df64d77b3abd205d9f4`.
Один MatchResult, один билетный ledger receipt, standings played=1 у обоих клубов.
`match-practice-v2` применён один раз для 24 реально участвовавших футболистов;
обе автоматические замены вызваны травмами. У 40 игроков заявки active_match_id
очищен, recovery доведена до финала с контрактной секундной точностью.
Failed jobs=0. Единственное итоговое outbox-уведомление имеет sent, attempts=1
и настоящий Telegram message ID; финал и `/match` → отчёт проверены в Telegram.
В `/match` показан следующий настоящий матч и срок подготовки состава.

## Grafana на HDC

По явному уточнению пользователя используется **Main Org. (1)** и папка
**Goaletto operator** в существующем Grafana 12.1.0. Новая организация не создавалась.
Агрегаты доступны участникам общей организации; ограничения папки не скрывают
источник от Explore. Это осознанная настройка владельца, заменяющая прежнее
требование отдельной приватной организации. Anonymous API получает 401. В Main Org. при проверке один Admin и один Viewer;
участников/роли не меняли, общей организации предоставлены только агрегаты.

Создан отдельный `goaletto_grafana`, только `goaletto_operator_reader`,
NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOREPLICATION/NOBYPASSRLS, pool/connection limit 2.
Роль не имеет прав ни на одну public table/view. Реальный login проверен в
READ WRITE: запрещены managers, feedback_comments, journey_events, UPDATE clubs,
CREATE public table и SET ROLE goaletto. Все 14 SQL панелей выполнились; шесть
journey-отчётов совпали с application CLI на интервале
2026-10-19 08:18:48.945479 — 2026-10-20 08:18:48.945479 UTC. Внешних starts=0:
внутренний smoke не добавляет людей в публичную воронку.

Источник и provider добавлены отдельными `goaletto.yaml` в существующий
`D:/Utilities/grafana-enterprise-12.1.0.windows-amd64/grafana-v12.1.0/conf/provisioning`.
Пароль сгенерирован на HDC и хранится только в datasource YAML с приватным ACL;
в репозиторий/argv/вывод не попадает. Dashboard JSON — в `data/goaletto-dashboards`,
provider path `/var/lib/grafana/goaletto-dashboards`. PostgreSQL TLS disable относится
только к приватной Docker-сети одного хоста, не к внешнему подключению.

В existing `D:/HomeDataCenter/compose/docker-compose.observability.prod.yml`
изменён только граф сетей Grafana: добавлена external `goaletto_default`.
Существующие сети/volumes/настройки сохранены. Пользователь явно согласовал
создание login, сохранение секрета, приватное подключение и перезапуск Grafana.
Пересоздан только `hdc-grafana`; остальные игры/БД не перезапускались.
Все 19 соседних контейнеров работают, у остальных 18 StartedAt раньше начала
этой работы; новых перезапусков, кроме согласованного Grafana, нет.
Перед правкой исходный Compose сохранён в Goaletto `grafana/observability-before.yaml`.
При пересоздании observability network Goaletto должна уже существовать;
запускать Compose выборочно, без общего `down`.

Панели: [вход и участие](https://grafana.tg-games.com/d/goaletto-launch),
[доставка и worker](https://grafana.tg-games.com/d/goaletto-operations).

## Retention и export на HDC

`analytics_coverage` хранит начало 2026-10-20 07:52:25.860710 UTC и retention
180 дней. Read-only проверка дала 0 истёкших кандидатов во всех четырёх таблицах.
Очистка выполнена дважды с защитой под блокировкой: при любом кандидате на удаление
проверка отказала бы до очистки. Ownership, игровые receipts и свежие analytics
counts совпали до/после. Реальное удаление и точные временные границы остаются
доказанными локальной hand-count PG матрицей; на HDC проверен только текущий no-op.

`journey_export` имеет ровно девять согласованных колонок и security_barrier,
PUBLIC grants на чувствительные analytics relations отсутствуют. Внешних строк 0.
Проверена header-only выгрузка `operator-exports/journey-export-20261020T0828.csv`
с приватным Windows ACL. Datasource login не имеет доступа даже к этому view.
Maintenance — явный операторский CLI из [launch gate](analytics/launch-gate.md);
автоматическое расписание journey cleanup в этой работе не добавлялось.

## Границы приёмки

Это закрытый запуск для внутренних аккаунтов. Полный Windows reboot, проверка
автозапуска Docker после login/reboot и подключение HDC alerts остаются отдельной
приёмкой. Не перезагружать общий HDC ради проверки без окна для соседних сервисов.
AND-69/70/72 не закрываются одним наличием контейнеров, backup или heartbeat.
Внешние опросы, реклама и admission людей не включены. Аналитические панели/права, текущий retention и restricted export HDC проверены;
это не выдаёт разрешение на внешние рассылки.
