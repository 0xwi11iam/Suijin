"""
suijin/core/error_handler.py — Graceful error handling across the entire codebase.

Decorators and context managers that:
- Catch and log all exceptions without crashing the agent loop
- Classify errors for intelligent retry/fallback decisions
- Provide safe defaults when operations fail
"""
from __future__ import annotations

import functools
import logging
import traceback
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def safe_call(default_return=None, log_level: int = logging.WARNING):
    """Decorator: wrap any function with a try/except that returns a safe default.

    Usage:
        @safe_call(default_return=[])
        def might_fail(): ...
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.log(
                    log_level,
                    f"[safe_call] {func.__name__} failed: {e}",
                    exc_info=(log_level <= logging.DEBUG),
                )
                return default_return() if callable(default_return) else default_return
        return wrapper
    return decorator


def safe_async(default_return=None, log_level: int = logging.WARNING):
    """Async version of safe_call."""
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.log(
                    log_level,
                    f"[safe_async] {func.__name__} failed: {e}",
                    exc_info=(log_level <= logging.DEBUG),
                )
                return default_return() if callable(default_return) else default_return
        return wrapper
    return decorator


class GracefulFallback:
    """Context manager that catches exceptions and provides a fallback value.

    Usage:
        with GracefulFallback(default="safe_value") as result:
            result.value = risky_operation()
        # result.value is "safe_value" if risky_operation() raised
    """
    def __init__(self, default=None, log_level: int = logging.WARNING):
        self.default = default
        self.log_level = log_level
        self.value = None
        self.exception = None
        self.ok = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.ok = False
            self.exception = exc_val
            self.value = self.default() if callable(self.default) else self.default
            logger.log(
                self.log_level,
                f"[GracefulFallback] {exc_type.__name__}: {exc_val}",
            )
            return True  # Suppress exception
        return False


def classify_and_handle(error: Exception, context: str = "") -> dict:
    """Classify an error and return a structured response for the agent.

    Returns a dict suitable for injecting into agent messages.
    """
    error_type = type(error).__name__
    error_msg = str(error)[:300]

    classification = "unknown_error"
    guidance = "An unexpected error occurred. Try a different approach."

    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        if "refused" in error_msg.lower():
            classification = "connection_refused"
            guidance = "Connection refused. Target may be down or blocking requests. Verify target is accessible."
        elif "timeout" in error_msg.lower():
            classification = "timeout"
            guidance = "Request timed out. Target may be slow or unreachable. Increase timeout or try a lighter request."
        else:
            classification = "network_error"
            guidance = "Network error. Check connectivity and retry with backoff."

    elif isinstance(error, (ValueError, TypeError, KeyError, AttributeError)):
        classification = "data_error"
        guidance = f"Data format error: {error_msg[:100]}. Check the input format and retry."

    elif isinstance(error, ImportError):
        classification = "missing_dependency"
        guidance = f"Missing dependency: {error_msg[:100]}. Install the required package and retry."

    elif isinstance(error, PermissionError):
        classification = "permission_denied"
        guidance = "Permission denied. Check file/directory permissions."

    return {
        "error_type": error_type,
        "classification": classification,
        "message": error_msg,
        "guidance": guidance,
        "context": context,
        "traceback": traceback.format_exc()[-500:],
    }
