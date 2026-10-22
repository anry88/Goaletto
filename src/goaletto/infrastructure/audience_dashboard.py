"""Native Grafana dashboard; values come only from VictoriaMetrics."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text

from goaletto.infrastructure.audience import SourceLabel, mapping_token, source_labels
from goaletto.infrastructure.database import Database

DS = {"type": "prometheus", "uid": "goaletto-vm"}
FRESH = (
    " and on(job,instance) (goaletto_db_snapshot_success == 1)"
    " and on(job,instance) (time()-goaletto_db_snapshot_timestamp_seconds < 120)"
)


def build_dashboard(labels: tuple[SourceLabel, ...], coverage: datetime | None) -> dict[str, Any]:
    panels: list[dict[str, Any]] = []
    description = (
        "Регистрация — первый принятый /start. Активный — уникальный менеджер с явным "
        "действием в игре; автоматические матчи и уведомления не считаются. "
        "Внутренние аккаунты и opt-out исключены. Окна скользящие: 24 часа, 7 и 30 суток."
    )
    coverage_text = (
        "Данные об активности с "
        + coverage.strftime("%d.%m.%Y %H:%M UTC")
        + (
            "; окна длиннее срока наблюдений пока неполные."
            if datetime.now(UTC) - coverage < timedelta(days=30)
            else "."
        )
        if coverage
        else "Покрытие ещё не установлено."
    )
    panels.append(
        {
            "id": 1,
            "type": "text",
            "title": "Как читать цифры",
            "gridPos": {"x": 0, "y": 0, "w": 24, "h": 3},
            "options": {"mode": "markdown", "content": description + "\n\n" + coverage_text},
        }
    )

    def query(window: str, measure: str, bucket: str | None = None) -> str:
        selector = 'job="goaletto-worker",window="' + window + '",measure="' + measure + '"'
        if bucket is not None:
            selector += ',source="' + bucket + '"'
        value = "goaletto_audience{" + selector + "}" + FRESH
        if bucket is not None:
            value += (
                " and on(job,instance) (goaletto_audience_source_map == "
                + str(mapping_token(labels))
                + ")"
            )
        return "sum(" + value + ")"

    def add(
        title: str, targets: list[dict[str, Any]], kind: str, x: int, y: int, w: int, h: int
    ) -> None:
        options = (
            {
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "orientation": "auto",
                "textMode": "value",
                "colorMode": "value",
                "graphMode": "none",
            }
            if kind == "stat"
            else {
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "orientation": "horizontal",
                "displayMode": "basic",
                "showUnfilled": False,
                "valueMode": "color",
                "minVizHeight": 22,
            }
        )
        panels.append(
            {
                "id": len(panels) + 1,
                "title": title,
                "type": kind,
                "datasource": DS,
                "description": description,
                "gridPos": {"x": x, "y": y, "w": w, "h": h},
                "targets": targets,
                "fieldConfig": {
                    "defaults": {
                        "unit": "none",
                        "decimals": 0,
                        "min": 0,
                        "noValue": "Нет свежих данных",
                        "color": {"mode": "fixed", "fixedColor": "blue"},
                    },
                    "overrides": [],
                },
                "options": options,
            }
        )

    def target(ref: str, expr: str, legend: str) -> dict[str, Any]:
        return {
            "refId": ref,
            "datasource": DS,
            "expr": expr,
            "legendFormat": legend,
            "instant": True,
            "range": False,
            "format": "time_series",
        }

    cards = [
        ("Всего зарегистрировались", "all", "registered"),
        ("Активные · 24 часа", "1d", "active"),
        ("Активные · 7 дней", "7d", "active"),
        ("Активные · 30 дней", "30d", "active"),
        ("Новые · 24 часа", "1d", "registered"),
        ("Новые · 7 дней", "7d", "registered"),
        ("Новые · 30 дней", "30d", "registered"),
        ("Получили клуб · всего", "all", "claimed"),
    ]
    for i, (title, window, measure) in enumerate(cards):
        add(
            title,
            [target("A", query(window, measure), title)],
            "stat",
            i % 4 * 6,
            3 + i // 4 * 4,
            6,
            4,
        )
    names = [
        ("direct", "Без метки / напрямую"),
        *((x.bucket, x.code) for x in labels),
        ("unknown", "Неизвестный источник"),
        ("other", "Другие источники"),
    ]
    for title, window, measure, x, y, width in (
        ("Регистрации по источникам · всего", "all", "registered", 0, 11, 12),
        ("Новые регистрации по источникам · $period", "$period", "registered", 12, 11, 12),
        ("Активные по источникам · $period", "$period", "active", 0, 20, 24),
    ):
        targets = [
            target(f"S{i:02}", query(window, measure, bucket), name)
            for i, (bucket, name) in enumerate(names)
        ]
        add(title, targets, "bargauge", x, y, width, max(9, len(names)))
    # Keep rows separate even with the maximum source catalog.
    panels[-1]["gridPos"]["y"] = 11 + max(9, len(names))
    return {
        "uid": "goaletto-audience",
        "title": "Goaletto · игроки и источники",
        "description": description
        + " "
        + coverage_text
        + " Числа — текущие SQL-снимки фиксированных окон, не произвольный исторический cohort.",
        "schemaVersion": 39,
        "version": 1,
        "editable": False,
        "tags": ["goaletto", "players", "acquisition"],
        "timezone": "utc",
        "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "panels": panels,
        "templating": {
            "list": [
                {
                    "name": "period",
                    "label": "Период по источникам",
                    "type": "custom",
                    "query": "24 часа : 1d,7 дней : 7d,30 дней : 30d",
                    "current": {"text": "7 дней", "value": "7d"},
                    "options": [
                        {"text": text, "value": value, "selected": value == "7d"}
                        for text, value in (("24 часа", "1d"), ("7 дней", "7d"), ("30 дней", "30d"))
                    ],
                }
            ]
        },
    }


async def publish_dashboard(database: Database, path: Path) -> None:
    async with database.read_transaction() as session:
        await session.execute(text("SET LOCAL statement_timeout = '2000ms'"))
        labels = await source_labels(session)
        coverage = await session.scalar(
            text("SELECT started_at FROM analytics_coverage WHERE version=1")
        )
    content = json.dumps(build_dashboard(labels, coverage), ensure_ascii=False, indent=2) + "\n"

    def write() -> None:
        if path.exists() and path.read_text() == content:
            return
        # The dedicated directory contains only this dashboard, no datasource secrets.
        temporary = path.with_suffix(".pending")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    await asyncio.to_thread(write)
