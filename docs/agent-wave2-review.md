# Вторая волна агентов · проверка и интеграция 2026-10-13

Приняты ветки от main ba16344a:
- notification-settings 0aa85faa (AND-50);
- history-pagination 8cc988f0 (AND-66 follow-up);
- analytics-reports 6c6f206d (AND-79 / часть AND-77);
- runtime-diagnostics 57636085 (части AND-69/70).

Общий конфликт pyproject.toml разрешён сохранением обеих CLI. Settings router,
команда помощи и кнопка меню подключены к game dispatcher; Bot IO после commit.
/settings до /start отвечает понятным текстом. Mutation перепроверяет expiry
после ожидания locks, conflicting desired values одной operation отклоняются.
Настройки не меняют availability/last_game_action_at/expires_at.

PostgreSQL обнаружил реальную несовместимость: новый notification_preferences
receipt запрещён CHECK0008. Общая frozen migration0011_agent_wave2 расширяет
allowlist без нового journey event, добавляет indexes истории и GIN path_ops.
Предложенный индексный patch имел неверное имя parent0010 — исправлено.
Downgrade не удаляет/переписывает settings receipts молча.

History: исправлена страница2 пустой истории; missing receipt/invalid checkpoint
не пропускаются молча. Добавлена bounded season pagination без разрыва legacy
API и callbacks;12 сезонов дают3 страницы. Player/club pages bounded; snapshots
и halftime stage events сохраняются. MVP остаётся без утверждённой формулы.

Diagnostics: исправлен запрос ticks (использовались fixture UUID вместо match UUID),
scope пустых матчей и ограничение seasons. Read-only CLI и analytics CLI получают
один server READ ONLY REPEATABLE READ snapshot: нет false alarm из-за worker
между SELECT, server отклоняет запись.

Analytics: исправлены PG fixtures с system session_id и запрещённым UPDATE
append-only event. Journey SQL as-of/retention/n/N проверен на PostgreSQL. Delivery
явно объявляет current-state semantics; восстановление прошлых outcomes по --at
недоступно. Mature fixture opportunities/recovery/полные latency metrics ещё AND-77/79.

Проверки: ruff/format, mypy82;372 unit/offline passed;16 real PostgreSQL integration
passed. Проверены settings через настоящий Dispatcher, сохранение ownership
deadline, seed/claims/lineup/match,12-season archive, hand-counted n/N, read-only
snapshot и отсутствие false tick gap на full match. Telegram fake; polling/HDC
не запускались. Новых DB нет; используется одна Goal app DB.

После общей приёмки — main, затем portrait quota check. Если Imagegen ещё
ограничен, следующая необходимая задача AND-53: live admission/UI.

GitHub CI исходного9734c4fe обнаружил ModuleNotFoundError tests.unit при запуске
pytest executable (локально python -m pytest проходил). Итоговая сборка добавляет
явные tests/unit/integration packages и проверяет также прямой pytest CLI.
