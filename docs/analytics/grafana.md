# Операторский Grafana · AND-80/82

Два dashboard в `deploy/grafana/dashboards` используют встроенный PostgreSQL
источник. Нового web API, JavaScript или публичного интерфейса Goaletto нет.
Миграция 0044 добавляет только схему `goaletto_operator` и функцию `report`:
игровые таблицы и worker не меняются, копия событий не хранится.

## Данные и ограничения

- «Вход и участие»: покрытие, start → claim → manual lineup за 24 часа,
  источники, D1/D3/D7/D30, первая/вторая матчевая возможность, последние действия,
  ошибки, подтверждение помощи и восстановление за 30 минут.
- «Доставка и worker»: текущие очереди, последний boot worker, p50/p95 технических
  попыток, доставка сообщений/попытки, добровольные ответы и выбранные причины.
  Текст комментариев, люди, Telegram IDs и payloads не выводятся.
- Время UTC. Диапазон задаёт начало когорты/наблюдения и момент отчёта в его конце;
  максимум 366 дней. Coverage и persisted retention обрезают journey-окно.
  `coverage_not_started` отличается от доступного отчёта с пустой когортой.
  Период до начала сбора имеет `outside_coverage` и пустые rows, а не нулевую конверсию.
  При нулевом N процент не вычисляется. Waiting/unknown не превращаются в отказ.
- Вторая opportunity имеет свой уменьшенный знаменатель. Полный календарь,
  реальные времена и ownership проверяются тем же SQL, что в CLI. Товарищеские
  не становятся лиговыми возможностями для этих метрик.
- Фильтры source/locale/variant/tier/set применяются до агрегации когорты, а не
  к готовым числам. Source — direct/known/unknown; кампании видны в таблице.
  Locale — текущий сохранённый язык менеджера: исторический язык входа не записан.
  Недели сравниваются диапазоном и `cohort_week` последних действий.
- Variant — только реально записанная `experiment_exposed` в окне, без назначения
  контрольной группы задним числом. Это наблюдаемый сегмент, не причинный A/B отчёт;
  запуск/назначение экспериментов остаётся AND-81. При отсутствии exposure сегмент пуст.
- Tier/set — первая полученная после старта команда и её сезонная membership на
  момент claim; до начала сезона используется ближайший будущий сезон. Текущая
  `Club.league_id` не переписывает историю. Этот фильтр исключает не получивших
  клуб и меняет знаменатель; полная воронка требует `tier=set=all`.
- Отзывы/доставка/latency операционные, без journey-фильтров; internal видны
  отдельной колонкой в отзывах. Очереди и worker — **текущий** снимок, независимо
  от выбранного исторического периода. Worker показывает последний boot, не
  здоровье всех реплик: конкретный контейнер проверяет `health_cli`.
- Пустые heartbeat rows не означают healthy; отсутствие sender также может
  означать выключенную отправку. Worker latency включает deferred attempts;
  command_wait_for_tick — игровое ожидание, не технический SLA.

SQL зафиксирован в migration 0044 без импорта изменяемого application-кода.
Изменение семантики CLI требует новой миграции функции и PG-проверки равенства.
Нельзя переписывать уже применённую 0044, подменять данные для красивых графиков
или добавлять выдачу raw таблиц ради нового фильтра.

## PostgreSQL: отдельные права

Применить `alembic upgrade head` на настоящей Goaletto DB. Затем выполнить
`deploy/grafana/access.sql` владельцем этой БД с остановкой при ошибке:

```bash
psql --no-password --dbname=goaletto --set=ON_ERROR_STOP=1 \
  --single-transaction --file=deploy/grafana/access.sql
```

Host/port/user/password задаются host-only окружением/pgpass. На HDC выполнять
внутри приватной Goaletto сети, не открывать PostgreSQL порт ради Grafana.
Скрипт проверяет имя БД/наличие интерфейса и создаёт NOLOGIN-группу
`goaletto_operator_reader`, даёт только USAGE схемы и EXECUTE одной функции.
Существующая группа с небезопасными атрибутами, родительской ролью, доступом к
public tables/views или CREATE public отклоняется, а не молча исправляется.

Для datasource создать отдельный LOGIN `goaletto_grafana`: NOSUPERUSER,
NOCREATEDB, NOCREATEROLE, NOREPLICATION, NOBYPASSRLS, INHERIT, CONNECTION LIMIT 2;
выдать только `goaletto_operator_reader`. Пароль назначить через `psql \password`
или host-only secret workflow, без литерала в shell/history/Git. Рекомендуемые
параметры этого логина в БД Goaletto:

```sql
GRANT goaletto_operator_reader TO goaletto_grafana;
ALTER ROLE goaletto_grafana IN DATABASE goaletto SET statement_timeout = '10s';
ALTER ROLE goaletto_grafana IN DATABASE goaletto SET default_transaction_read_only = on;
```

Не использовать игровой login для Grafana. READ ONLY — дополнительная защита:
главная граница — отсутствие прав на таблицы/запись даже после отключения этой
настройки. SECURITY DEFINER имеет фиксированный `search_path=pg_catalog,pg_temp`,
все игровые источники явно `public.*`, PUBLIC EXECUTE/USAGE отозваны. TEMP-таблица
пользователя не может подменить источник. Произвольного dynamic SQL в функции нет.

## Приватность в существующем Grafana

Создать **отдельную операторскую организацию** существующего Grafana, доступную
только операторам. Не добавлять datasource в общую организацию: в Grafana OSS
права папки сами по себе не ограничивают запросы к datasource из Explore.
Не включать anonymous/публичные dashboard snapshots и не приглашать игроков.

Настройки provisioning находятся в `deploy/grafana/provisioning`. Host-only env:

- `GOALETTO_GRAFANA_ORG_ID` — ID отдельной операторской организации;
- `GOALETTO_GRAFANA_PG_HOST` — приватный Goaletto PostgreSQL host:port;
- `GOALETTO_GRAFANA_PG_USER` / `GOALETTO_GRAFANA_PG_PASSWORD` — отдельный login;
- `GOALETTO_GRAFANA_PG_SSLMODE` — фактическая настройка приватного транспорта;
  локальный Docker smoke использовал disable, внешнее соединение требует
  корректной TLS-конфигурации, обычно verify-full.

Добавить только Goaletto YAML к существующим provisioning directories;
`dashboards` монтировать read-only в `/var/lib/grafana/dashboards/goaletto`.
Не заменять чужие файлы provisioning. UID datasource `goaletto-operator-pg`,
UID dashboard `goaletto-launch`/`goaletto-operations`, folder `goaletto-operator`.
Pool ограничен двумя соединениями, refresh раз в минуту, datasource не default.
Файлы dashboard/provisioning не являются ACL: после загрузки применить
`deploy/grafana/folder-permissions.json` через
`POST /api/folders/goaletto-operator/permissions` в операторской организации
и прочитать permissions обратно. Заголовок `X-Grafana-Org-Id` должен указывать
её ID; авторизация только host-only credential, без вывода/коммита секрета.
Проверить отсутствие Viewer/Editor и лишних участников организации. При добавлении
операторов права выдаются явно. Комментарии доступны отдельным restricted
операторским процессом, не через этот datasource.

## Приёмка 2026-10-18

На единственной настоящей Goaletto DB проверены отдельный настоящий LOGIN,
совпадение SQL с CLI, зрелость/unknown, исключение internal/opt-out, фильтры,
coverage gap и отказ на таблицы/комментарии/изменения. Проверка идёт без
READ ONLY, чтобы запрет записи доказали GRANT, а не режим транзакции.

В отдельном временном локальном Grafana **12.4.2** provisioning загрузил оба
JSON. Все 14 panel queries прошли через Grafana datasource с restricted LOGIN;
анонимный запрос dashboard получил 401, folder permissions оставлены admin-only.
Временные контейнер/логин/секрет удалены, соседние сервисы не менялись.

Ruff/format, mypy (189 модулей) и 1 100 unit-проверок прошли. Общий связанный
PostgreSQL-прогон — 27 сценариев; окончательные шесть новых сценариев повторены
после добавления `outside_coverage`. Offline SQL, реальный downgrade/upgrade
0044 и `alembic check` прошли. Тестовая БД не создавалась: использована одна
настоящая application DB Goaletto, её мир разработки можно свободно пересоздавать.

Это не установка на HDC (там при инвентаризации Grafana 12.1.0). AND-80/82
остаются открыты до реального подключения HDC, проверки организации/участников,
GRANT фактического production LOGIN и сверки панелей на той же выборке. Локальная
приёмка не разрешает внешние опросы, рекламу или admission игроков автоматически.

Источники: [Grafana PostgreSQL datasource](https://grafana.com/docs/grafana/latest/datasources/postgres/configure/),
[provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/),
[PostgreSQL SECURITY DEFINER](https://www.postgresql.org/docs/17/sql-createfunction.html).
