"""Preview must remain safe without a configured database or environment file."""

import json

from goaletto.application import world_capacity_cli


def test_reserve_preview_never_constructs_settings_or_database(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("Preview must not access runtime credentials")

    monkeypatch.setattr(world_capacity_cli, "Settings", forbidden)
    monkeypatch.setattr(world_capacity_cli, "Database", forbidden)
    monkeypatch.setattr("sys.argv", ["world-capacity"])
    world_capacity_cli.main()
    assert json.loads(capsys.readouterr().out) == {
        "database_connected": False,
        "apply_required": True,
    }
