"""Logging utilities với structlog."""
import sys
import os
import io
import structlog
from pathlib import Path
from src.config import config


def _level_from_str(level_str: str) -> int:
    """Convert string level to int."""
    mapping = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
    return mapping.get(level_str.upper(), 20)


def _safe_serializer(msg: str, event_dict: dict) -> str:
    """JSON serialize an event dict, skipping fields that fail encoding."""
    import json
    safe_dict = {}
    for k, v in event_dict.items():
        try:
            json.dumps({k: v}, ensure_ascii=False)
            safe_dict[k] = v
        except (TypeError, UnicodeEncodeError):
            safe_dict[k] = repr(v)
    return json.dumps(safe_dict, ensure_ascii=False, default=str)


class SafeJSONRenderer:
    """Custom renderer that never crashes on Unicode."""
    def __call__(self, logger, method_name, event_dict):
        return _safe_serializer(method_name, event_dict)


def setup_logger(name: str = "text2sql") -> structlog.BoundLogger:
    """Thiết lập structlog cho toàn bộ hệ thống."""
    min_level = _level_from_str(config.LOG_LEVEL)

    # Fix Unicode on Windows: wrap stderr in UTF-8 TextIOWrapper
    if os.name == "nt":
        try:
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass

    log_file = config.LOG_DIR / f"{name}.log"

    # Write to both UTF-8 file and stdout (both safe now)
    try:
        _file_handle = open(log_file, "a", encoding="utf-8", errors="replace")
    except Exception:
        _file_handle = sys.stderr

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            SafeJSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(min_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=_file_handle),
        cache_logger_on_first_use=True,
    )

    return structlog.get_logger(name)
