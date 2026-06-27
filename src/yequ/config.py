"""Application configuration via Pydantic Settings.

Reads from environment variables, .env file, or defaults.
Prefix: YEQU_
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """YeQu Center configuration."""

    model_config = SettingsConfigDict(
        env_prefix="YEQU_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_name: str = "YeQu Center"
    debug: bool = False
    test_mode: bool = False  # Skip background tasks in test

    # Database
    database_url: str = "postgresql+asyncpg://yequ:yequ@127.0.0.1:5432/yequ"
    database_pool_size: int = 20
    database_max_overflow: int = 20
    database_pool_timeout_sec: float = 10.0
    database_pool_recycle_sec: int = 1800

    # Node protocol
    allowed_timestamp_skew_sec: int = 30
    message_dedup_ttl_sec: int = 300  # 5 minutes
    default_heartbeat_interval_sec: int = 10
    default_heartbeat_timeout_multiplier: int = 3
    default_signal_report_interval_sec: int = 5
    default_signal_stale_multiplier: int = 3
    default_job_poll_interval_sec: int = 3
    default_lease_sec: int = 30

    # Recovery
    recovery_window_sec: int = 300  # 5 minutes for nodes to rejoin after restart

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # json or console

    # Auth
    require_admin_auth: bool = True  # Admin/Agent token scope enforced

    # Timeline
    debug_timeline: bool = False  # Set YEQU_DEBUG_TIMELINE=true to write diagnostic events

    # DeepSeek LLM Provider
    deepseek_api_key: str = ""  # Set via YEQU_DEEPSEEK_API_KEY env var
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_max_retries: int = 2  # Max retry attempts on timeout/rate-limit
    deepseek_retry_backoff_base: float = 1.0  # Backoff base in seconds (1s, 2s, 4s...)
    deepseek_read_timeout: float = 30.0  # Read timeout in seconds

    @property
    def heartbeat_timeout_sec(self) -> int:
        return self.default_heartbeat_interval_sec * self.default_heartbeat_timeout_multiplier

    @property
    def signal_stale_sec(self) -> int:
        return self.default_signal_report_interval_sec * self.default_signal_stale_multiplier


# Singleton cache
_settings: Settings | None = None


def get_settings() -> Settings:
    """Return a cached Settings instance (loads once)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
