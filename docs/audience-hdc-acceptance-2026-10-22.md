# HDC · аудитория и состав · 2026-10-22

Запрос владельца: понятные регистрации/активные/источники в Grafana по примеру
Purrrfolio, установка на HDC и несколько небольших улучшений игры.

## Установлено

- AND-161: [Goaletto · игроки и источники](https://grafana.tg-games.com/d/goaletto-audience)
  в прежней общей организации 1 / Goaletto operator, 12 native panels.
  Всего/новые/активные 24 часа, 7 и 30 дней, получившие клуб за всё время,
  три разреза по источникам. Период источников выбирается отдельно.
  Числа читает только VictoriaMetrics. Новые source names публикует worker;
  коды не уходят в Prometheus labels. [Семантика](analytics/audience-dashboard.md).
- AND-156: описание всех семи схем по реальным линиям и позициям, наличие DM,
  расшифровки и последствия дополнительной/незнакомой позиции; RU/EN.
- AND-158: `/squad` читает сохранённую схему и реальные LineupSlot.position;
  отдельно показывает позицию самого футболиста, скамейку и вне заявки.

## Релиз

Исходный Git SHA: `39a022c806f16f8637f5559b1cbf20c7af5009b7`.
Image: `goaletto:39a022c8-hdc`, linux/amd64, runtime-only.
Config digest: `sha256:ef67ce87ad635f9c29780360cd0149069d5cc809b1e21f523b132fedd865c145`.
Архив: 75 175 424 bytes, SHA-256
`e04532a955836ce05a798de8e377913717bf4aec2753b929a480ff4d9ce7b778`.
Контрольная сумма/платформа/образ проверены до старта.

Перед обновлением штатная backup-проверка подтвердила recent_verified_backup;
расписание и политика хранения не менялись. Обновлены только bot/worker;
PostgreSQL и Grafana сохранили прежний uptime. Миграции не требуются, revision 0050.
Прежний portrait mount и все шесть overlays сохранены; седьмой audience overlay
даёт worker Modify только в новом подкаталоге Grafana Goaletto dashboards.
Secret/datasource/соседние dashboards не монтируются. PGDATA не менялся.

## Приёмка

- Ruff, mypy (216 source files); 1188 non-integration pytest passed.
- Один integration pytest на временных таблицах единственной локальной application
  DB, с rollback: ручная ожидаемая матрица регистраций/активности, исключения,
  повторные события и вернувшийся старый пользователь. Создания/сброса БД нет.
- Все 7 схем, RU/EN Telegram length, 23 футболиста / 5 страниц, сохранённые места,
  переходы карточек без потерь и дублирования. Последняя правка высоты Grafana header
  дополнительно прошла соответствующие unit tests.
- HDC independent read-only SQL parity: всего 6 регистраций; новые 2/6/6 и
  активные 2/6/6 за 1/7/30 суток. Получили клуб за всё время 4.
- Источники всего: напрямую 2, riverking 1, ai_match_prediction 3;
  unknown/other 0. Это моментальный снимок, не зафиксированная бизнес-метрика.
- HTTP metrics 200; все 23 MetricsQL targets вернули непустые успешные результаты;
  snapshot success 1; bounded labels проверены. Jobs/sender и worker healthy.
- Реальный экран Grafana: переключены 24 часа, 7 и 30 дней; числа и названия
  совпали. Date coverage видна без прокрутки текста: 20.10.2026 15:05 UTC;
  длинные окна пока неполные. Screenshot: work/audience-grafana-2026-10-22.png.

Проверки не отправляли Telegram-сообщения и не изменяли менеджеров/составы на HDC.
Фактическое отображение бота проверено через те же renderers и DB query contracts;
живые Telegram-нажатия от чужого имени не выполнялись. Production не сбрасывался.
