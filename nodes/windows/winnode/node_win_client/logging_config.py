from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path


class TokenRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        token = os.environ.get("NODE_TOKEN", "")
        if token and hasattr(record, "msg") and isinstance(record.msg, str):
            record.msg = record.msg.replace(token, "***REDACTED***")
        return True


def setup_logging(
    log_dir: str | Path = "logs",
    log_file: str = "yequ-win-client.log",
    level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 10,
) -> logging.Logger:
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("yequ-win-client")
    logger.setLevel(level)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    handler = logging.handlers.RotatingFileHandler(
        log_path / log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    handler.addFilter(TokenRedactingFilter())
    logger.addHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(TokenRedactingFilter())
    logger.addHandler(console)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"yequ-win-client.{name}")
    return logging.getLogger("yequ-win-client")
