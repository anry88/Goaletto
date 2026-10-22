"""Native dashboard mappings remain bounded, fresh and source-label safe."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

from goaletto.infrastructure.audience import SourceLabel, mapping_token
from goaletto.infrastructure.audience_dashboard import build_dashboard, publish_dashboard


def test_native_panels_have_real_counts_freshness_and_guarded_source_mapping():
    labels = (SourceLabel("c01", "Private_CaseSensitive", "one"),)
    dashboard = build_dashboard(labels, datetime(2026, 10, 20, tzinfo=UTC))
    assert len(dashboard["panels"]) == 12
    assert "24 часа" in str(dashboard) and "30 дней" in str(dashboard)
    for panel in dashboard["panels"]:
        for query in panel.get("targets", []):
            assert query["datasource"]["uid"] == "goaletto-vm"
            assert "goaletto_db_snapshot_success" in query["expr"]
            assert "goaletto_db_snapshot_timestamp_seconds" in query["expr"]
            assert "Private_CaseSensitive" not in query["expr"]
            if query["legendFormat"] == "Private_CaseSensitive":
                assert str(mapping_token(labels)) in query["expr"]
    changed = (SourceLabel("c01", "Different", "two"),)
    assert mapping_token(labels) != mapping_token(changed)
    assert 0 <= mapping_token(labels) < 2**48


async def test_source_names_refresh_atomically_without_rewriting_unchanged_dashboard(tmp_path):
    rows = [{"id": "one", "code": "first"}]

    class Session:
        async def execute(self, statement):
            class Rows:
                def mappings(self):
                    return iter(rows)

            return Rows()

        async def scalar(self, statement):
            return datetime(2026, 10, 20, tzinfo=UTC)

    @asynccontextmanager
    async def read():
        yield Session()

    database = SimpleNamespace(read_transaction=read)
    path = tmp_path / "audience.json"
    await publish_dashboard(database, path)
    before = path.stat().st_mtime_ns
    await publish_dashboard(database, path)
    assert path.stat().st_mtime_ns == before
    rows.append({"id": "two", "code": "New_Source"})
    await publish_dashboard(database, path)
    assert "New_Source" in path.read_text()
    assert not path.with_suffix(".pending").exists()
