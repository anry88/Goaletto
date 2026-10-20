# Runbook резервного копирования Goaletto · AND-70

2026-10-20: настроен реальный backup с HDC на рабочий Mac в
`~/Documents/Goaletto Backups`, проверен фоновый LaunchAgent и restore именно
из off-host копии. [Точная приёмка и расписание](hdc-acceptance-2026-10-20.md).
Запуск вручную: `.venv/bin/python scripts/backup_hdc.py`. Checksum и размер
проверяются до публикации manifest; готовые архивы не перезаписываются.

Сохранение disposable dev-мира не является обязательным условием reset:
пользователь разрешил свободное пересоздание точной БД Goaletto. Для самого
restore-drill нужен проверенный архив, из которого проверяется восстановление.

Инструмент: `python scripts/backup_goaletto.py`. Даёт проверяемый `pg_dump`
одной явно согласованной базы `goaletto` в custom-формате плюс SHA256-манифест.
Настоящие dump/restore проверяются Codex на единственной development Goaletto DB.
Для HDC используйте `scripts/backup_hdc.py`: off-host копия и LaunchAgent на Mac
настроены и проверены 2026-10-20; ниже остаются переносимые команды общего CLI.

Связь: часть AND-70. Не дублирует read-only diagnostics CLI
(`goaletto-diagnostics`, см. [runtime-diagnostics-runbook.md](runtime-diagnostics-runbook.md)):
диагностика — проверка живого состояния, этот инструмент — архив и восстановление.

## Границы

- Только одна база `goaletto`. Неверное имя отклоняется до dump
  (`WRONG_DATABASE`); перечисления «всех баз» и backup общих Docker volumes нет.
- Target — явная метка проекта (`local-goaletto`, `hdc-goaletto`), обязательный
  `--target`. Wildcard/volume (`all`, `*`, `all-databases`, `postgres_data`)
  отклоняются (`WRONG_TARGET`). Target никогда не определяется только по номеру
  порта: локально Goaletto слушает 5433 (5432 занят другим проектом), а на HDC
  внутри собственного Compose сеть и порты могут отличаться
  (см. [deployment-hdc.md](deployment-hdc.md), `compose.yaml`,
  `compose.local.yaml`, `deploy/compose.hdc.yaml`).
- По умолчанию dry-run: печатает безопасный план без пароля и credential DSN
  и ничего не выполняет. Реальный dump — только с явным `--apply`.
- Destructive restore-команды в инструменте нет (запрет волны). Восстановление
  ниже — ручная операторская процедура для согласованного окна.
- Retention и ежедневное расписание — параметры и примеры оператора; инструмент
  не ставит cron/Task Scheduler сам.
- Инструмент не копирует `.env` и не отправляет архивы третьим сторонам.

## Быстрый старт (безопасно везде)

```bash
python scripts/backup_goaletto.py --target local-goaletto
python scripts/backup_goaletto.py --target local-goaletto --apply \
  --host 127.0.0.1 --port 5433 --output-dir work/backups
```

Полный список опций: `--help`. Переопределяемый `--timestamp YYYYMMDDTHHMMSSZ`
делает имена воспроизводимыми; без него используется текущее UTC-время.

Перед dump инструмент выполняет read-only psql-проверку current_database() и
наличия таблиц Goaletto. Метка target не заменяет проверку подключения.
`--psql` позволяет явно выбрать executable. Версия сервера попадает в manifest.

## Пароль

Пароль никогда не передаётся в argv, логах и манифесте:

- `PGPASSWORD='<из host-only секрета>'` в окружении, либо
- `--password-file <host-only файл>` (один пароль UTF-8; завершающий перевод строки не входит в пароль).

Без пароля (peer/trust) переменную можно не задавать; stdin закрыт, а pg_dump/psql запускаются с --no-password,
поэтому вместо зависания на запросе пароля команда быстро завершится ошибкой
с безопасным кодом. Не печатайте токены и DSN с credentials при диагностике.

## Что получается

В `--output-dir` публикуются два файла с правами `0600`:

- `goaletto-<UTC>.dump` — PostgreSQL custom-format архив (`pg_dump -Fc`);
- `goaletto-<UTC>.manifest.json` — манифест: инструмент и его версия, target,
  dbname/host/port/username, UTC-время, версия `pg_dump`, имя архива, SHA256,
  размер. Пароля и DSN в манифесте нет.

Dump пишется во временный `.dump-*.part` в том же каталоге и публикуется через
атомарное создание hard link без перезаписи существующего имени только после успешной записи, хеширования и записи манифеста.
При ошибке или прерывании временные файлы удаляются; файл, похожий на успешный
backup, не остаётся. Частичные файлы всегда имеют суффикс `.part` и не
подбираются процедурой восстановления.

Повтор с тем же timestamp без `--overwrite` безопасно завершается
`OUTPUT_COLLISION`, существующий архив не трогается. После обычного сбоя публикации удаляются только
файлы этой попытки. Manifest публикуется последним; успешность означает проверенную
пару. При жёсткой остановке процесса возможен orphan dump без manifest: он не считается
backup. Atomic rename пары файлов и гарантированная crash durability не обещаются. `--overwrite` больше не разрешает замену: завершённые архивы неизменяемы,
выберите новый timestamp. Это сохраняет предыдущую копию при ошибке или гонке.

Коды ошибок (только классы/коды, stderr внешней команды целиком не печатается):

| Код | Смысл |
|---|---|
| `WRONG_DATABASE` (10) | dbname не ровно `goaletto` |
| `WRONG_TARGET` (11) | нет явной метки проекта |
| `PG_DUMP_NOT_FOUND` (12) | нет исполняемого `pg_dump` |
| `PG_DUMP_FAILED` (13) | `pg_dump` завершился с ошибкой (печатается только returncode) |
| `INTERRUPTED` (14) | прерывание; частичные файлы удалены |
| `OUTPUT_NOT_WRITABLE` (15) | каталог недоступен для записи |
| `OUTPUT_COLLISION` (16) | архив/манифест уже есть или запрошена перезапись |
| `TARGET_PROBE_FAILED` (13) | psql не подтвердил подключение/схему |
| `INVALID_ARCHIVE` (13) | нет custom-format PGDMP header/архив пуст |
| `PERMISSION_DENIED` (17) | права файловой системы/исполняемого файла |

## Проверка контрольной суммы

Сразу после dump и после любой копии сверьте хеш:

```bash
shasum -a 256 work/backups/goaletto-<UTC>.dump
# сравнить с полем sha256 в work/backups/goaletto-<UTC>.manifest.json
```

Расхождение — брак копии: архив не использовать, разбирать причину, повторять
dump/копию. Манифест без совпадающего архива — не backup.

## Off-host копия

Архив и манифест копируются парой во внешнее хранилище оператора, например:

```bash
scp "work/backups/goaletto-<UTC>.dump" "work/backups/goaletto-<UTC>.manifest.json" \
  operator@offhost:/srv/goaletto-backups/
```

После копии повторить проверку хеша на приёмнике. Не включать в передачу
`.env`, credentials, DB volumes и image-архивы релизов. Хранилище и транспорт —
решение оператора; инструмент ничего никуда не отправляет сам.

## Retention и расписание (параметры оператора)

Пример политики, задаваемой оператором вручную (не устанавливается инструментом):

- `KEEP_DAILY=7`, `KEEP_WEEKLY=4`, `BACKUP_DIR=work/backups` (локально) или
  каталог релизов/бекапов сервиса на HDC;
- пример cron-строки для ручной установки оператором (ежедневно, время
  согласовать с окном без матчей):

```text
30 3 * * * cd /srv/goaletto && PGPASSWORD='<host-only>' \
  python3 scripts/backup_goaletto.py --target hdc-goaletto --apply \
  --host <internal-host> --port <internal-port> --output-dir /srv/goaletto-backups \
  >>/var/log/goaletto-backup.log 2>&1
```

На HDC PostgreSQL без публичного порта: запускать dump изнутри Compose-сети
(например, one-off контейнером в проекте `goaletto` или `docker compose exec`),
реальная процедура зафиксирована в [HDC-приёмке](hdc-acceptance-2026-10-20.md).
HDC-backup запускает пользовательский LaunchAgent на Mac через SSH, без Windows
Task Scheduler и открытия PostgreSQL наружу.

## Восстановление (операторский drill)

Запрет пользователя: новых локальных/CI тестовых БД не создавать. Restore
проверяется на единственной development Goaletto DB. Пока нет реальных игроков,
текущий мир disposable: сохранять его перед drill необязательно.

1. **Выбрать проверенный архив.** Сверить его размер и SHA-256 с manifest.
   Сохранение текущего disposable dev-мира — необязательный отдельный шаг.
2. **Совместимость миграций.** Сверить `alembic current` в БД и `alembic history`
   кода: архив должен соответствовать ревизии, на которой работает (или будет
   работать) код. Миграции автоматически не откатывать; несовместимость —
   стоп drill и отдельный план от Codex.
3. **Остановить только Goaletto-процессы.** Останавливаются bot и worker проекта
   `goaletto` (локально: `docker compose stop bot worker` с нужными `-f`;
   на HDC: только сервисы каталога Goaletto). Соседние сервисы, PostgreSQL
   другого проекта и volumes не трогать. Volume `postgres_data` не удалять.
4. **Restore.** Распаковать проверенный архив в базу `goaletto` через
   `pg_restore` custom-формата (флаги `--clean --if-exists` — решением Codex
   в окне; команду выполняет Codex, не этот инструмент).
5. **Миграции и restart.** При необходимости `alembic upgrade head`, затем
   поднять стек (`up -d`), убедиться, что одноразовый `migrate` завершился
   успешно до старта bot/worker.
6. **Проверка после restore.** Read-only `goaletto-diagnostics --format json`,
   сверка таблиц/лиг (ожидаемое число клубов комплекта 5×10), smoke `/start`,
   отсутствие чужих данных. Любое расхождение — откат.
7. **Rollback.** Если мир сохраняли, повторить restore его архива и шаги 5–6.
   Disposable dev-мир также можно свободно пересоздать. Откат приложения отдельно:
   предыдущий `GOALETTO_IMAGE`
   и совместимая конфигурация (см. [deployment-hdc.md](deployment-hdc.md)).

## Приёмка Codex

Unit-покрытие (`tests/unit/test_backup_tooling.py`, всё на fake runner/env/FS):

- fake успешный dump: архив + манифест, SHA256/счётчик размера, UTC-время,
  версии инструмента и PostgreSQL, права `0600`;
- failed dump: нет финальных файлов, временные удалены, полный stderr не
  печатается, только `PG_DUMP_FAILED` + returncode;
- interrupted/partial: частичный файл удалён, код `INTERRUPTED`;
- collision: без `--overwrite` — `OUTPUT_COLLISION`, существующий архив цел;
  с `--overwrite` — отказ, для нового архива нужен новый timestamp;
- wrong database/target: отказ до запуска `pg_dump`, runner не вызывается;
- отсутствие `pg_dump`: `PG_DUMP_NOT_FOUND` без traceback;
- ошибки прав: `PERMISSION_DENIED`/`OUTPUT_NOT_WRITABLE`;
- checksum: подмена архива после backup детектируется сверкой манифеста;
- отсутствие секретов: маркер пароля не встречается в captured
  stdout/stderr/argv/манифесте.

Реальный drill: dump единственной development Goaletto DB, off-host копия,
проверка хеша, restore в эту же БД,
rollback-готовность. При приёмке 2026-10-13 локальный dump и restore drill выполнены на единственной
Goaletto development DB; исходный мир восстановлен. См. [отчёт приёмки](agent-wave3-review.md).
2026-10-20 проверены off-host копия HDC → Mac, реальный restore именно
возвращённого с Mac архива (85 таблиц совпали) и фоновый SSH-запуск LaunchAgent.

## Оставшиеся ограничения

- На HDC dump/list/restore использовали PostgreSQL tools его же контейнера.
  Для общего CLI совместимость внешних `pg_dump`/сервера проверять отдельно.
- LaunchAgent требует включённый Mac, пользовательскую сессию и доступ к HDC.
  После сна/недоступности повторяет попытку; удаление старых копий не настроено.
- Custom dump содержит одну БД, не host-only secrets и не cluster-global LOGIN.
  На новом кластере сначала создать роль владельца Goaletto; отдельный Grafana
  login/минимальные GRANT восстановить через provisioning/access workflow.
- Destructive restore остаётся явной операторской процедурой; флага restore
  у backup CLI нет. Разрешение свободно пересоздавать dev-мир перестаёт действовать
  с появлением реальных игроков/данных.
