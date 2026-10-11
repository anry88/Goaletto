# ADR 0001: Python и Telegram-бот

Статус: принято пользователем 2026-10-11.

Python 3.12+, aiogram 3, PostgreSQL, SQLAlchemy asyncio + asyncpg, Alembic.
Процессы bot и worker, long polling, Docker Compose на существующем HDC.
Public HTTP API, frontend, Mini App и JavaScript отсутствуют.
На начальном этапе долговечная очередь в PostgreSQL, без отдельного Redis.
Лицензия: proprietary/all rights reserved.

Основание: бот — единственный интерфейс, симуляция дискретная с интервалом 60 секунд;
общая frontend/backend типизация TypeScript здесь не даёт пользы.
Границы домена независимы от Telegram и PostgreSQL.
