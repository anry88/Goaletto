# HDC: Prometheus → VictoriaMetrics → Grafana · 2026-10-20

По новому указанию пользователя прямой SQL datasource заменён цепочкой
**bot/worker `/metrics` → существующая VictoriaMetrics → Grafana**.
Это продолжение [первого HDC smoke](hdc-acceptance-2026-10-20.md), а не повторное
создание мира. При установке: `goaletto`, revision `0044_operator_reports`,
0 внешних менеджеров, 0 live fixtures. Миграций и reset базы не требовалось.

## Установленный релиз

Runtime собран из чистого `d9c030313958fd8705f56dd7e2e6b37ae4561f7b`,
Linux/amd64, tag `goaletto:d9c03031-hdc`. Архив 76 125 184 bytes,
SHA-256 `d207bf3a41ba8b4c5e6458802a49f74a0410077c46e5248f5233cf0371be69f1`.
Canonical Docker config digest:
`sha256:2e69705dcf07c126a66158aad7bd5d1baa0f27895e69645f7cefc4a04ae684ca`.
Перед load проверены SHA/размер, после — digest/архитектура/revision label.
Последующая правка представления JSON dashboard применена отдельно provisioning.

К четырём прежним overlay добавлен `deploy/compose.monitoring.yaml`.
Пересозданы только bot/worker, использован прежний read-only пакет портретов.
Оба приложения подключены к `goaletto_default` и `observability_default`;
DNS aliases `goaletto-bot-metrics` / `goaletto-worker-metrics`, порт 9109 только
внутри Docker. Host ports отсутствуют. PostgreSQL остался только в своей сети.
Bot/worker StartedAt `09:10:55Z`; после проверки отказа они не перезапускались.

В `D:/Utilities/victoria-metrics-windows-amd64-v1.123.0/vmagent.yml` добавлены
ровно два Goaletto job, 15s interval / 5s timeout. Прежнее содержимое сохранено,
исходный SHA-256 `0913fc3bde8c2a2786aac4ad9d72846a59c81d5026cf9f161f5bbb20bfdb5b99`.
`/-/reload` вернул 200. VictoriaMetrics v1.123.0 не перезапускалась:
StartedAt остался `2026-10-13T20:28:17.3160088Z`.

Все 14 прежних targets сохранили job/URL и health: 7 up, 7 уже down до изменений.
Оба новых targets — up. Все 18 соседних контейнеров сохранили ID и StartedAt;
исключения — согласованный Grafana и отдельные контейнеры самого Goaletto.

## Grafana

В общей Main Org. 1, папка Goaletto operator, работают:

- [Бот и команды](https://grafana.tg-games.com/d/goaletto-runtime): 22 панели.
- [Доставка и worker](https://grafana.tg-games.com/d/goaletto-operations): 17 панелей.
- [Вход и участие](https://grafana.tg-games.com/d/goaletto-launch): 11 панелей.

Все 50 MetricsQL expression выполнены через настоящий VictoriaMetrics query API:
50 success, 0 errors. Пустые feedback/источники и не наблюдавшиеся события не
подменены успешными нулями. Все три dashboard просмотрены в браузере; таблицы
скрывают служебные labels, даты покрытия показываются в UTC, команды отсортированы
по числу запусков. В HDC service folder сохранён `dashboard-query-results.json`.

Datasource `goaletto-vm` — Prometheus, URL `http://hdc-victoria-metrics:8428`,
POST, 15s. UI Test: **Successfully queried the Prometheus API**.
Старый Goaletto operator PostgreSQL datasource удалён provisioning; его пароль
убран из provisioning-файла. `goaletto_grafana` теперь NOLOGIN и не является
членом `goaletto_operator_reader`. Grafana больше не подключена к
`goaletto_default`. Пересоздан только Grafana, StartedAt `09:11:39Z`.
Прежние участники/роли организации и чужой datasource Victoria сохранены.
Anonymous `/api/datasources` возвращает 401.

## Настоящие команды и сверка агрегатов

Через существующий Telegram Desktop, собственный внутренний аккаунт и единственный
HDC polling выполнены `/help`, `/match`, `/finance`. Ответы получены; счётчики
экспортера и затем VictoriaMetrics показали по 1 start/handled/duration observation
для каждой команды, `sendMessage{outcome="accepted"}=3`. Значения видны в Grafana.
Новый локальный poller не запускался. Callback routing и ошибки/429/cancellation
проверены unit-тестами; реальный callback этим протоколом не заявляется.

На HDC опубликованный cache сверён с SQL на его собственном report timestamp
`2026-10-20T09:22:06.211069Z`: все 30 report/window комбинаций и 159 экспортированных
analytics samples совпали. Шесть отчётов дополнительно совпали с существующими
CLI query functions: entry, sources, retention, last-steps, recovery, opportunities.
Внешние starts=0; внутренние smoke-команды не создали публичную когорту.
Непустые hand-count cohorts и maturity/unknown проверены локальной PG-матрицей.

Окна аналитики — rolling 1/7/30 дней относительно момента SQL-снимка. Grafana time
picker показывает историю снимков и технических counters; произвольный исторический
cohort/as-of и пересечения campaign/locale/variant/tier/set остаются в SQL/CLI.
Нельзя интерпретировать increase(command counter) как retained managers.
История новых технических counters начинается с этого релиза, без backfill.

## Отказ и восстановление БД

Останавливался только `goaletto-postgres-1`, после проверки точного проекта/БД
и отсутствия внешних менеджеров. Первая короткая остановка подтвердила доступность
endpoint и старение progress, но закончилась раньше следующего сбора SQL-снимка.
Полная проверка: `09:15:16Z` → `09:16:33Z`, примерно 77 секунд.

Во время полного отказа оба HTTP `/metrics` вернули 200; exporter worker показал
snapshot_success=0, snapshot_errors_total=1, возраст последнего успеха 91s.
Все 229 DB gauge samples исчезли из exposition; процессные counters остались.
Progress jobs/sender устарел на 77s, probe не выдавал ложные success heartbeat.
VictoriaMetrics сохранила snapshot_success=0 и непрерывный `min_over_time(up[3m])=1`
для обоих процессов. DB панели дополнительно фильтруют success и свежесть <120s.
У query API существующей VictoriaMetrics наблюдалась задержка появления последних
scrape samples; доступность endpoint и историю отказа проверяли отдельно.

Тот же PostgreSQL запущен обратно в finally. Без restart bot/worker восстановились
оба progress timestamp, success=1 и 229 DB samples; новое время снимка подтверждено
и в VictoriaMetrics. PostgreSQL/worker в финале healthy, bot running.
Мир и результаты матчей не пересоздавались. Windows reboot, alert rules/доставка
уведомлений и нагрузочная ёмкость этим smoke не проверялись.

## Проверки кода

CI [39398592311](https://github.com/anry88/Goaletto/actions/runs/39398592311):
1134 unit/offline passed, ruff/format, mypy 194 modules, offline Alembic SQL.
Локально серийно выполнены 15 PostgreSQL-сценариев metrics snapshot/operator SQL/
worker health/runtime observation на единственной application DB Goaletto.
Проверены mature/waiting/internal, aggregation без IDs, отсутствие fake zero,
rollback/failed commit без job counter, HTTP 200/404/405 и stale cache clearing.
Фактические `alembic upgrade head` и `alembic check` прошли, schema head не менялся.

Контракты/настройки/каталог метрик: [prometheus-monitoring.md](prometheus-monitoring.md).
