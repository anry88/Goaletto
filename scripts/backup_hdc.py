"""Pull one verified Goaletto backup from HDC to this Mac over SSH.

No credentials are copied or printed. PostgreSQL remains private; only the
container labelled project=goaletto/service=postgres is eligible. Completed
local backup pairs are immutable. A manifest is published only after checksum
verification, and a failed transfer leaves no successful-looking local dump.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

REMOTE_ROOT = "D:/HomeDataCenter/services/goaletto"
DEFAULT_DIRECTORY = Path.home() / "Documents" / "Goaletto Backups"


class BackupError(Exception):
    """Safe error code; subprocess output may contain operational secrets."""


def remote_script(stem: str) -> str:
    if not re.fullmatch(r"goaletto-\d{8}T\d{6}Z-[0-9a-f]{8}", stem):
        raise BackupError("invalid_backup_name")
    return r"""
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
try {
    $ids=@(docker ps -q --filter label=com.docker.compose.project=goaletto `
        --filter label=com.docker.compose.service=postgres)
    if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) { throw 'scope' }
    $id=$ids[0].Trim()
    $sql="SELECT current_database(), to_regclass('public.matches') IS NOT NULL " +
         "AND to_regclass('public.match_results') IS NOT NULL, " +
         "(SELECT version_num FROM alembic_version);"
    $probe=@($sql | docker exec -i $id psql -X -A -t -v ON_ERROR_STOP=1 `
        -U goaletto -d goaletto 2>$null)
    if ($LASTEXITCODE -ne 0 -or $probe.Count -ne 1) { throw 'target' }
    if ($probe[0] -notmatch '^goaletto\|t\|[0-9a-z_]+$') { throw 'target' }
    $revision=$probe[0].Split('|')[2]
    $directory='D:/HomeDataCenter/services/goaletto/backups'
    [IO.Directory]::CreateDirectory($directory) | Out-Null
    $stem='STEM'
    $inside='/tmp/'+$stem+'.dump'
    $partial=Join-Path $directory ($stem+'.dump.part')
    $final=Join-Path $directory ($stem+'.dump')
    if ((Test-Path $final) -or (Test-Path $partial)) { throw 'collision' }
    try {
        $ignored=docker exec $id pg_dump --no-password -U goaletto -d goaletto `
            --format=custom --file=$inside 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'dump' }
        $ignored=docker exec $id pg_restore --list $inside 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'archive' }
        $ignored=docker cp ($id+':'+$inside) $partial 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'copy' }
        Move-Item -LiteralPath $partial -Destination $final -ErrorAction Stop
        $hash=(Get-FileHash -Algorithm SHA256 -LiteralPath $final).Hash.ToLowerInvariant()
        $metadata=[ordered]@{
            version=1; project='goaletto'; database='goaletto'; schema_revision=$revision
            filename=($stem+'.dump'); sha256=$hash; bytes=(Get-Item $final).Length
            remote_path=$final.Replace('\','/'); created_at=[DateTime]::UtcNow.ToString('o')
        }
        $json=$metadata | ConvertTo-Json -Compress
        $manifest=Join-Path $directory ($stem+'.manifest.json')
        [IO.File]::WriteAllText($manifest, $json, (New-Object Text.UTF8Encoding $false))
        Write-Output $json
    } finally {
        $ignored=docker exec $id rm -f $inside 2>$null
        if (Test-Path $partial) { Remove-Item -LiteralPath $partial -Force }
    }
} catch { Write-Output '{"error":"hdc_backup_failed"}'; exit 1 }
""".replace("STEM", stem)


def call(argv: list[str], *, timeout: int = 600) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise BackupError("transport_failed")
    return result.stdout


def validate_metadata(raw: str, stem: str) -> dict[str, object]:
    if not re.fullmatch(r"goaletto-\d{8}T\d{6}Z-[0-9a-f]{8}", stem):
        raise BackupError("invalid_backup_name")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise BackupError("invalid_manifest") from error
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or data.get("project") != "goaletto"
        or data.get("database") != "goaletto"
        or data.get("filename") != stem + ".dump"
        or not isinstance(data.get("schema_revision"), str)
        or not re.fullmatch(r"[0-9a-z_]{1,64}", data["schema_revision"])
        or data.get("remote_path") != REMOTE_ROOT + "/backups/" + stem + ".dump"
        or not isinstance(data.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", data["sha256"])
        or type(data.get("bytes")) is not int
        or data["bytes"] < 6
    ):
        raise BackupError("invalid_manifest")
    return data


def publish(directory: Path, partial: Path, metadata: dict[str, object]) -> Path:
    """Verify a complete transfer before atomically publishing immutable names."""
    with partial.open("rb") as source:
        if source.read(5) != b"PGDMP":
            raise BackupError("invalid_archive")
        source.seek(0)
        digest = hashlib.file_digest(source, "sha256").hexdigest()
        os.fsync(source.fileno())
    if partial.stat().st_size != metadata["bytes"] or digest != metadata["sha256"]:
        raise BackupError("checksum_mismatch")
    name = str(metadata["filename"])
    if Path(name).name != name or not name.endswith(".dump"):
        raise BackupError("invalid_filename")
    final = directory / name
    manifest = directory / name.replace(".dump", ".manifest.json")
    descriptor, temporary = tempfile.mkstemp(prefix=".manifest-", suffix=".part", dir=directory)
    temporary_path = Path(temporary)
    linked = False
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(metadata, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        if manifest.exists():
            raise BackupError("output_collision")
        try:
            os.link(partial, final)
        except FileExistsError as error:
            raise BackupError("output_collision") from error
        linked = True
        os.link(temporary_path, manifest)
    except BaseException:
        if linked:
            final.unlink(missing_ok=True)
        raise
    finally:
        temporary_path.unlink(missing_ok=True)
    return manifest


def recent_backup(directory: Path, now: datetime, hours: float) -> bool:
    """Only verified complete local pairs suppress another scheduled attempt."""
    for manifest in directory.glob("goaletto-*.manifest.json"):
        age = now.timestamp() - manifest.stat().st_mtime
        if not 0 <= age < timedelta(hours=hours).total_seconds():
            continue
        try:
            data = json.loads(manifest.read_text())
            name = str(data["filename"])
            data = validate_metadata(json.dumps(data), name.removesuffix(".dump"))
            archive = directory / name
            with archive.open("rb") as source:
                valid = source.read(5) == b"PGDMP"
                source.seek(0)
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            if valid and digest == data["sha256"] and archive.stat().st_size == data["bytes"]:
                return True
        except (OSError, ValueError, KeyError, BackupError):
            continue
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--ssh-host", default="hdc")
    parser.add_argument("--minimum-interval-hours", type=float, default=0)
    args = parser.parse_args()
    partial = None
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", args.ssh_host):
            raise BackupError("invalid_ssh_alias")
        if not 0 <= args.minimum_interval_hours <= 24:
            raise BackupError("invalid_interval")
        directory = args.directory.expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        with (directory / ".backup.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print('{"status":"already_running"}')
                return 0
            now = datetime.now(UTC)
            if recent_backup(directory, now, args.minimum_interval_hours):
                print('{"status":"recent_verified_backup"}')
                return 0
            stem = f"goaletto-{now:%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
            encoded = base64.b64encode(remote_script(stem).encode("utf-16le")).decode("ascii")
            raw = call(
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=15",
                    args.ssh_host,
                    "powershell -NoProfile -NonInteractive -EncodedCommand " + encoded,
                ]
            )
            metadata = validate_metadata(raw.strip(), stem)
            descriptor, temporary = tempfile.mkstemp(prefix=".dump-", suffix=".part", dir=directory)
            os.close(descriptor)
            partial = Path(temporary)
            call(
                [
                    "scp",
                    "-q",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=15",
                    args.ssh_host + ":" + str(metadata["remote_path"]),
                    str(partial),
                ]
            )
            partial.chmod(0o600)
            metadata["source"] = args.ssh_host
            metadata["copied_at"] = datetime.now(UTC).isoformat()
            manifest = publish(directory, partial, metadata)
            print(
                json.dumps(
                    {"status": "verified", "manifest": str(manifest), "bytes": metadata["bytes"]}
                )
            )
            return 0
    except (BackupError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason": str(error)
                    if isinstance(error, BackupError)
                    else type(error).__name__,
                }
            )
        )
        return 1
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
