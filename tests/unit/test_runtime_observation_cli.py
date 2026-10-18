"""The operational preview never connects or loads host secrets."""

import json

from goaletto.application import runtime_observation_cli as cli


def test_runtime_preview_without_database(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("Preview must not load credentials")

    monkeypatch.setattr(cli, "Settings", forbidden)
    monkeypatch.setattr(cli, "Database", forbidden)
    monkeypatch.setattr(
        "sys.argv",
        ["runtime-observation", "--since", "2026-10-18T00:00:00Z", "--at", "2026-10-18T01:00:00Z"],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out) == {
        "database_connected": False,
        "apply_required": True,
    }
