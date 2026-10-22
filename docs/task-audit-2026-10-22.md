# Аудит задач «В работе» · 2026-10-22

Проверены все семь Goaletto-задач со статусом In Progress в Linear: актуальные
критерии, код опубликованного main `e819f4ae`, протоколы проверок, текущий HDC.
Результат: **две Done, пять Backlog, ноль In Progress**. Статусы повторно прочитаны
из Linear после обновления. Архив прежних описаний сохранён в каждой задаче.
Обновление статусов не означает, что оставшиеся требования были выполнены.

## Завершённый объём

| Задача | Основание для Done | Где учитывается остаток |
|---|---|---|
| [AND-70](https://linear.app/andrei-brazhnikov/issue/AND-70) · backup, health, наблюдаемость | Off-host backup по штатному расписанию, проверенный restore до публичного запуска, durable jobs/sender health, приватные metrics → VictoriaMetrics → Grafana и настоящая проверка DB outage/recovery | Общие reboot/autostart и alert rules/доставка централизованы в AND-69; capacity в AND-71 |
| [AND-72](https://linear.app/andrei-brazhnikov/issue/AND-72) · HDC deployment/smoke | Реальный Telegram путь от claim до результата, image verification/rollback, migrations, изоляция соседей, публичный запуск 20 октября, последующие релизы до 04ef71a2/schema0051 | Общий host drill в AND-69, capacity в AND-71; по решению владельца это ручной инфраструктурный этап |

Это консолидация повторявшихся требований, а не утверждение о полном Windows
reboot, настроенной доставке тревог или измеренной ёмкости HDC. Они остаются
открыты. Удалены устаревшие blocking links к уже выполненным задачам и к уже
состоявшемуся запуску. Прежние ссылки в исторических текстах оставлены.

Доказательства: [HDC smoke/restore/recovery](hdc-acceptance-2026-10-20.md),
[Prometheus/VM/Grafana](monitoring-acceptance-2026-10-20.md),
[публичный запуск](public-launch-2026-10-20.md),
[последний релиз](club-management-2026-10-22.md),
[панель аудитории](audience-hdc-acceptance-2026-10-22.md).

## Оставшиеся задачи

Вместо старых широких названий в Linear записан конкретный остаток. Статус Backlog
означает ожидающую работу: сейчас эти проверки/генерация не выполняются.

| Задача | Подтверждённый остаток | Что уже сделано |
|---|---|---|
| [AND-44](https://linear.app/andrei-brazhnikov/issue/AND-44) · завершить возрастные/кадровые портреты | 54 листа: 21 футбольный, 33 кадровых; импорт, техническая и визуальная проверка, установка | 308/362 листа, 6160/7240 дополнительных вариантов. Последний portrait-аудит HDC: все 101 действующий футболист человеческих клубов имеют текущую возрастную карточку. Это не покрытие будущих возрастов |
| [AND-48](https://linear.app/andrei-brazhnikov/issue/AND-48) · визуальная приёмка Telegram | Полная ручная RU/EN матрица экранов, фото↔текст/возвратов, длинных имён, stale/lifecycle/retired/staff состояний | Функциональные меню, карточки, guards, пагинация, history context, cache и аналитика; PG/Dispatcher приёмка. Настоящий Telegram smoke покрывал основной путь, не всю матрицу |
| [AND-66](https://linear.app/andrei-brazhnikov/issue/AND-66) · лучший игрок матча | Согласовать формулу и tie-break, реализовать сохранение и исторический RU/EN вывод | История/статистика/архивы всех соревнований, bounded queries, frozen snapshots и возврат на исходную страницу готовы |
| [AND-69](https://linear.app/andrei-brazhnikov/issue/AND-69) · ручная приёмка HDC | Windows reboot/Docker autostart в окне владельца; host degraded-mode/recovery; настройка и реальная доставка/восстановление тревог | Reconciliation, локальные crash/rollback/concurrency; настоящий HDC DB disconnect и worker pause/recovery; replay/деньги без дублей |
| [AND-71](https://linear.app/andrei-brazhnikov/issue/AND-71) · баланс и ёмкость HDC | Несколько seeds и сравнимые стратегии; scout durations/интенсивности при одинаковом штабе; реальные HDC p95/ресурсы и эксплуатационные лимиты под наблюдением владельца | PG races; local real-clock 50/75 матчей с регистрациями/ставками/callbacks; десять сезонов, 6055 матчей и 24757 сервисных заданий |

Код `application/history.py` и `bot/history_cards.py` прямо запрещает вывод
неутверждённого MVP/derived rating. Поэтому AND-66 не закрыта как готовая история
с формально потерянным остатком. Коэффициенты баланса по одному seed не признаны
окончательными; ускоренный finalizer не доказывает live capacity.

В AND-44 исправлено устаревшее правило: по указанию пользователя базовые лица
разрешено повторно использовать для разных людей. Назначенное лицо каждого
человека и его история остаются постоянными. Новые identity для нового комплекта
не нужны; остаток — варианты прежних лиц. Старый Imagegen limit от 13 октября
не выдаётся за текущий подтверждённый блокер.

Источники: [портреты и HDC покрытие](small-ui-and-portraits-2026-10-22.md),
[checkpoint](../assets/portraits/whole-v2/lifecycle-v1/generation-checkpoint.json),
[staff/development acceptance](staff-development-acceptance.md),
[PG races](postgres-race-acceptance.md), [нагрузочные профили](launch-load.md),
[десять сезонов и ограничения](reports/ten-seasons-2026-10-20.md).

## Проверки во время аудита

- Повторены 39 unit-проверок навигации, клубных/staff карточек, истории/context,
  пагинации и portrait file cache: 39 passed.
- На HDC только чтение: Docker running; bot и worker `goaletto:04ef71a2-hdc`,
  PostgreSQL/worker healthy. Health CLI: database ok, jobs success age 1,78s,
  sender 4,40s, оба компонента ok. Это текущий снимок, не новый fault drill.
- Не выполнялись restart, отказ БД, миграции, reset, игровые команды, генерация
  изображений или отправки Telegram. Production защищён; тестовые данные в него
  не добавлялись. Расписание backup и настройки Grafana не менялись.
- Изменения репозитория ограничены этим отчётом и актуализацией roadmap.
  Повтор полного pytest/mypy/миграций для документации не требуется; прежние
  результаты перечислены в соответствующих протоколах, не выданы за новый прогон.
