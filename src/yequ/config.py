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
    postgres_application_name: str = "yequ-center"
    postgres_idle_in_transaction_session_timeout_ms: int = 120_000
    postgres_statement_timeout_ms: int = 120_000
    postgres_lock_timeout_ms: int = 10_000
    postgres_terminate_stale_idle_transactions_on_startup: bool = True

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
    session_audit_enabled: bool = True
    session_audit_log_dir: str = "var/session-audit"

    # Artifact storage
    artifact_storage_dir: str = "var/artifacts"
    artifact_max_upload_bytes: int = 50 * 1024 * 1024

    # YeQu Context Router
    ycr_backend: str = "http"
    ycr_base_url: str = "http://127.0.0.1:9810"
    ycr_timeout_sec: float = 45.0
    ycr_fail_closed: bool = True
    ycr_service_token: str = ""
    ycr_embedding_provider: str = "openai_compatible"
    ycr_embedding_model: str = "BAAI/bge-m3"
    ycr_embedding_base_url: str = ""
    ycr_embedding_api_key: str = ""
    ycr_embedding_timeout_sec: float = 60.0
    ycr_vector_dimensions: int = 1024
    ycr_projection_inline_bytes: int = 12000
    ycr_projection_preview_chars: int = 1200
    ycr_tool_result_inline_bytes: int = 2048
    ycr_tool_result_preview_chars: int = 1200
    ycr_context_block_inline_bytes: int = 16000
    ycr_context_block_preview_chars: int = 1600
    ycr_max_raw_ref_bytes: int = 100 * 1024 * 1024
    ycr_rerank_model: str = "BAAI/bge-reranker-base"
    ycr_rerank_top_k: int = 20
    ycr_rerank_timeout_sec: float = 90.0
    ycr_scheduler_embedding_concurrency: int = 1
    ycr_scheduler_rerank_concurrency: int = 1
    ycr_scheduler_background_batch_size: int = 5
    ycr_summary_provider: str = "openai_compatible"
    ycr_summary_base_url: str = ""
    ycr_summary_api_key: str = ""
    ycr_summary_model: str = ""
    ycr_summary_timeout_sec: float = 20.0
    ycr_summary_input_chars: int = 16_000

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
