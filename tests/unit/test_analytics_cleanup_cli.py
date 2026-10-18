import json

import pytest

from goaletto.application import analytics_cleanup_cli as cli


def test_preview_never_opens_database(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["cleanup", "--at", "2026-10-18T00:00:00+00:00"])
    monkeypatch.setattr(cli, "Database", lambda _: pytest.fail("Preview must not connect"))
    cli.main()
    assert json.loads(capsys.readouterr().out) == {
        "applied": False,
        "at": "2026-10-18T00:00:00+00:00",
    }


def test_failure_never_prints_exception_text(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["cleanup", "--at", "2026-10-18T00:00:00+00:00", "--apply"])

    async def fail(at):
        raise ValueError("SECRET_DSN_MUST_NOT_APPEAR")

    monkeypatch.setattr(cli, "apply", fail)
    with pytest.raises(SystemExit):
        cli.main()
    error = capsys.readouterr().err
    assert "ValueError" in error and "SECRET_DSN" not in error
