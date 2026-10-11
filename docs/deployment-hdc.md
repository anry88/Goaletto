# Размещение на Home Data Center

В документации существующего HDC подтверждена схема Windows host + Docker и Cloudflare
Tunnel. Goaletto использует Linux-контейнеры в этом Docker environment.
Доступный SSH alias — hdc. Конфигурацию и реальные мощности сервера перед деплоем проверить;
в рамках начального каркаса соединение с сервером и production-деплой не выполнялись.

Предлагаемый путь: D:\HomeDataCenter\services\goaletto.
Самостоятельный Compose project goaletto не перезапускает соседние сервисы.
Порты bot/worker не публикуются. PostgreSQL в HDC override тоже без опубликованного порта.
Cloudflare Tunnel не требуется: бот получает updates исходящим long polling.

## До запуска

- Зарегистрировать Goaletto в BotFather и подтвердить доступность username.
- На HDC хранить .env с токеном и отдельным случайным паролем БД.
- GOALETTO_DATABASE_URL должен использовать hostname postgres, те же user/db/password,
  что и Compose PostgreSQL. Никогда не оставлять пример goaletto_dev_only в production.
- Проверить Docker Linux mode, запуск после reboot, отключённый sleep, синхронизацию UTC,
  свободный диск и ресурсы рядом с существующими ботами.
- Для merge override проверить поддержку !reset через docker compose version и config --quiet.
- Один процесс polling на токен. Если бот ранее использовал webhook, сначала отдельно
  согласованно снять старый webhook и остановить старый процесс, не сбрасывая updates.

## Команды в PowerShell из каталога проекта

```powershell
docker compose -f compose.yaml -f deploy/compose.hdc.yaml config --quiet
docker compose -f compose.yaml -f deploy/compose.hdc.yaml up -d --build
docker compose -f compose.yaml -f deploy/compose.hdc.yaml ps
docker compose -f compose.yaml -f deploy/compose.hdc.yaml logs --tail 100 bot worker
```

migrate — одноразовый сервис; bot/worker стартуют только после успешного upgrade head.
Не использовать docker compose config без --quiet в логах: он раскрывает значения .env.

## Проверки и откат

Проверить /start, /help и ответы preview-кнопок, статус worker, диагностическую задачу,
миграции и отсутствие публичного PostgreSQL-порта.
Gameplay пока отсутствует; эти проверки не означают готовность игры к запуску.

Перед изменением схемы снять backup. Откат приложения — к предыдущему image/commit;
миграцию автоматически не откатывать без проверки совместимости и отдельного плана.
Не удалять volume при обычном обновлении. PostgreSQL volume — не резервная копия.

Backup: ежедневный pg_dump, копия вне HDC, регулярно проверяемое восстановление.
Для запуска с людьми проверить сценарий аварии во время матча/rollover/аукциона.
Метрики: tick lateness, pending jobs, outbox age, failed jobs, connection pool, диск,
количество свободных клубов лиги 5, время последнего backup.

[Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/).
