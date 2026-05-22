"""Loguru-based logger with rotating file sink + rich console output."""

from __future__ import annotations

import sys

from loguru import logger as _logger

from core.settings import settings


def _configure() -> None:
    _logger.remove()

    _logger.add(
        sys.stderr,
        level=settings.log_level,
        colorize=True,
        format=(
            "<green>{time:HH:mm:ss}</green> "
            "<level>{level: <7}</level> "
            "<cyan>{name}:{line}</cyan> | "
            "<level>{message}</level>"
        ),
        backtrace=False,
        diagnose=False,
    )

    _logger.add(
        settings.logs_dir / "copilot.log",
        level="DEBUG",
        rotation="10 MB",
        retention="14 days",
        compression="zip",
        enqueue=True,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {name}:{function}:{line} | {message}",
    )

    _logger.add(
        settings.logs_dir / "signals.jsonl",
        level="INFO",
        rotation="50 MB",
        retention="60 days",
        serialize=True,
        filter=lambda r: r["extra"].get("event") == "signal",
    )


_configure()
logger = _logger
