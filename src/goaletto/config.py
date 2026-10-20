from pathlib import Path
from tempfile import gettempdir
from typing import Annotated

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GOALETTO_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: str = "development"
    bot_token: SecretStr = SecretStr("")
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://goaletto:goaletto_dev_only@localhost:5432/goaletto"
    )
    payments_enabled: bool = True
    payments_admin_chat_id: int = 0
    log_level: str = "INFO"
    worker_poll_seconds: float = Field(default=1, ge=0.1, le=60)
    worker_health_file: Path = Path(gettempdir()) / "goaletto-worker-instance"
    outbox_enabled: bool = False
    game_enabled: bool = False
    # Private Prometheus exposition only; no gameplay routes or public bind by default.
    metrics_enabled: bool = False
    metrics_host: str = "127.0.0.1"
    metrics_port: int = Field(default=9109, ge=1, le=65535)
    metrics_snapshot_seconds: float = Field(default=60, ge=15, le=3600)
    # JSON array in the host-only environment; empty preserves unrestricted access.
    allowed_telegram_ids: frozenset[Annotated[int, Field(gt=0)]] = frozenset()
    internal_telegram_ids: frozenset[Annotated[int, Field(gt=0)]] = frozenset()
    # Voluntary feedback (AND-78, manual/internal cohort only). Collection stays
    # disabled until an explicit comment retention is configured.
    feedback_enabled: bool = False
    feedback_automatic_enabled: bool = False
    feedback_comment_retention_days: int | None = Field(default=None, gt=0, le=730)
    feedback_cooldown_days: int = Field(default=7, ge=7, le=730)
