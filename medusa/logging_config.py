"""
medusa/logging_config.py — Centralized logging configuration.

Replaces scattered print() calls with structured logging.
Import and call setup_logging() at startup.
"""
import logging, sys, os
from pathlib import Path

LOG_DIR = Path(os.environ.get("MEDUSA_LOG_DIR", "/tmp/medusa_logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

_log_initialized = False


def setup_logging(level: str = "INFO", log_file: str = None):
    """Configure root logger with console + file handlers."""
    global _log_initialized
    if _log_initialized:
        return
    _log_initialized = True

    logger = logging.getLogger("medusa")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)  # Only warnings+ to console
    console.setFormatter(logging.Formatter(
        "[%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(console)

    # File handler
    file_path = Path(log_file) if log_file else LOG_DIR / "medusa.log"
    file_handler = logging.FileHandler(file_path)
    file_handler.setLevel(logging.DEBUG)  # Everything to file
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d: %(message)s"
    ))
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str = None) -> logging.Logger:
    """Get a logger for a specific module."""
    return logging.getLogger(f"medusa.{name}" if name else "medusa")
