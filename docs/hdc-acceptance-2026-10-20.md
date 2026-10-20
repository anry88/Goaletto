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

## Границы приёмки

Это закрытый запуск для внутренних аккаунтов. Полный Windows reboot, проверка
автозапуска Docker после login/reboot, отдельная предыдущая версия для настоящего
application rollback и подключение HDC alerts остаются отдельной приёмкой.
Не перезагружать общий HDC ради этой проверки без окна для соседних сервисов.
AND-69/70/72 не закрываются одним наличием контейнеров, backup или heartbeat.
AND-80/82 требуют фактической приватной Grafana организации/логина и сверки отчётов;
локальные SQL проверки не заменяют эту HDC-приёмку и допуск внешних игроков.
