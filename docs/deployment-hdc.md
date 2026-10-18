# Размещение на Home Data Center

Решение пользователя от 2026-10-11: деплой управляется локально/вручную.
Основной путь — сборка и проверка image на рабочем компьютере, перенос архива на HDC.
Альтернатива — commit/push здесь, pull и сборка на HDC. GitHub Actions выполняет
проверки; доступ к HDC и автоматический деплой через CI не требуются.

HDC: Windows host + Docker с Linux-контейнерами, SSH alias hdc.
Предлагаемый путь: D:\HomeDataCenter\services\goaletto.
Отдельный Compose project goaletto, volumes и host-only secrets. PostgreSQL без
публичного порта; bot/worker используют исходящий long polling, без Cloudflare Tunnel.
Production-деплой Goaletto ещё не выполнялся.

Обновление 2026-10-18: read-only SSH/inventory проверены, HDC отвечает;
Docker Linux/x86_64, 8 CPU, около 7,71 GiB RAM, Compose 5.1.4. Goaletto ещё не
развёрнут, соседние сервисы не менялись. В release добавлен
[worker healthcheck](worker-health.md); локальная [нагрузочная проверка](launch-load.md)
не заменяет измерение на HDC и проверку бюджета памяти существующих сервисов.

## Подготовка

- GoalettoBot зарегистрирован, профиль и иконка применены. Перед запуском проверить
  токен без печати, один polling instance и отсутствие конфликтующего webhook.
- На HDC собственный .env с токеном и случайным паролем БД, DATABASE_URL с hostname
  postgres и согласованными user/db/password. Не переносить локальный .env в release.
- Проверить Linux mode, архитектуру, Docker Compose с поддержкой !reset, диск,
  ресурсы, reboot/sleep и синхронизацию UTC. Соседние сервисы не перезапускать.
- PostgreSQL image заранее загрузить на HDC; его отдельная первоначальная загрузка
  не является публикацией application image.
- Перед обновлением: backup и штатная остановка только Goaletto bot/worker.
  Не менять схему одновременно с работающим старым worker.

## Основной путь: собрать здесь и перенести image

Сборка только из проверенного чистого коммита. Для x86-64 HDC — linux/amd64;
сначала подтвердить архитектуру сервера, для ARM — linux/arm64.
Не использовать нативную архитектуру Mac вместо платформы HDC.

На рабочем компьютере, zsh:

```bash
release=$(git rev-parse --short=12 HEAD)
mkdir -p work/releases
docker build --platform linux/amd64 --load --tag "goaletto:$release" .
docker image save --output "work/releases/goaletto-$release.tar" "goaletto:$release"
shasum -a 256 "work/releases/goaletto-$release.tar"
docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' "goaletto:$release"
```

После успешной сборки выполнить локальный smoke image; записать полный Git SHA,
tag, image ID, платформу и SHA-256 архива. Tag не переиспользовать для другой сборки.
Передать tar и metadata через SFTP/SCP alias hdc в releases/ каталога сервиса.
Compose-файлы из того же коммита передать отдельно или получить через репозиторий.
Не включать .env, credentials, DB volumes и backup.

На HDC сверить SHA-256 через Get-FileHash -Algorithm SHA256.
PowerShell из каталога сервиса:

```powershell
$release = "REPLACE_WITH_RELEASE_SHA"
docker image load --input ".\releases\goaletto-$release.tar"
if ($LASTEXITCODE -ne 0) { throw "Image load failed" }
$env:GOALETTO_IMAGE = "goaletto:$release"
docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' $env:GOALETTO_IMAGE
if ($LASTEXITCODE -ne 0) { throw "Release image unavailable" }
```

Сверить image ID/платформу с metadata. Сохранить GOALETTO_IMAGE в host-only .env
для повторного запуска; пример выше задаёт его только для текущей сессии.

## Альтернатива: pull и сборка на HDC

Здесь выполнить проверки, commit/push в приватный git@github.com:anry88/Goaletto.git.
На HDC отдельный checkout с read-only доступом к GitHub, credentials вне Git/URL.

```powershell
git pull --ff-only
if ($LASTEXITCODE -ne 0) { throw "Repository update failed" }
$release = git rev-parse --short=12 HEAD
if ($LASTEXITCODE -ne 0) { throw "Cannot identify release" }
```

До сборки сверить полный HEAD с выбранным проверенным коммитом, затем:

```powershell
docker build --tag "goaletto:$release" .
if ($LASTEXITCODE -ne 0) { throw "Release build failed" }
$env:GOALETTO_IMAGE = "goaletto:$release"
```

Записать image ID, выполнить smoke и сохранить tag в host-only .env.
Если SHA уже собирался, использовать существующий проверенный image либо новый
build suffix; не перезаписывать tag. Сборка требует CPU/диск HDC и доступа к зависимостям.

## Общий запуск выбранного локального image

Использовать release override в обоих вариантах: он удаляет application build
и запрещает registry pull для migrate/bot/worker. Все три используют один image.

```powershell
$composeArgs = @("-f", "compose.yaml", "-f", "deploy/compose.hdc.yaml",
                 "-f", "deploy/compose.release.yaml")
docker compose @composeArgs config --quiet
if ($LASTEXITCODE -ne 0) { throw "Invalid release configuration" }
docker compose @composeArgs up -d --no-build --pull never
if ($LASTEXITCODE -ne 0) { throw "Release startup failed" }
docker compose @composeArgs ps
```

migrate — одноразовый сервис; bot/worker стартуют после успешного upgrade head.
Новый tag пересоздаёт migrate; при повторе того же tag проверить его предыдущий
успешный exit status. Каждую native-команду PowerShell проверять по LASTEXITCODE.
Не печатать docker compose config без --quiet: он раскрывает .env.
Команды на HDC ещё не выполнялись; release override проверяется локально на dummy env.

## Проверки и откат

Сейчас smoke: /start, /help, preview-кнопки, worker, миграции, отсутствие публичного
PostgreSQL-порта. Это не означает готовность gameplay. Для MVP пройти AND-72 и AND-82.

Сохранять предыдущий image/metadata/Compose-файлы. Откат приложения — выбрать
предыдущий GOALETTO_IMAGE и совместимую конфигурацию. Миграцию автоматически не
откатывать без проверки совместимости и отдельного плана. Volume не удалять.
Ежедневный pg_dump с копией вне HDC и проверяемым restore; volume не заменяет backup.
Проверить аварии матча/rollover/аукциона до допуска людей.
Метрики: tick lateness, pending jobs, outbox age, failed jobs, pool, диск,
свободные клубы лиги 5 и время последнего backup.

Основания: [Docker save](https://docs.docker.com/reference/cli/docker/image/save/),
[Compose merge/reset](https://docs.docker.com/reference/compose-file/merge/),
[Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/).
