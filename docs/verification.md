# Проверка начального каркаса · 2026-10-11

- Ruff check и format: успешно.
- mypy strict: успешно, 18 исходных Python-модулей.
- pytest с изолированным PostgreSQL 17: 23 теста успешно.
- Alembic upgrade head: успешно; alembic check: нет расхождений со схемой.
- Проверены повторное enqueue, rollback незавершённой задачи и SKIP LOCKED.
- Docker Compose HDC merge/config --quiet: успешно.
- Docker image python:3.12-slim: успешно собран; application работает от non-root user.
- SVG/PNG: 6 портретов, recipes, contact sheet и HTML; компоненты проверены как XML.
- Локальные ссылки документации: без отсутствующих файлов.

Проверки относятся к начальному scaffold. Реальные матчи, club claim, сезонный
rollover, экономика и отправка outbox-уведомлений пока не реализованы.
Реальный Telegram-бот без токена и HDC production-деплой не запускались.
CI на GitHub проверяется отдельно после первого push.
