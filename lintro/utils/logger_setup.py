"""Loguru logger configuration for Lintro.

Provides centralized logging setup for both CLI and tool execution contexts.
"""

import sys
from pathlib import Path


def setup_cli_logging() -> None:
    """Configure minimal logging for CLI commands (help, version, etc.).

    Only shows WARNING and ERROR level messages on console.
    No file logging - this is for lightweight CLI operations.
    """
    from loguru import logger

    logger.remove()
    logger.add(
        sys.stderr,
        level="WARNING",
        format="<level>{message}</level>",
        colorize=True,
    )


def setup_execution_logging(run_dir: Path, debug: bool = False) -> None:
    """Configure full logging for tool execution.

    Args:
        run_dir: Directory for log files.
        debug: If True, show DEBUG messages on console. Otherwise only WARNING+.
    """
    from loguru import logger

    logger.remove()

    # Console handler - DEBUG if flag set, else WARNING only
    console_level = "DEBUG" if debug else "WARNING"
    logger.add(
        sys.stderr,
        level=console_level,
        format="{message}",
        colorize=True,
    )

    # File handler with rotation (captures everything)
    run_dir.mkdir(parents=True, exist_ok=True)
    debug_log_path: Path = run_dir / "debug.log"
    logger.add(
        debug_log_path,
        level="DEBUG",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | "
            "{name}:{function}:{line} | {message}"
        ),
        rotation="100 MB",
        retention=5,
    )
