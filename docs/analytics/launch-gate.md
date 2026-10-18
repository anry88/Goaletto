# Проверка аналитики перед привлечением · AND-82

Локальная часть выполняется на единственной Goaletto development DB с
синтетическими аккаунтами, fake Telegram и сохранением исходного внутреннего мира.
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

1. Остановить bot/worker, проверить точный target Goaletto и отсутствие внешних
   людей. Сохранить `pg_dump` и контрольную сумму. Не запускать resets параллельно.
2. Ruff, format, mypy, unit/offline; отрендерить миграции.
3. На этой единственной сохранённой development DB выполнить последовательно:

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

4. Восстановить исходный мир даже при падении проверки, применить миграции,
   сверить fingerprints исходных таблиц и выполнить `alembic check`.
   Команда выше сама не делает backup/restore: сохранение и гарантированное
   восстановление — обязательная обёртка оператора, как в текущей приёмке.
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

## Оставшийся gate HDC

- Сверить Grafana с теми же SQL, UTC-интервалами, фильтрами, n/N и coverage.
  Пока dashboard не подключён, соответствие не подтверждено.
- Проверить от имени фактической аналитической роли: разрешён только выбранный
  export/агрегаты, запрещены Telegram mapping, feedback_comments и запись.
  READ ONLY транзакция игрового DB-пользователя не заменяет проверку GRANT.
- Подтвердить maintenance/retention и restricted доступ к выгрузкам на хосте.
  Локальная проверка PUBLIC ACL не доказывает правильность ролей HDC.
- Зафиксировать результаты перед AND-72 и первым внешним привлечением.
  Внешние опросы по-прежнему не включены; текущий фильтр пропускает только internal.

## Локальная приёмка 2026-10-18

1097 unit/offline и 50 различных PostgreSQL-сценариев прошли, включая существующую
hand-count матрицу и новые feedback/privacy/export случаи. Целевые повторы после
исправлений подтвердили отмену уже открытого вопроса и поведение unknown/crash.
Ruff, format, mypy (187 модулей) чистые. Миграции отрендерены; 0042 прошла
online upgrade/downgrade/upgrade и оба history guard на откатываемых данных.
`alembic check` чистый. Исходный внутренний мир восстановлен, fingerprints
72 таблиц совпали. Реальные Telegram, HDC, dashboard/роли не использовались.
