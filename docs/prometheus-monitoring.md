# Prometheus → VictoriaMetrics → Grafana

Уточнение пользователя 2026-10-20 заменяет прямое SQL-подключение Grafana.
Bot и worker сами отдают `GET /metrics` в стандартном Prometheus text format;
существующая VictoriaMetrics опрашивает их каждые 15 секунд. Три dashboard
используют только `goaletto-vm` (Prometheus datasource), без PostgreSQL credentials.
Это приватный endpoint мониторинга, без игровых HTTP routes и публичного frontend.

## Что измеряется

| Семейство | Смысл |
|---|---|
| `goaletto_updates_started_total`, `updates_total`, `update_duration_seconds`, `updates_inflight` | Реальные попытки команд, кнопок и сообщений, исход handler, длительность и параллелизм |
| `goaletto_telegram_requests_total`, `telegram_request_duration_seconds`, `telegram_retry_after_seconds` | Прямые Bot API вызовы обоих процессов: accepted, 429, forbidden, bad_request, network/timeout unknown |
| `goaletto_database_transactions_total`, `database_transaction_duration_seconds` | Наблюдаемые окончания transaction/commit и ошибки, отдельно read/write |
| `goaletto_database_statements_total`, `database_statement_duration_seconds`, `database_pool_connections` | SQL по ограниченному типу операции, ошибки, задержка и application pool; без текста и параметров SQL |
| `goaletto_worker_iterations_total`, `worker_iteration_duration_seconds`, `worker_last_success_timestamp_seconds`, `worker_last_iteration_ok` | Завершённые циклы jobs/sender, ошибки и прогресс; intentional sender backoff остаётся наблюдаемым |
| `goaletto_jobs_total`, `job_lateness_seconds`, `outbox_deliveries_total` | Попытки jobs и исходы outbox после commit receipt; исторический world replay исключён из match lateness |
| `goaletto_heartbeat_write_errors_total` | Ошибки отдельной bounded durable health-записи |
| `goaletto_event_loop_lag_seconds`, `event_loop_lag_observation_seconds`, `asyncio_tasks` | Планирование asyncio; этот probe не продлевает heartbeat игрового worker |
| `process_*`, `python_*` | CPU, RSS, file descriptors, uptime, GC и Python runtime |
| `goaletto_db_snapshot_*` | Успех/ошибки/время последнего обновления и стоимость фонового SQL-снимка |
| `goaletto_queue_items`, `goaletto_fixtures`, `goaletto_clubs` | Текущее состояние очередей и мира из БД; due/oldest считаются только по pending |
| `goaletto_analytics_*` | Фиксированные SQL-снимки воронки/источников/retention/opportunities/recovery/delivery/feedback с n/N и waiting/unknown |

Технические counters включают internal и retries. `handled` означает возврат
handler, а не успешный claim/покупку. `accepted` Telegram означает ответ API,
не прочтение сообщения. Network/timeout не превращаются в доставленный исход.
`getUpdates` содержит штатный long polling; из latency отправок он исключён.
SQL statement completed не доказывает commit: transaction outcomes показываются
отдельно. Job counters увеличиваются только после выхода из transaction caller.
Rollback не увеличивает committed, метрики не управляют повторением игровых команд.

Counters сбрасываются при restart процесса: использовать `rate` / `increase`.
Histogram p95 вычисляется из buckets, не усреднением сохранённых p95. При отсутствии
наблюдений нет выдуманной успешной нулевой задержки. Счётчики технических попыток
не являются durable историей и не подменяют PostgreSQL receipts.

## Снимки аналитики и ограничения

Worker фоном раз в 60 секунд читает READ ONLY REPEATABLE READ snapshot с
statement_timeout 2s и общим timeout 10s. HTTP scrape только сериализует готовый
cache и не запрашивает PostgreSQL. Отказ сбора очищает старые DB families,
выставляет success=0 и сохраняет последний успешный timestamp. Панели дополнительно
проверяют свежесть <120s; зависший сбор не выглядит здоровой нулевой очередью.

Окна `1d/7d/30d` задают когорту стартов относительно времени SQL-снимка. Существующая
семантика migration 0044 сохраняется: internal/opt-out исключены из journey,
waiting/unknown отдельно, уменьшенный N второй возможности, coverage gaps явно.
Отзывы различают internal. Пустые/неизвестные значения не заменяются нулём.

Time picker Grafana показывает историю опубликованных снимков и технических
попыток. Он не задаёт произвольную историческую когорту как прежний SQL dashboard.
Для точного as-of и произвольных campaign/locale/variant/tier/set пересечений
остаются restricted SQL/CLI из [launch gate](analytics/launch-gate.md).
Идентификаторы кампаний, людей, клубов, матчей и наборов не добавляются в labels.
Snapshot gauges не суммировать по времени и не применять к ним rate/increase.
Подробные campaign/source rows свернуты в ограниченные категории с суммированием
чисел; новые неизвестные категории попадают в `other`, текст не экспортируется.

## Конфигурация

По умолчанию listener выключен. Для локального запуска:

```dotenv
GOALETTO_METRICS_ENABLED=true
GOALETTO_METRICS_HOST=127.0.0.1
GOALETTO_METRICS_PORT=9109
GOALETTO_METRICS_SNAPSHOT_SECONDS=60
```

Bot и worker — разные контейнеры, оба слушают внутренний 9109.
При запуске обоих процессов непосредственно на Mac задавать разные порты.
`deploy/compose.monitoring.yaml` подключает только bot/worker к существующей
`observability_default` (имя задаёт GOALETTO_MONITORING_NETWORK), включает listener
0.0.0.0 внутри контейнера и задаёт DNS aliases `goaletto-bot-metrics` /
`goaletto-worker-metrics`. Host ports не публикуются. PostgreSQL остаётся только
в Goaletto default network; VictoriaMetrics и Grafana не требуют этой сети.

На HDC к четырём release overlay добавляется пятый:

```powershell
$a=@('-f','compose.yaml','-f','deploy/compose.hdc.yaml',
     '-f','deploy/compose.release.yaml','-f','deploy/compose.assets.yaml',
     '-f','deploy/compose.monitoring.yaml')
docker compose @a config --quiet
docker compose @a up -d --no-deps bot worker
```

`deploy/victoriametrics/goaletto-scrape.yaml` — фрагмент двух scrape jobs.
Добавлять их в существующий vmagent.yml, сохраняя остальные jobs. Reload через
`/-/reload`, без перезапуска VictoriaMetrics. До/после сверять существующие targets.
Grafana provisioning требует GOALETTO_GRAFANA_ORG_ID (HDC=1) и
GOALETTO_VICTORIAMETRICS_URL (приватный `http://hdc-victoria-metrics:8428`).
Удаляется только прежний datasource Goaletto operator; чужие источники не менять.
Убрать старый SQL secret из Goaletto provisioning, закрыть login goaletto_grafana
и удалить ненужное private Goaletto network attachment Grafana. Общая папка
Goaletto operator остаётся по явному решению пользователя.

## Приёмка

Нужны настоящий /metrics обоих процессов, оба up=1 в VictoriaMetrics, живой
/start/help/match или кнопка через единственный HDC polling и рост соответствующих
counters, все MetricsQL запросы трёх dashboard через VictoriaMetrics и просмотр
в Grafana. Ошибка БД должна оставлять процессный endpoint доступным, показывать
snapshot failure/staleness и исчезновение DB gauges, затем восстанавливаться.
Не выдумывать исторические counters до установки и не reset development-мир
ради красивых графиков. Реальные HDC результаты фиксируются отдельно.

Документация библиотек: [Prometheus instrumentation](https://prometheus.io/docs/practices/instrumentation/),
[Python client](https://prometheus.github.io/client_python/),
[VictoriaMetrics scraping/reload](https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/).
