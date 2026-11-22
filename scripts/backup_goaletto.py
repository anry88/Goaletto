"""Verifiable pg_dump backup tool for the Goaletto PostgreSQL database.

Scope: produce one PostgreSQL custom-format archive plus a SHA256 manifest for
an explicitly agreed single-database target. The tool never enumerates all
databases, never copies ``.env`` files and never sends archives anywhere.

Default mode is a dry run: it prints a safe plan (no password, no credential
DSN) and performs no dump. A real dump requires the explicit ``--apply`` flag.

Reproduction (no real dump, safe everywhere)::

    python scripts/backup_goaletto.py --target local-goaletto
    python scripts/backup_goaletto.py --target local-goaletto --apply \\
        --host 127.0.0.1 --port 5433 --output-dir work/backups

Real-dump acceptance (Codex drill on the single Goaletto development DB)::

    PGPASSWORD='<from host-only secret>' python scripts/backup_goaletto.py \\
        --target local-goaletto --apply --output-dir work/backups
    shasum -a 256 work/backups/goaletto-*.dump
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

TOOL_NAME = "backup_goaletto"
TOOL_VERSION = "1.0.0"
EXPECTED_DBNAME = "goaletto"
DUMP_FORMAT = "custom"
ARCHIVE_SUFFIX = ".dump"
MANIFEST_SUFFIX = ".manifest.json"
TEMP_SUFFIX = ".part"
FILE_MODE = 0o600
PASSWORD_ENV_VAR = "PGPASSWORD"
TIMESTAMP_PATTERN = re.compile(r"^\d{8}T\d{6}Z$")

# Targets that would mean "everything" rather than one agreed database.
FORBIDDEN_TARGETS = frozenset(
    {
        "",
        "all",
        "*",
        "all-databases",
        "all-volumes",
        "postgres_data",
    }
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_WRONG_DATABASE = 10
EXIT_WRONG_TARGET = 11
EXIT_PG_DUMP_NOT_FOUND = 12
EXIT_PG_DUMP_FAILED = 13
EXIT_INTERRUPTED = 14
EXIT_OUTPUT_NOT_WRITABLE = 15
EXIT_OUTPUT_COLLISION = 16
EXIT_PERMISSION_DENIED = 17


class BackupError(Exception):
    """Safe, classified failure: carries a code, never secrets or raw stderr."""

    def __init__(self, code: str, exit_code: int, detail: str = "") -> None:
        super().__init__(detail)
        self.code = code
        self.exit_code = exit_code
        self.detail = detail


@dataclass(frozen=True)
class BackupConfig:
    target: str
    host: str
    port: int
    dbname: str
    username: str
    output_dir: Path
    pg_dump_bin: str
    timestamp: str
    overwrite: bool
    password_file: Path | None
    psql_bin: str = "psql"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner(Protocol):
    """Injectable subprocess seam (no shell anywhere)."""

    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> CommandResult: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class FileOps(Protocol):
    """Injectable file-system seam so unit tests never need real PostgreSQL."""

    def ensure_dir(self, path: Path) -> None: ...
    def exists(self, path: Path) -> bool: ...
    def create_temp(self, prefix: str, suffix: str, directory: Path) -> Path: ...
    def write_bytes(self, path: Path, data: bytes) -> None: ...
    def read_bytes(self, path: Path) -> bytes: ...
    def sha256_and_size(self, path: Path) -> tuple[str, int]: ...
    def chmod(self, path: Path, mode: int) -> None: ...
    def publish(self, src: Path, dst: Path) -> None: ...
    def read_prefix(self, path: Path, size: int) -> bytes: ...
    def remove(self, path: Path) -> None: ...


class SubprocessRunner:
    """Real runner: argv list only, secrets carried via env, stdin closed."""

    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> CommandResult:
        try:
            completed = subprocess.run(
                list(argv),
                env=dict(env),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                shell=False,
                check=False,
            )
        except FileNotFoundError as exc:
            raise BackupError(
                "PG_DUMP_NOT_FOUND", EXIT_PG_DUMP_NOT_FOUND, str(exc.filename or "")
            ) from exc
        except PermissionError as exc:
            raise BackupError("PERMISSION_DENIED", EXIT_PERMISSION_DENIED, "") from exc
        except OSError as exc:
            raise BackupError("PG_DUMP_FAILED", EXIT_PG_DUMP_FAILED, type(exc).__name__) from exc
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )


class UtcClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class OsFileOps:
    """Real file operations: owner-only temp files, atomic no-clobber publication."""

    def ensure_dir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def exists(self, path: Path) -> bool:
        return path.exists()

    def create_temp(self, prefix: str, suffix: str, directory: Path) -> Path:
        fd, name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=str(directory))
        os.close(fd)
        os.chmod(name, FILE_MODE)
        return Path(name)

    def write_bytes(self, path: Path, data: bytes) -> None:
        path.write_bytes(data)

    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()

    def sha256_and_size(self, path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    def chmod(self, path: Path, mode: int) -> None:
        os.chmod(path, mode)

    def publish(self, src: Path, dst: Path) -> None:
        # Atomic create, never clobber another completed/concurrent backup.
        os.link(src, dst)

    def read_prefix(self, path: Path, size: int) -> bytes:
        with path.open("rb") as handle:
            return handle.read(size)

    def remove(self, path: Path) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verifiable pg_dump backup for one agreed Goaletto database "
        "(dry run by default; real dump needs --apply)."
    )
    parser.add_argument(
        "--target",
        required=True,
        help="Explicit agreed target/project label, e.g. local-goaletto or "
        "a manually verified production container. Never inferred from a port number alone.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5433)
    parser.add_argument("--dbname", default=EXPECTED_DBNAME)
    parser.add_argument("--username", default=EXPECTED_DBNAME)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("work/backups"),
        help="Directory for the archive and its manifest.",
    )
    parser.add_argument(
        "--pg-dump",
        default="pg_dump",
        help="pg_dump executable (resolved via PATH unless a path is given).",
    )
    parser.add_argument(
        "--psql", default="psql", help="psql executable for read-only target verification"
    )
    parser.add_argument(
        "--timestamp",
        default=None,
        help="UTC stamp YYYYMMDDTHHMMSSZ; defaults to the current UTC time. "
        "An explicit stamp makes runs reproducible.",
    )
    parser.add_argument(
        "--password-file",
        type=Path,
        default=None,
        help="Host-only file holding the database password. The password is "
        "passed via the environment, never via argv, logs or the manifest. "
        "Falls back to the PGPASSWORD environment variable.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Deprecated: archives are immutable; use a new timestamp instead.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the real pg_dump. Without it, only a safe plan is printed.",
    )
    return parser


def validate_target(target: str) -> str:
    cleaned = target.strip()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", cleaned) or cleaned.lower() in FORBIDDEN_TARGETS:
        raise BackupError(
            "WRONG_TARGET",
            EXIT_WRONG_TARGET,
            "Target must be one explicitly agreed project label, "
            "not a wildcard, volume or database enumeration.",
        )
    return cleaned


def validate_dbname(dbname: str) -> str:
    if dbname != EXPECTED_DBNAME:
        raise BackupError(
            "WRONG_DATABASE",
            EXIT_WRONG_DATABASE,
            f"Refusing: database must be exactly {EXPECTED_DBNAME!r}.",
        )
    return dbname


def resolve_timestamp(raw: str | None, clock: Clock) -> str:
    if raw is not None:
        if not TIMESTAMP_PATTERN.match(raw):
            raise BackupError(
                "WRONG_TIMESTAMP",
                EXIT_USAGE,
                "Timestamp must look like YYYYMMDDTHHMMSSZ.",
            )
        return raw
    return clock.now().strftime("%Y%m%dT%H%M%SZ")


def build_config(args: argparse.Namespace, clock: Clock) -> BackupConfig:
    target = validate_target(args.target)
    dbname = validate_dbname(args.dbname)
    if not 1 <= args.port <= 65535:
        raise BackupError("WRONG_PORT", EXIT_USAGE, "Port must be 1-65535.")
    if args.overwrite:
        raise BackupError(
            "OUTPUT_COLLISION",
            EXIT_OUTPUT_COLLISION,
            "Use a new timestamp; completed archives are immutable.",
        )
    if (
        not args.host
        or any(c.isspace() for c in args.host)
        or "://" in args.host
        or "@" in args.host
    ):
        raise BackupError("WRONG_TARGET", EXIT_WRONG_TARGET)
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]*", args.username):
        raise BackupError("WRONG_TARGET", EXIT_WRONG_TARGET)
    timestamp = resolve_timestamp(args.timestamp, clock)
    return BackupConfig(
        target=target,
        host=args.host,
        port=args.port,
        dbname=dbname,
        username=args.username,
        output_dir=args.output_dir,
        pg_dump_bin=args.pg_dump,
        timestamp=timestamp,
        overwrite=args.overwrite,
        password_file=args.password_file,
        psql_bin=args.psql,
    )


def archive_stem(config: BackupConfig) -> str:
    return f"{config.dbname}-{config.timestamp}"


def archive_name(config: BackupConfig) -> str:
    return archive_stem(config) + ARCHIVE_SUFFIX


def manifest_name(config: BackupConfig) -> str:
    return archive_stem(config) + MANIFEST_SUFFIX


def safe_plan(config: BackupConfig) -> str:
    """Human-readable plan with no password and no credential DSN."""
    lines = [
        f"tool: {TOOL_NAME} {TOOL_VERSION} (dry run: no dump performed)",
        f"target: {config.target}",
        f"database: {config.dbname}",
        f"host: {config.host}",
        f"port: {config.port}",
        f"username: {config.username}",
        f"pg_dump: {config.pg_dump_bin}",
        f"format: {DUMP_FORMAT} (PostgreSQL custom-format archive)",
        f"archive: {config.output_dir / archive_name(config)}",
        f"manifest: {config.output_dir / manifest_name(config)}",
        "password: via environment/file only (never shown, never in argv)",
        "note: target names one agreed database; it is never inferred "
        "from a port number and never means 'all databases' or volumes.",
    ]
    return "\n".join(lines) + "\n"


def load_password(
    config: BackupConfig, environ: Mapping[str, str], file_ops: FileOps
) -> str | None:
    if config.password_file is not None:
        try:
            raw = file_ops.read_bytes(config.password_file)
        except FileNotFoundError as exc:
            raise BackupError("PASSWORD_FILE_MISSING", EXIT_USAGE, "") from exc
        except OSError as exc:
            raise BackupError(
                "PASSWORD_FILE_UNREADABLE", EXIT_PERMISSION_DENIED, type(exc).__name__
            ) from exc
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BackupError("PASSWORD_FILE_UNREADABLE", EXIT_PERMISSION_DENIED, "") from exc
        password = text.rstrip("\r\n")
        if not password:
            raise BackupError("PASSWORD_FILE_EMPTY", EXIT_USAGE, "")
        return password
    value = environ.get(PASSWORD_ENV_VAR)
    if value is not None and not value:
        return None
    return value


def dump_env(environ: Mapping[str, str], password: str | None) -> dict[str, str]:
    merged = dict(environ)
    if password is not None:
        merged[PASSWORD_ENV_VAR] = password
    else:
        merged.pop(PASSWORD_ENV_VAR, None)
    return merged


def dump_argv(config: BackupConfig, pg_dump_bin: str, tmp_dump: Path) -> list[str]:
    """Argv for pg_dump: connection flags only, never a password."""
    return [
        pg_dump_bin,
        "--no-password",
        "--host",
        config.host,
        "--port",
        str(config.port),
        "--username",
        config.username,
        "--dbname",
        config.dbname,
        "--format",
        DUMP_FORMAT,
        "--file",
        str(tmp_dump),
    ]


def pg_dump_version(runner: Runner, pg_dump_bin: str, env: Mapping[str, str]) -> str:
    try:
        result = runner.run([pg_dump_bin, "--version"], env)
    except FileNotFoundError as exc:
        raise BackupError("PG_DUMP_NOT_FOUND", EXIT_PG_DUMP_NOT_FOUND, "") from exc
    except PermissionError as exc:
        raise BackupError("PERMISSION_DENIED", EXIT_PERMISSION_DENIED, "") from exc
    except OSError as exc:
        raise BackupError("PG_DUMP_FAILED", EXIT_PG_DUMP_FAILED, type(exc).__name__) from exc
    if result.returncode != 0:
        raise BackupError("PG_DUMP_NOT_FOUND", EXIT_PG_DUMP_NOT_FOUND, "")
    text = result.stdout.strip() or "unknown"
    match = re.search(r"(\d+(?:\.\d+)+)", text)
    return match.group(1) if match else "unknown"


def verify_target(config: BackupConfig, runner: Runner, env: Mapping[str, str]) -> str:
    """Check actual database and application schema before pg_dump, no writes."""
    sql = """SELECT current_database() || '|' || current_setting('server_version_num') || '|' ||
      CASE WHEN to_regclass('public.clubs') IS NOT NULL
        AND to_regclass('public.managers') IS NOT NULL
        AND to_regclass('public.jobs') IS NOT NULL
        AND to_regclass('public.world_seed_receipts') IS NOT NULL
        AND to_regclass('public.alembic_version') IS NOT NULL
      THEN 'goaletto-schema' ELSE 'wrong-schema' END"""
    argv = [
        config.psql_bin,
        "--no-psqlrc",
        "--no-password",
        "--host",
        config.host,
        "--port",
        str(config.port),
        "--username",
        config.username,
        "--dbname",
        config.dbname,
        "--tuples-only",
        "--no-align",
        "--set",
        "ON_ERROR_STOP=1",
        "--command",
        sql,
    ]
    result = runner.run(argv, env)
    if result.returncode != 0:
        raise BackupError("TARGET_PROBE_FAILED", EXIT_PG_DUMP_FAILED)
    parts = result.stdout.strip().split("|")
    if (
        len(parts) != 3
        or parts[0] != EXPECTED_DBNAME
        or parts[2] != "goaletto-schema"
        or not parts[1].isdigit()
    ):
        raise BackupError("WRONG_TARGET", EXIT_WRONG_TARGET)
    return parts[1]


def build_manifest(
    *,
    config: BackupConfig,
    pg_version: str,
    archive_filename: str,
    sha256: str,
    size_bytes: int,
    created_utc: str,
    server_version_num: str,
) -> dict[str, object]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "target": config.target,
        "dbname": config.dbname,
        "host": config.host,
        "port": config.port,
        "username": config.username,
        "format": DUMP_FORMAT,
        "archive": archive_filename,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "created_utc": created_utc,
        "pg_dump": config.pg_dump_bin,
        "pg_dump_version": pg_version,
        "server_version_num": server_version_num,
    }


def verify_manifest(manifest: Mapping[str, object], file_ops: FileOps, directory: Path) -> bool:
    """Recompute the archive checksum; True only on an exact match."""
    try:
        archive = str(manifest["archive"])
        expected = str(manifest["sha256"])
        size_value = manifest["size_bytes"]
        if type(size_value) is not int:
            return False
        size = size_value
    except (KeyError, TypeError, ValueError):
        return False
    if (
        Path(archive).name != archive
        or not archive.endswith(ARCHIVE_SUFFIX)
        or size <= 0
        or not re.fullmatch(r"[0-9a-f]{64}", expected)
    ):
        return False
    try:
        actual_sha, actual_size = file_ops.sha256_and_size(directory / archive)
    except OSError:
        return False
    return actual_sha == expected and actual_size == size


def _remove_quietly(file_ops: FileOps, path: Path | None) -> None:
    if path is None:
        return
    try:
        file_ops.remove(path)
    except OSError:
        pass


def perform_backup(
    config: BackupConfig,
    *,
    runner: Runner,
    environ: Mapping[str, str],
    clock: Clock,
    file_ops: FileOps,
) -> dict[str, object]:
    """Run pg_dump into a temp file, then publish an immutable archive and completion manifest."""
    try:
        file_ops.ensure_dir(config.output_dir)
    except PermissionError as exc:
        raise BackupError("PERMISSION_DENIED", EXIT_PERMISSION_DENIED, "") from exc
    except OSError as exc:
        raise BackupError(
            "OUTPUT_NOT_WRITABLE", EXIT_OUTPUT_NOT_WRITABLE, type(exc).__name__
        ) from exc

    final_dump = config.output_dir / archive_name(config)
    final_manifest = config.output_dir / manifest_name(config)
    if file_ops.exists(final_dump) or file_ops.exists(final_manifest):
        raise BackupError(
            "OUTPUT_COLLISION",
            EXIT_OUTPUT_COLLISION,
            "Archive or manifest already exists; pick another timestamp.",
        )

    try:
        probe = file_ops.create_temp(
            prefix=".probe-", suffix=TEMP_SUFFIX, directory=config.output_dir
        )
    except PermissionError as exc:
        raise BackupError("PERMISSION_DENIED", EXIT_PERMISSION_DENIED, "") from exc
    except OSError as exc:
        raise BackupError(
            "OUTPUT_NOT_WRITABLE", EXIT_OUTPUT_NOT_WRITABLE, type(exc).__name__
        ) from exc
    _remove_quietly(file_ops, probe)

    password = load_password(config, environ, file_ops)
    env = dump_env(environ, password)

    resolved_bin = config.pg_dump_bin
    # No shell resolution here: the --version probe below is the availability
    # check. A missing executable surfaces as PG_DUMP_NOT_FOUND from the runner.

    pg_version = pg_dump_version(runner, resolved_bin, env)
    try:
        server_version_num = verify_target(config, runner, env)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise BackupError("TARGET_PROBE_FAILED", EXIT_PG_DUMP_FAILED) from exc

    published_dump: Path | None = None
    published_manifest: Path | None = None
    succeeded = False
    tmp_dump: Path | None = None
    tmp_manifest: Path | None = None
    try:
        tmp_dump = file_ops.create_temp(
            prefix=".dump-", suffix=TEMP_SUFFIX, directory=config.output_dir
        )
        try:
            result = runner.run(dump_argv(config, resolved_bin, tmp_dump), env)
        except FileNotFoundError as exc:
            raise BackupError("PG_DUMP_NOT_FOUND", EXIT_PG_DUMP_NOT_FOUND, "") from exc
        except PermissionError as exc:
            raise BackupError("PERMISSION_DENIED", EXIT_PERMISSION_DENIED, "") from exc
        except OSError as exc:
            raise BackupError("PG_DUMP_FAILED", EXIT_PG_DUMP_FAILED, type(exc).__name__) from exc
        if result.returncode != 0:
            raise BackupError(
                "PG_DUMP_FAILED",
                EXIT_PG_DUMP_FAILED,
                f"pg_dump exited with returncode={result.returncode}",
            )
        sha256, size = file_ops.sha256_and_size(tmp_dump)
        if size <= 5 or file_ops.read_prefix(tmp_dump, 5) != b"PGDMP":
            raise BackupError("INVALID_ARCHIVE", EXIT_PG_DUMP_FAILED)
        created_utc = clock.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        manifest = build_manifest(
            config=config,
            pg_version=pg_version,
            archive_filename=archive_name(config),
            sha256=sha256,
            size_bytes=size,
            created_utc=created_utc,
            server_version_num=server_version_num,
        )
        payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        tmp_manifest = file_ops.create_temp(
            prefix=".manifest-", suffix=TEMP_SUFFIX, directory=config.output_dir
        )
        file_ops.write_bytes(tmp_manifest, payload)
        file_ops.chmod(tmp_manifest, FILE_MODE)
        file_ops.chmod(tmp_dump, FILE_MODE)
        # Publish only after both temp files are complete and hashed.
        file_ops.publish(tmp_dump, final_dump)
        published_dump = final_dump
        file_ops.publish(tmp_manifest, final_manifest)
        published_manifest = final_manifest
        file_ops.chmod(final_dump, FILE_MODE)
        file_ops.chmod(final_manifest, FILE_MODE)
        # Genuine re-read of the published files: never leave behind a pair
        # whose checksum does not match the manifest.
        if not verify_manifest(manifest, file_ops, config.output_dir):
            _remove_quietly(file_ops, published_dump)
            _remove_quietly(file_ops, published_manifest)
            published_dump = None
            published_manifest = None
            raise BackupError("MANIFEST_MISMATCH", EXIT_PG_DUMP_FAILED, "")
        succeeded = True
    except FileExistsError as exc:
        raise BackupError("OUTPUT_COLLISION", EXIT_OUTPUT_COLLISION) from exc
    except KeyboardInterrupt as exc:
        raise BackupError("INTERRUPTED", EXIT_INTERRUPTED, "") from exc
    except BackupError:
        raise
    except PermissionError as exc:
        raise BackupError("PERMISSION_DENIED", EXIT_PERMISSION_DENIED, "") from exc
    except OSError as exc:
        raise BackupError(
            "OUTPUT_NOT_WRITABLE", EXIT_OUTPUT_NOT_WRITABLE, type(exc).__name__
        ) from exc
    finally:
        # Remove only paths this attempt created. Never damage an older backup.
        if not succeeded:
            _remove_quietly(file_ops, published_manifest)
            _remove_quietly(file_ops, published_dump)
        # Temp names never resemble a finished backup, and leftovers are removed.
        _remove_quietly(file_ops, tmp_dump)
        _remove_quietly(file_ops, tmp_manifest)
    return manifest


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner | None = None,
    environ: Mapping[str, str] | None = None,
    clock: Clock | None = None,
    file_ops: FileOps | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    active_runner = runner if runner is not None else SubprocessRunner()
    active_environ = environ if environ is not None else os.environ
    active_clock = clock if clock is not None else UtcClock()
    active_files = file_ops if file_ops is not None else OsFileOps()

    try:
        config = build_config(args, active_clock)
    except BackupError as exc:
        print(f"error: code={exc.code}", file=sys.stderr)
        if exc.detail:
            print(f"error detail: {exc.detail}", file=sys.stderr)
        return exc.exit_code

    if not args.apply:
        sys.stdout.write(safe_plan(config))
        return EXIT_OK

    try:
        manifest = perform_backup(
            config,
            runner=active_runner,
            environ=active_environ,
            clock=active_clock,
            file_ops=active_files,
        )
    except BackupError as exc:
        # Safe classes/codes only: no secrets, no external stderr text.
        print(f"error: code={exc.code}", file=sys.stderr)
        if exc.detail and "returncode" in exc.detail:
            print(f"error detail: {exc.detail}", file=sys.stderr)
        return exc.exit_code
    sys.stdout.write(
        "backup complete\n"
        f"archive: {config.output_dir / archive_name(config)}\n"
        f"manifest: {config.output_dir / manifest_name(config)}\n"
        f"sha256: {manifest['sha256']}\n"
        f"size_bytes: {manifest['size_bytes']}\n"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
