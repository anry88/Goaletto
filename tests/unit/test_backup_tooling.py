"""Unit tests for scripts/backup_goaletto.py (AND-70 backup tooling wave).

Everything runs with a fake runner, fake environment, fixed clock and either a
fake file system or a pytest tmp_path. No real pg_dump, PostgreSQL, HDC or
network access is used here.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import stat
import sys
from collections.abc import Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backup_goaletto.py"


def load_script():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("backup_goaletto", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["backup_goaletto"] = module
    spec.loader.exec_module(module)
    return module


bt = load_script()

SECRET = "s3cr3t-PW-marker-9f8c"
LONG_STDERR = "FATAL: password authentication failed for user goaletto; " + SECRET * 4


@dataclass
class FakeRunner:
    """Injectable subprocess double; records argv/env, never touches secrets."""

    calls: list[tuple[list[str], dict[str, str]]] = field(default_factory=list)
    dump_bytes: bytes = b"PGDMP" + b"FAKEPGDUMP" * 64
    version_text: str = "pg_dump (PostgreSQL) 17.4\n"
    fail_returncode: int | None = None
    fail_stderr: str = ""
    raise_on_dump: BaseException | None = None
    version_returncode: int = 0

    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> bt.CommandResult:  # type: ignore[name-defined]
        self.calls.append((list(argv), dict(env)))
        if len(argv) >= 2 and argv[-1] == "--version":
            if self.version_returncode != 0:
                return bt.CommandResult(
                    returncode=self.version_returncode, stdout="", stderr=self.fail_stderr
                )
            return bt.CommandResult(returncode=0, stdout=self.version_text, stderr="")
        if "--command" in argv:
            return bt.CommandResult(0, "goaletto|170004|goaletto-schema", "")
        if self.raise_on_dump is not None:
            raise self.raise_on_dump
        if self.fail_returncode is not None:
            return bt.CommandResult(
                returncode=self.fail_returncode, stdout="", stderr=self.fail_stderr
            )
        return bt.CommandResult(returncode=0, stdout="", stderr="")


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)


class FakeFileOps:
    """In-memory file system double with injectable permission failures."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.modes: dict[str, int] = {}
        self.dirs: set[str] = set()
        self.fail_ensure: BaseException | None = None
        self.fail_create: BaseException | None = None
        self.removed: list[str] = []
        self._counter = 0

    def ensure_dir(self, path: Path) -> None:
        if self.fail_ensure is not None:
            raise self.fail_ensure
        self.dirs.add(str(path))

    def exists(self, path: Path) -> bool:
        return str(path) in self.files

    def create_temp(self, prefix: str, suffix: str, directory: Path) -> Path:
        if self.fail_create is not None:
            raise self.fail_create
        self._counter += 1
        path = directory / f"{prefix}{self._counter:04d}{suffix}"
        self.files[str(path)] = b""
        self.modes[str(path)] = 0o600
        return path

    def write_bytes(self, path: Path, data: bytes) -> None:
        self.files[str(path)] = bytes(data)

    def read_bytes(self, path: Path) -> bytes:
        try:
            return self.files[str(path)]
        except KeyError:
            raise FileNotFoundError(str(path)) from None

    def sha256_and_size(self, path: Path) -> tuple[str, int]:
        data = self.read_bytes(path)
        return hashlib.sha256(data).hexdigest(), len(data)

    def chmod(self, path: Path, mode: int) -> None:
        if str(path) not in self.files:
            raise FileNotFoundError(str(path))
        self.modes[str(path)] = mode

    def publish(self, src: Path, dst: Path) -> None:
        if self.exists(dst):
            raise FileExistsError(str(dst))
        data = self.read_bytes(src)
        self.files[str(dst)] = data
        self.modes[str(dst)] = self.modes.get(str(src), 0o600)

    def read_prefix(self, path: Path, size: int) -> bytes:
        return self.read_bytes(path)[:size]

    def remove(self, path: Path) -> None:
        self.removed.append(str(path))
        self.files.pop(str(path), None)


class DumpWritingRunner(FakeRunner):
    """Fake pg_dump that writes archive bytes to the --file target."""

    def __init__(self, files: FakeFileOps, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self._files = files

    def run(self, argv: Sequence[str], env: Mapping[str, str]):  # type: ignore[no-untyped-def]
        result = super().run(argv, env)
        if result.returncode == 0 and "--file" in list(argv):
            target = Path(list(argv)[list(argv).index("--file") + 1])
            self._files.write_bytes(target, self.dump_bytes)
        return result


def run_main(
    args: list[str],
    *,
    runner=None,  # type: ignore[no-untyped-def]
    environ=None,  # type: ignore[no-untyped-def]
    file_ops=None,  # type: ignore[no-untyped-def]
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = bt.main(
            args,
            runner=runner or FakeRunner(),
            environ=environ if environ is not None else {},
            clock=FakeClock(),
            file_ops=file_ops or FakeFileOps(),
        )
    return code, out.getvalue(), err.getvalue()


def base_args(**overrides):  # type: ignore[no-untyped-def]
    args: dict[str, object] = {
        "target": "local-goaletto",
        "output_dir": "/backups",
        "timestamp": "20261013T120000Z",
    }
    args.update(overrides)
    flat = ["--target", str(args["target"]), "--output-dir", str(args["output_dir"])]
    if "timestamp" in args and args["timestamp"] is not None:
        flat += ["--timestamp", str(args["timestamp"])]
    return flat


def test_dry_run_is_default_and_prints_safe_plan() -> None:
    runner = FakeRunner()
    files = FakeFileOps()
    code, out, err = run_main(
        base_args(), runner=runner, environ={"PGPASSWORD": SECRET}, file_ops=files
    )
    assert code == 0
    assert runner.calls == []
    assert files.files == {}
    assert "local-goaletto" in out
    assert "goaletto" in out
    assert "dry run" in out.lower()
    assert SECRET not in out
    assert SECRET not in err


def test_dry_run_shows_no_password_or_dsn() -> None:
    code, out, err = run_main(
        base_args(),
        runner=FakeRunner(),
        environ={
            "PGPASSWORD": SECRET,
            "DATABASE_URL": f"postgresql://goaletto:{SECRET}@x/goaletto",
        },
    )
    assert code == 0
    assert SECRET not in out + err


def test_apply_success_writes_archive_and_manifest() -> None:
    files = FakeFileOps()
    runner = DumpWritingRunner(files)
    code, out, err = run_main(
        base_args() + ["--apply"],
        runner=runner,
        environ={"PGPASSWORD": SECRET},
        file_ops=files,
    )
    assert code == 0
    archive = "/backups/goaletto-20261013T120000Z.dump"
    manifest = "/backups/goaletto-20261013T120000Z.manifest.json"
    assert files.files[archive] == runner.dump_bytes
    data = json.loads(files.files[manifest].decode())
    assert data["tool"] == "backup_goaletto"
    assert data["tool_version"] == "1.0.0"
    assert data["target"] == "local-goaletto"
    assert data["dbname"] == "goaletto"
    assert data["format"] == "custom"
    assert data["pg_dump_version"] == "17.4"
    assert data["created_utc"] == "2026-10-13T12:00:00Z"
    assert data["size_bytes"] == len(runner.dump_bytes)
    assert data["sha256"] == hashlib.sha256(runner.dump_bytes).hexdigest()
    assert SECRET not in files.files[manifest].decode()
    assert SECRET not in out + err
    # Owner-only permissions on finished files.
    assert files.modes[archive] == 0o600
    assert files.modes[manifest] == 0o600
    # pg_dump argv carries connection flags, never the password.
    dump_calls = [c for c in runner.calls if "--format" in c[0]]
    assert len(dump_calls) == 1
    argv, env = dump_calls[0]
    assert "--file" in argv and "--format" in argv
    assert SECRET not in " ".join(argv)
    assert env.get("PGPASSWORD") == SECRET


def test_failed_dump_leaves_no_final_files() -> None:
    files = FakeFileOps()
    runner = FakeRunner(fail_returncode=1, fail_stderr=LONG_STDERR)
    code, out, err = run_main(
        base_args() + ["--apply"],
        runner=runner,
        environ={"PGPASSWORD": SECRET},
        file_ops=files,
    )
    assert code == 13
    assert "PG_DUMP_FAILED" in err
    assert LONG_STDERR not in out + err
    assert SECRET not in out + err
    temp_prefixes = (".dump-", ".manifest-", ".probe-")
    finals = [p for p in files.files if not Path(p).name.startswith(temp_prefixes)]
    assert finals == []
    assert files.removed, "partial temp files must be cleaned up"


def test_interrupted_partial_file_is_removed() -> None:
    files = FakeFileOps()

    class InterruptingRunner(FakeRunner):
        def run(self, argv, env):  # type: ignore[no-untyped-def]
            self.calls.append((list(argv), dict(env)))
            if len(argv) >= 2 and argv[-1] == "--version":
                return bt.CommandResult(returncode=0, stdout=self.version_text, stderr="")
            if "--command" in argv:
                return FakeRunner.run(self, argv, env)
            target = Path(list(argv)[list(argv).index("--file") + 1])
            files.write_bytes(target, b"PARTIAL-DUMP")
            raise KeyboardInterrupt

    code, out, err = run_main(
        base_args() + ["--apply"], runner=InterruptingRunner(), environ={}, file_ops=files
    )
    assert code == 14
    assert "INTERRUPTED" in err
    assert "/backups/goaletto-20261013T120000Z.dump" not in files.files


def test_collision_refuses_silent_overwrite() -> None:
    files = FakeFileOps()
    archive = "/backups/goaletto-20261013T120000Z.dump"
    files.files[archive] = b"EXISTING-GOOD-BACKUP"
    runner = DumpWritingRunner(files)
    code, _out, err = run_main(base_args() + ["--apply"], runner=runner, environ={}, file_ops=files)
    assert code == 16
    assert "OUTPUT_COLLISION" in err
    assert files.files[archive] == b"EXISTING-GOOD-BACKUP"


def test_explicit_overwrite_cannot_destroy_archive() -> None:
    files = FakeFileOps()
    archive = "/backups/goaletto-20261013T120000Z.dump"
    files.files[archive] = b"OLD-BACKUP"
    files.modes[archive] = 0o600
    runner = DumpWritingRunner(files, dump_bytes=b"NEW-BACKUP")
    code, _out, _err = run_main(
        base_args() + ["--apply", "--overwrite"], runner=runner, environ={}, file_ops=files
    )
    assert code == 16
    assert files.files[archive] == b"OLD-BACKUP"


def test_wrong_database_is_rejected_before_dump() -> None:
    runner = FakeRunner()
    code, _out, err = run_main(
        ["--target", "local-goaletto", "--dbname", "otherdb", "--apply"],
        runner=runner,
        environ={},
    )
    assert code == 10
    assert "WRONG_DATABASE" in err
    assert runner.calls == []


@pytest.mark.parametrize("bad", ["", "   ", "all", "*", "all-databases", "postgres_data"])
def test_wrong_target_is_rejected_before_dump(bad: str) -> None:
    runner = FakeRunner()
    code, _out, err = run_main(["--target", bad, "--apply"], runner=runner, environ={})
    assert code in (2, 11)
    if code == 11:
        assert "WRONG_TARGET" in err
    assert runner.calls == []


def test_missing_pg_dump_maps_to_safe_code() -> None:
    files = FakeFileOps()

    class MissingRunner(FakeRunner):
        def run(self, argv, env):  # type: ignore[no-untyped-def]
            self.calls.append((list(argv), dict(env)))
            raise FileNotFoundError("pg_dump")

    code, _out, err = run_main(
        base_args() + ["--apply"], runner=MissingRunner(), environ={}, file_ops=files
    )
    assert code == 12
    assert "PG_DUMP_NOT_FOUND" in err
    assert "Traceback" not in err


def test_permission_error_maps_to_safe_code() -> None:
    files = FakeFileOps()
    files.fail_ensure = PermissionError("/backups")
    code, _out, err = run_main(
        base_args() + ["--apply"], runner=FakeRunner(), environ={}, file_ops=files
    )
    assert code == 17
    assert "PERMISSION_DENIED" in err


def test_manifest_checksum_detects_tampering(tmp_path: Path) -> None:
    files = FakeFileOps()
    runner = DumpWritingRunner(files)
    code, _, _ = run_main(base_args() + ["--apply"], runner=runner, environ={}, file_ops=files)
    assert code == 0
    manifest = json.loads(files.files["/backups/goaletto-20261013T120000Z.manifest.json"].decode())
    assert bt.verify_manifest(manifest, files, Path("/backups")) is True
    files.files["/backups/goaletto-20261013T120000Z.dump"] += b"tamper"
    assert bt.verify_manifest(manifest, files, Path("/backups")) is False
    assert bt.verify_manifest({"archive": "x"}, files, Path("/backups")) is False
    _ = tmp_path


def test_no_secrets_in_argv_stdout_stderr_or_manifest() -> None:
    files = FakeFileOps()
    runner = DumpWritingRunner(files, fail_stderr=LONG_STDERR)
    code, out, err = run_main(
        base_args() + ["--apply"],
        runner=runner,
        environ={"PGPASSWORD": SECRET},
        file_ops=files,
    )
    assert code == 0
    blob = out + err
    for _argv, _env in runner.calls:
        blob += " ".join(_argv)
    for content in files.files.values():
        blob += content.decode("utf-8", "replace")
    assert SECRET not in blob


def test_real_filesystem_roundtrip_with_fake_runner(tmp_path: Path) -> None:
    outdir = tmp_path / "backups"
    runner = FakeRunner()

    real_calls = runner.calls

    class WritingRunner(FakeRunner):
        def run(self, argv, env):  # type: ignore[no-untyped-def]
            result = FakeRunner.run(self, argv, env)
            if result.returncode == 0 and "--file" in list(argv):
                Path(list(argv)[list(argv).index("--file") + 1]).write_bytes(b"PGDMPREALFS-DUMP")
            return result

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = bt.main(
            [
                "--target",
                "local-goaletto",
                "--output-dir",
                str(outdir),
                "--timestamp",
                "20261013T120000Z",
                "--apply",
            ],
            runner=WritingRunner(),
            environ={},
            clock=FakeClock(),
            file_ops=bt.OsFileOps(),
        )
    assert code == 0
    archive = outdir / "goaletto-20261013T120000Z.dump"
    manifest_path = outdir / "goaletto-20261013T120000Z.manifest.json"
    assert archive.read_bytes() == b"PGDMPREALFS-DUMP"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["sha256"] == hashlib.sha256(b"PGDMPREALFS-DUMP").hexdigest()
    assert stat.S_IMODE(os.stat(archive).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(manifest_path).st_mode) == 0o600
    assert real_calls == []


def test_wrong_actual_schema_is_rejected_before_dump() -> None:
    class WrongTarget(FakeRunner):
        def run(self, argv, env):
            if "--command" in argv:
                return bt.CommandResult(0, "goaletto|170004|wrong-schema", "")
            return super().run(argv, env)

    runner = WrongTarget()
    code, _, err = run_main(base_args() + ["--apply"], runner=runner)
    assert code == 11 and "WRONG_TARGET" in err
    assert not any("--format" in argv for argv, env in runner.calls)


def test_publication_failure_cleans_only_own_archive() -> None:
    class FailingPublication(FakeFileOps):
        def publish(self, src, dst):
            if dst.name.endswith(".manifest.json"):
                raise PermissionError("publication failed")
            super().publish(src, dst)

    files = FailingPublication()
    code, _, _ = run_main(
        base_args() + ["--apply"], runner=DumpWritingRunner(files), file_ops=files
    )
    assert code == 17 and files.files == {}


def test_collision_during_publication_preserves_concurrent_backup() -> None:
    class RacingPublication(FakeFileOps):
        def publish(self, src, dst):
            self.files[str(dst)] = b"OTHER-COMPLETED-BACKUP"
            super().publish(src, dst)

    files = RacingPublication()
    code, _, _ = run_main(
        base_args() + ["--apply"], runner=DumpWritingRunner(files), file_ops=files
    )
    assert code == 16
    assert list(files.files.values()) == [b"OTHER-COMPLETED-BACKUP"]


def test_version_output_cannot_leak_secret_and_empty_dump_is_rejected() -> None:
    files = FakeFileOps()
    runner = DumpWritingRunner(files, version_text=SECRET, dump_bytes=b"")
    code, out, err = run_main(base_args() + ["--apply"], runner=runner, file_ops=files)
    assert code == 13 and SECRET not in out + err and files.files == {}
    assert bt.pg_dump_version(FakeRunner(version_text=SECRET), "pg_dump", {}) == "unknown"
