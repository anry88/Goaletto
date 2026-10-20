"""Run calibration beside the existing Mac Docker PostgreSQL, without a new DB.

The launcher verifies desktop-linux, Docker Desktop, Compose project/service and
its 127.0.0.1:5433 binding. Only that container's network is shared. No bot token
is passed and no polling/sender service starts. Install/build the native runtime
image with the command documented in docs/ten-season-calibration.md.
"""

import argparse
import json
import os
import subprocess
from pathlib import Path

from sqlalchemy import make_url

from goaletto.config import Settings


def output(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--seasons", type=int, default=10)
    parser.add_argument("--seed", type=int, default=71)
    parser.add_argument("--output", type=Path, default=Path("work/calibration-10-seasons"))
    args = parser.parse_args()
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    if (settings.environment, url.host, url.port, url.database) != (
        "development",
        "127.0.0.1",
        5433,
        "goaletto",
    ):
        raise SystemExit("Only the existing local development Goaletto DB is permitted")
    if output("docker", "context", "show") != "desktop-linux":
        raise SystemExit("Use only the verified Mac Docker Desktop context")
    if "Docker Desktop" not in output("docker", "info", "--format", "{{.OperatingSystem}}"):
        raise SystemExit("Not Mac Docker Desktop")
    ids = output(
        "docker",
        "ps",
        "-q",
        "--filter",
        "label=com.docker.compose.project=goaletto",
        "--filter",
        "label=com.docker.compose.service=postgres",
    ).splitlines()
    if len(ids) != 1:
        raise SystemExit("Expected exactly one existing local Goaletto postgres service")
    pg = json.loads(output("docker", "inspect", ids[0]))[0]
    if pg["NetworkSettings"]["Ports"].get("5432/tcp") != [
        {"HostIp": "127.0.0.1", "HostPort": "5433"}
    ]:
        raise SystemExit("Unexpected local Goaletto PostgreSQL binding")
    root = Path(__file__).resolve().parents[1]
    private = root / "work" / "calibration-local-private"
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(private, 0o700)
    marker = private / "container-id"
    marker.write_text(pg["Id"] + "\n")
    envfile = private / "runtime.env"
    descriptor = os.open(envfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    url = make_url(settings.database_url.get_secret_value()).set(host="127.0.0.1", port=5432)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(
            "GOALETTO_ENVIRONMENT=development\nGOALETTO_BOT_TOKEN=\n"
            "GOALETTO_GAME_ENABLED=false\nGOALETTO_OUTBOX_ENABLED=false\n"
            "GOALETTO_METRICS_ENABLED=false\nPYTHONPATH=/workspace/src:/workspace\n"
        )
        stream.write("GOALETTO_DATABASE_URL=" + url.render_as_string(hide_password=False) + "\n")
    os.chmod(envfile, 0o600)
    try:
        command = [
            "docker",
            "run",
            "--rm",
            "--init",
            "--name",
            "goaletto-calibration-local",
            "--network",
            "container:" + pg["Id"],
            "--env-file",
            str(envfile),
            "--volume",
            str(root) + ":/workspace",
            "--workdir",
            "/workspace",
            "--volume",
            str(marker) + ":/run/goaletto-calibration-local:ro",
            "--entrypoint",
            "python",
            "goaletto:calibration-local",
            "scripts/simulate_ten_seasons.py",
            "--local-service",
            "--seasons",
            str(args.seasons),
            "--seed",
            str(args.seed),
            "--output",
            str(args.output),
        ]
        command.append("--resume" if args.resume else "--reset-local-development")
        if args.report_only:
            command.append("--report-only")
        raise SystemExit(subprocess.call(command))
    finally:
        envfile.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
