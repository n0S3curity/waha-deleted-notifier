from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # WAHA connectivity
    waha_base_url: str = "http://localhost:3000"
    waha_api_key: SecretStr = SecretStr("")

    # Sessions
    waha_listen_session: str = "listener"  # receives webhook events, used for file capture
    waha_notify_session: str = "default"   # sends notifications

    # Notification target
    notify_group_id: str = ""  # e.g. 120363407713984498@g.us

    # Storage
    media_dir: str = "/data/media"
    db_path: str = "/data/bot.db"
    days_to_save_files: int = 7

    # Media attachments in notifications (requires WAHA Plus/Pro sendImage/sendFile).
    # False (default) = text-only notifications, safe for WAHA Core (free).
    send_media_attachments: bool = False

    # Dedup
    webhook_dedup_enabled: bool = True

    # Admin UI
    admin_username: str = "admin"
    admin_password_hash: str = ""
    secret_key: SecretStr = SecretStr("")
    session_max_age_seconds: int = 28800

    # Logging
    log_level: str = "INFO"


settings = Settings()
