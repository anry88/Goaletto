# HDC: стартовая база и форма до 10 · 2026-10-23

AND-165 выпущен на HDC 2026-10-23 около 19:31 UTC. Runtime source:
`719fbac87cc265ce8afb24b6230c9f5b182d3327`, image `goaletto:719fbac8-hdc`.
Выпуск также включает ранее проверенный AND-164 — турнирные дисквалификации
и автоматическую замену перед матчем. Прежние результаты не пересчитываются.

- Image config: `sha256:b1a56bea529272fef32204e33de712d0aa0200a60787759ca9abf25c35e297e1`.
- Архив: 79 393 280 байт, SHA-256
  `34d870d58899de5df78c1441aa154ee84bde9f415d16f5465b94a37a1f0b97c7`.
- Linux/amd64 runtime собран офлайн из проверенного `goaletto:04ef71a2-hdc`
  и нового Python wheel: requirements.lock не изменён. Обычная сборка ожидала
  metadata Docker Hub; wheel собран штатным pip, проверены import smoke и pip check.
- Сохранены все семь overlays, приватные metrics, PGDATA и assets на D.
  Останавливались/пересоздавались только Goaletto bot/worker. PostgreSQL,
  Grafana и соседние службы не пересоздавались.
- Свежая проверенная off-host копия подтверждена по существующему правилу 20 часов;
  лишний архив и reset не выполнялись.

## Результат миграции

Аддитивно применены 0052–0054; схема — `0054_form_ten`, защита данных остаётся
запечатанной. Важно: HDC Settings.environment исторически `development`;
это **не** разрешение сбрасывать настоящую production-БД. Первая проверка
окружения остановилась до изменения навыков; продолжение проверило точное имя
БД, постоянную защиту, revision, количество людей, исходный hash и остановку
приложения. Метка окружения и секретные настройки не менялись.

Fresh preview: 2 000 доказанных исходных игроков / 100 клубов, без исключений,
locked matches и риска банкротства. Fingerprint:
`70924a76028ce9965b0239b96eb18defc631a22d885536c178a0d05752f99544`.
Медианная текущая база качества 11,4 → 5,8. Зафиксированы 2 000 immutable audit
rows. Остальные 15 игроков не менялись. Повторный preview: eligible=0,
already_applied=2 000. Все игровые навыки совпадают с audit after; равенство
`after − new_base = before − old_base` выполнено для каждого навыка каждого игрока.
Recovery stamina совпадает с новой выносливостью; отрицательных бюджетов нет.

Во время остановки приложения сравнили canonical JSON hashes **всех строк**
12 таблиц до/после. Совпали: persons (2 133), player_development (2 015),
salary_contracts (2 105), ledger_entries (715), ownership_periods (6),
match_results/matches (по 9), match_ticks (270), accepted_commands (32),
world_seed_receipts (2), star_payments (0) и data_protection (1).
История, личности, прогресс, контракты, владение и защитная защёлка сохранены.

## Приёмка

Локально: 1 271 unit/offline passed, 31 разный связанный PostgreSQL-сценарий
прошёл; Ruff/format, mypy 223 модулей, SQL render, upgrade и alembic check чистые.
[CI исходного runtime SHA — success](https://github.com/anry88/Goaletto/actions/runs/40011041412).

На HDC bot polling запущен, worker healthy; jobs и sender имеют свежие committed
пульсы, failed jobs=0, ERROR/Traceback в новых логах bot/worker=0. Runtime читает
`starter-squad-v2` и `match-practice-v3`; PostgreSQL CHECK допускает форму 1..10.
Во время этой приёмки игровые матчи не создавались и сообщения людям не отправлялись.
