# Проверка аналитики перед привлечением · AND-82

Дополнение 2026-10-18: [Grafana и проверка доступа](grafana.md) добавляют
два provisioned dashboard, aggregate-only SQL-интерфейс 0044 и приёмку
настоящего restricted LOGIN. Все 14 panel queries прошли в локальном Grafana.
Это закрывает подготовку панелей и локальную проверку GRANT; фактическая
операторская организация, datasource и права HDC ещё требуют приёмки.

Локальная часть выполняется прямо на единственной настоящей БД Goaletto с
синтетическими аккаунтами и fake Telegram. Во время разработки её можно свободно
сбрасывать/пересоздавать; сохранение и восстановление тестового мира не обязательны.
Она не разрешает автоматически запускать рекламу или отправлять опросы людям.
AND-82 остаётся открытой до сверки реальных dashboard/access в AND-80/72.

## Что сверять

| Проверка | Ожидание и источник |
|---|---|
| direct / known / unknown, internal / opt-out | Hand-count 1/1 для каждого внешнего источника; внутренние и opt-out исключены. `test_analytics_acceptance_pg.py`, настоящий Dispatcher в `test_launch_measurement.py` |
| Claim / manual lineup, autolineup, retry | Готовность требует ручного сохранения того же владения. Retry не удваивает, rollback не сохраняет успех. Матрица AND-98 и `test_launch_measurement.py` |
| Первый/второй матч, перенос, пауза, новый ownership | Реальные actual start/finish и результат; до finish+24ч waiting. Второй N только среди участников первого. Необъявленные планы не получают выдуманного kickoff. `test_launch_measurement.py` |
| D7 и поздний первый матч | Незрелый D7 — waiting; календарное ожидание не считается уходом. Системные доставки и автосостав не дают retention. `test_analytics_acceptance_pg.py`, unit acceptance matrix |
| Delivery unknown / restart | Network intent не подтверждает доставку. Сохранённый неизвестный исход опроса не повторяется после рестарта. `test_feedback_automation.py`, `test_runtime_observation.py` |
| Ответы / skip | Четыре созданных предложения: ответ 1/4, skip 1/4, opt-out 1/4, waiting 1/4; неподтверждённая доставка 4/4. Причина есть только у ответившего. `test_feedback_automation.py` |
| Cooldown / terminal / команды | Общие ≥7д, manager/campaign UNIQUE, skip/opt-out terminal, комментарий ≤500 и ≤30мин, `/команды` не попадают в текст. `test_feedback_pg.py`, real Dispatcher в `test_feedback_automation.py` |
| Cleanup / export / доступ | Retention хранит ровно на границе, удаляет строго старше, повтор безопасен. Игровые receipts/ownership сохраняются, стартовая когорта не возникает заново. Export только внешних псевдонимов, без Telegram mapping/envelope/комментариев; PUBLIC grants отсутствуют. `test_launch_privacy.py` |
| READ ONLY / перезапуск отчёта | Сервер отклоняет запись, новые сессии читают ту же сводку. `test_feedback_automation.py`, `test_launch_measurement.py` |

Точные ожидаемые числа заданы литералами в тестах; публичный SQL и его
счётчики не используются для вычисления ожидаемого результата. Новая внутренняя
автоматика использует общий opportunity classifier, но operational receipts
вместо analytics mappings. Она не добавляет внутренних людей в публичные отчёты.

## Порядок локального запуска

1. Проверить target Goaletto и остановить мешающие прогону bot/worker.
   Данные разработки можно сбрасывать. Не запускать resets параллельно.
2. Ruff, format, mypy, unit/offline; отрендерить миграции.
3. На этой единственной настоящей БД выполнить последовательно:

   ```bash
   GOALETTO_ALLOW_DEVELOPMENT_RESET=1 .venv/bin/pytest -q \
     tests/integration/test_analytics_acceptance_pg.py \
     tests/integration/test_analytics_reports_pg.py \
     tests/integration/test_launch_measurement.py \
     tests/integration/test_launch_privacy.py \
     tests/integration/test_feedback_pg.py \
     tests/integration/test_feedback_automation.py \
     tests/integration/test_runtime_observation.py \
     tests/integration/test_agent_wave.py
   ```

4. Выполнить `alembic check`; при необходимости пересоздать игровой мир для
   ручного теста. Backup/restore тестовых данных не является условием прогона.
5. SQL downgrade/upgrade проверять только на пустой новой истории. Миграция 0042
   запрещает потерю trigger/scoped-action history; обходить запрет нельзя.

Не создавать отдельные test databases. После появления реальных игроков
разрешение сбрасывать development world перестаёт действовать.

## Операторский runbook

Начало покрытия хранит `analytics_coverage`, первый наблюдаемый вход —
`managers.first_game_start_at`, автоматические предложения — собственное
`feedback.scan.payload.coverage_at`. Все три сохраняются при рестарте.
Internal / opt-out first entry также фиксируется operational, без публичного события.
Не восстанавливать фиктивные cohort/events на прошлые даты.

`analytics_reports_cli` и `campaign_cli` дают preview без `--apply`, при чтении
используют серверный READ ONLY REPEATABLE READ snapshot. В отчёте всегда
проверять coverage/available_from, зрелый N и waiting/unknown. N=0 — `rate=null`.
Общие delivery-отчёты показывают текущие operational outcomes, а не снимок
доставки в прошлом; `sent` означает приём Telegram API, не прочтение.

Journey retention можно применить отдельной операторской командой:

```bash
python -m goaletto.application.analytics_cleanup_cli --at <UTC ISO>
python -m goaletto.application.analytics_cleanup_cli --at <UTC ISO> --apply
```

Первая команда не подключается к БД. Вторая использует сохранённые
`analytics_coverage.retention_days`, удаляет истёкшие events/touches/sessions и
не меняет игру. Без начатого покрытия явно отказывает. Комментарии очищает
существующий ежедневный `feedback.cleanup` с отдельным явным retention (30 дней
для внутреннего теста). Применение на HDC и расписание обслуживания проверяются
при развёртывании, здесь работающий scheduler не запускался.

Если цифры не сходятся, сначала проверить coverage и внутренние исключения,
потом actual times/ownership/result, затем occurred/recorded и delivery attempt.
Если вопрос не появился — проверить feature flags, coverage, cooldown,
24-часовой срок предложения, возврат пользователя и `bot_available`.
Не чинить цифры вставкой событий или сбросом failed jobs.

## Приёмка HDC · 2026-10-20

- В существующем Grafana 12.1.0 подключены оба dashboard, все 14 SQL запросов
  выполнены фактическим ограниченным login. Шесть journey-отчётов сверены с CLI
  на одном UTC-интервале. UI показывает coverage_start `2026-10-20T07:52:25.860710Z`,
  ограничение покрытия и 0 внешних starts, не смешивая внутренний smoke с людьми.
- Пользователь выбрал общую Main Org. (1) и папку Goaletto operator. Агрегаты
  доступны её существующим Admin/Viewer; участников и их роли не меняли.
  Отдельная организация не создавалась. Anonymous API =401.
- Настоящий `goaletto_grafana` получает только aggregate function, без raw tables,
  `journey_export`, mapping, comments или записи. Запреты проверены в READ WRITE,
  не только через режим транзакции. Шесть безопасных атрибутов/connection limit,
  минимальная membership и отсутствие прав на public relations подтверждены.
- Реальная БД хранит retention 180 дней. В четырёх таблицах 0 истёкших кандидатов.
  `cleanup_events` применён дважды с guard под блокировкой: при любом кандидате
  проверка отказала бы до DELETE. Это проверка текущего no-op; точные границы и
  удаление старых synthetic rows доказаны локальной hand-count PG матрицей.
  Ownership, receipts и свежие analytics counts не изменились.
- Проверены девять разрешённых колонок `journey_export`, security_barrier,
  0 PUBLIC ACL entries и 0 внешних строк. Header-only CSV сохранён в закрытом
  HDC `goaletto/operator-exports`, доступ — административный SSH/host-only ACL.
  Grafana этот export не читает. Регулярную ручную maintenance выполнять
  указанным выше CLI; автоматическое расписание journey cleanup не устанавливалось.

[Полная HDC-приёмка](../hdc-acceptance-2026-10-20.md). Аналитический gate AND-82
проверен. Это не включает внешнее привлечение, feedback-рассылки или admission:
allowlist остаётся внутренним, automatic feedback выключен, host reboot/alerts
и эксплуатационная приёмка AND-72 остаются отдельными задачами.

## Локальная приёмка 2026-10-18

1097 unit/offline и 50 различных PostgreSQL-сценариев прошли, включая существующую
hand-count матрицу и новые feedback/privacy/export случаи. Целевые повторы после
исправлений подтвердили отмену уже открытого вопроса и поведение unknown/crash.
Ruff, format, mypy (187 модулей) чистые. Миграции отрендерены; 0042 прошла
online upgrade/downgrade/upgrade и оба history guard на откатываемых данных.
`alembic check` чистый. Исходный внутренний мир восстановлен, fingerprints
72 таблиц совпали. Реальные Telegram, HDC, dashboard/роли не использовались.
