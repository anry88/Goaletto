"""Off-host backup publication never blesses corruption, partial IO or collisions."""

import hashlib
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "backup_hdc", Path(__file__).resolve().parents[2] / "scripts/backup_hdc.py"
)
assert spec and spec.loader
backup = importlib.util.module_from_spec(spec)
sys.modules["backup_hdc"] = backup
spec.loader.exec_module(backup)
STEM = "goaletto-20261020T100000Z-1234abcd"
DATA = b"PGDMP" + b"example" * 100


def metadata():
    return {
        "version": 1,
        "project": "goaletto",
        "database": "goaletto",
        "schema_revision": "0044_operator_reports",
        "filename": STEM + ".dump",
        "remote_path": backup.REMOTE_ROOT + "/backups/" + STEM + ".dump",
        "sha256": hashlib.sha256(DATA).hexdigest(),
        "bytes": len(DATA),
    }


def test_publish_verifies_private_immutable_pair_and_detects_tampering(tmp_path):
    part = tmp_path / ".dump.part"
    part.write_bytes(DATA)
    part.chmod(0o600)
    manifest = backup.publish(tmp_path, part, metadata())
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / (STEM + ".dump")).stat().st_mode & 0o777 == 0o600
    assert backup.recent_backup(tmp_path, datetime.now(UTC), 20)
    with pytest.raises(backup.BackupError, match="collision"):
        backup.publish(tmp_path, part, metadata())
    assert (tmp_path / (STEM + ".dump")).read_bytes() == DATA
    (tmp_path / (STEM + ".dump")).write_bytes(DATA + b"tamper")
    assert not backup.recent_backup(tmp_path, datetime.now(UTC), 20)


@pytest.mark.parametrize("content", [b"bad archive", DATA[:-1], DATA + b"corrupt"])
def test_bad_transfer_publishes_nothing(tmp_path, content):
    part = tmp_path / ".dump.part"
    part.write_bytes(content)
    with pytest.raises(backup.BackupError):
        backup.publish(tmp_path, part, metadata())
    assert list(tmp_path.glob("*.manifest.json")) == []
    assert list(tmp_path.glob("goaletto-*.dump")) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("project", "other"),
        ("database", "postgres"),
        ("filename", "../secret.dump"),
        ("remote_path", "D:/another-service/backup.dump"),
        ("sha256", "broken"),
        ("bytes", True),
    ],
)
def test_foreign_or_malformed_manifest_rejected(field, value):
    data = metadata()
    data[field] = value
    with pytest.raises(backup.BackupError):
        backup.validate_metadata(json.dumps(data), STEM)


def test_remote_scope_and_stem_cannot_expand_to_another_project():
    script = backup.remote_script(STEM)
    assert "label=com.docker.compose.project=goaletto" in script
    assert "label=com.docker.compose.service=postgres" in script
    assert "-U goaletto -d goaletto" in script
    assert "pg_restore --list" in script
    assert "POSTGRES_PASSWORD" not in script
    with pytest.raises(backup.BackupError):
        backup.remote_script("unsafe'; powershell bad")
    with pytest.raises(backup.BackupError):
        backup.validate_metadata(json.dumps(metadata()), "../escape")


def test_partial_transfer_and_secret_errors_are_not_reported_as_success(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "argv", ["backup_hdc", "--directory", str(tmp_path)])

    def fail(argv, **kwargs):
        raise OSError("PRIVATE-SECRET-MARKER")

    monkeypatch.setattr(backup, "call", fail)
    assert backup.main() == 1
    output = capsys.readouterr().out
    assert "PRIVATE-SECRET-MARKER" not in output
    assert json.loads(output)["status"] == "failed"
    assert not list(tmp_path.glob("*.dump"))
    assert not list(tmp_path.glob("*.manifest.json"))


def test_clock_reversal_does_not_suppress_backup(tmp_path):
    part = tmp_path / ".dump.part"
    part.write_bytes(DATA)
    manifest = backup.publish(tmp_path, part, metadata())
    now = datetime.now(UTC)
    os.utime(manifest, (now.timestamp() + 86400, now.timestamp() + 86400))
    assert not backup.recent_backup(tmp_path, now, 20)


def test_retention_deletes_only_older_than_fourteen_days_owned_pairs(tmp_path):
    now = datetime(2026, 10, 20, 12, tzinfo=UTC)
    paths = {}
    for age in (13, 14, 15):
        stamp = now - timedelta(days=age)
        stem = f"goaletto-{stamp:%Y%m%dT%H%M%SZ}-1234abcd"
        paths[age] = tmp_path / (stem + ".dump"), tmp_path / (stem + ".manifest.json")
        data = metadata()
        data.update(
            filename=stem + ".dump", remote_path=backup.REMOTE_ROOT + "/backups/" + stem + ".dump"
        )
        paths[age][0].write_bytes(DATA)
        paths[age][1].write_text(json.dumps(data))
    orphan = tmp_path / "goaletto-20260901T100000Z-1234abcd.dump"
    orphan.write_bytes(DATA)
    log = tmp_path / "backup.log"
    log.write_text("keep log")
    assert backup.prune_local(tmp_path, now) == 1
    assert all(p.exists() for age in (13, 14) for p in paths[age])
    assert not any(p.exists() for p in paths[15])
    assert orphan.exists() and log.exists()


@pytest.mark.parametrize("kind", ["foreign", "symlink", "bad_date"])
def test_retention_keeps_foreign_invalid_and_symlinked_files(tmp_path, kind):
    stem = "goaletto-20260901T100000Z-1234abcd"
    if kind == "bad_date":
        stem = "goaletto-20269999T100000Z-1234abcd"
    data = metadata()
    data.update(
        filename=stem + ".dump", remote_path=backup.REMOTE_ROOT + "/backups/" + stem + ".dump"
    )
    if kind == "foreign":
        data["database"] = "another_project"
    archive = tmp_path / (stem + ".dump")
    archive.write_bytes(DATA)
    manifest = tmp_path / (stem + ".manifest.json")
    manifest.write_text(json.dumps(data))
    if kind == "symlink":
        archive.unlink()
        archive.symlink_to(manifest)
    assert backup.prune_local(tmp_path, datetime(2026, 10, 20, tzinfo=UTC)) == 0
    assert manifest.exists() and archive.exists()


def test_cleanup_requires_verified_recent_backup_before_any_remote_deletion(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Must not call SSH without a verified recent backup")

    monkeypatch.setattr(backup, "call", forbidden)
    with pytest.raises(backup.BackupError, match="requires_verified_recent_backup"):
        backup.cleanup(tmp_path, datetime.now(UTC), "hdc")


def test_recent_backup_skips_dump_but_runs_retention(tmp_path, monkeypatch, capsys):
    part = tmp_path / ".dump.part"
    part.write_bytes(DATA)
    backup.publish(tmp_path, part, metadata())
    monkeypatch.setattr(
        sys, "argv", ["backup_hdc", "--directory", str(tmp_path), "--minimum-interval-hours", "20"]
    )
    calls = []

    def cleanup_remote(argv, **kwargs):
        calls.append(argv)
        return '{"removed_pairs":2}'

    monkeypatch.setattr(backup, "call", cleanup_remote)
    assert backup.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "recent_verified_backup" and result["hdc_removed_pairs"] == 2
    assert len(calls) == 1 and calls[0][0] == "ssh"
