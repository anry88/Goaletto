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
    log_level: str = "INFO"
    worker_poll_seconds: float = Field(default=1, ge=0.1, le=60)
    outbox_enabled: bool = False
    game_enabled: bool = False
