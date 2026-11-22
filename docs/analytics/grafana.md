# Grafana Goaletto

С 2026-10-20 по уточнению пользователя monitoring использует цепочку
**bot/worker `/metrics` → существующая VictoriaMetrics → Grafana**.
Прямой PostgreSQL datasource заменён на Prometheus `goaletto-vm`.

Три provisioned dashboard в общей Main Org. (1), папка Goaletto operator:

- **Бот и команды**: реальные запуски, исходы, p95, Telegram API/429,
  транзакции/SQL/pool, event loop, CPU/RSS/uptime.
- **Доставка и worker**: очереди/due, heartbeat прогресса, jobs/lateness,
  outbox outcomes, матчи/клубы и свежесть SQL-снимка.
- **Вход и участие**: SQL-снимки окон 1/7/30 дней, n/N, coverage,
  retention/opportunities/recovery/delivery/feedback. Технические command counters
  не заменяют человеческую активность и retention.

Пользователь явно выбрал существующую общую организацию, а не отдельную приватную.
Её участники могут читать агрегаты; folder ACL не изолирует datasource от Explore.
Anonymous/публичные snapshots и приглашение игроков автоматически не включаются.
Идентификаторы и текстовые payloads/comments не попадают в метрики.

## Сохранённый SQL-контракт

Migration 0044 `goaletto_operator.report` остаётся замороженным источником
корректных агрегатов для worker snapshot и операторского SQL/CLI. Изменение его
семантики требует новой миграции и parity checks; migration не переписывать.
`deploy/grafana/access.sql` и admin-only folder ACL остаются дополнительными
операторскими инструментами, но Grafana больше не получает login к PostgreSQL.
Запреты raw tables/comments/write, точные n/N, mature/unknown, internal/opt-out,
исторический tier/set и search_path проверены существующей PG-матрицей.
Репрезентативные прежние SQL queries сохранены в tests/fixtures/operator_queries.json,
а dashboard JSON теперь содержит только MetricsQL.

## Простые числа игроков и источников ·2026-10-22

Новый [dashboard «Игроки и источники»](audience-dashboard.md) дополняет три
прежних: total/new registrations, активные24ч/7д/30д и реальные названия
источников. Его числа также идут только через VictoriaMetrics; имена источников
автоматически обновляются worker в отдельном native provisioning-файле.
