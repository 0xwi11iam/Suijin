"""Structured error types for blue team operations.

Replace raw string error returns with typed error objects that the
supervisor and SOC can reason about programmatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ErrorSeverity(Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class BlueError:
    """Base error type for blue team operations."""
    message: str
    severity: ErrorSeverity = ErrorSeverity.ERROR
    source: str = "unknown"
    recoverable: bool = True
    detail: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "error": self.message,
            "severity": self.severity.value,
            "source": self.source,
            "recoverable": self.recoverable,
            "detail": self.detail,
        }

    def __str__(self) -> str:
        return f"[{self.severity.value.upper()}] {self.source}: {self.message}"


@dataclass
class FirewallError(BlueError):
    source: str = "firewall"

@dataclass
class DeceptionError(BlueError):
    source: str = "deception"

@dataclass
class AIEngineError(BlueError):
    source: str = "ai_engine"
    raw_response: Optional[str] = None

@dataclass
class ProxyError(BlueError):
    source: str = "proxy"

@dataclass
class PatchError(BlueError):
    source: str = "patch"
    file_path: Optional[str] = None


def ok(result=None) -> dict:
    """Return a success result."""
    return {"status": "ok", "result": result}


def err(error: BlueError) -> dict:
    """Return an error result."""
    return {"status": "error", "error": error.to_dict()}
