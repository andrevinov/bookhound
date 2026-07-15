from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from bookhound.config import LoggingSettings


BOOKHOUND_LOGGER_NAME = "bookhound"
MANAGED_HANDLER_ATTRIBUTE = "_bookhound_managed"
SENSITIVE_EXTRA_KEYS = {
    "api_key",
    "authorization",
    "body",
    "content",
    "headers",
    "password",
    "raw_body",
    "secret",
    "token",
}
RESERVED_RECORD_ATTRIBUTES = set(
    logging.LogRecord(
        name="",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__
) | {"asctime", "message"}


class JsonLinesFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _format_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_record_extras(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", None)
        formatted_message = super().format(record)
        if event is None:
            return formatted_message
        return f"{formatted_message} event={event}"


def configure_logging(settings: LoggingSettings) -> None:
    logger = logging.getLogger(BOOKHOUND_LOGGER_NAME)
    _remove_managed_handlers(logger)

    handler = _build_handler(settings)
    setattr(handler, MANAGED_HANDLER_ATTRIBUTE, True)
    handler.setLevel(_log_level(settings.level))
    handler.setFormatter(_formatter(settings.format))

    logger.addHandler(handler)
    logger.setLevel(_log_level(settings.level))
    logger.propagate = False


def _build_handler(settings: LoggingSettings) -> logging.Handler:
    if settings.destination == "stderr":
        return logging.StreamHandler(sys.stderr)

    if settings.file_path is None:
        raise ValueError("A log file path is required when log destination is file.")

    settings.file_path.parent.mkdir(parents=True, exist_ok=True)
    return logging.FileHandler(settings.file_path, encoding="utf-8")


def _formatter(log_format: str) -> logging.Formatter:
    if log_format == "json":
        return JsonLinesFormatter()
    return TextFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )


def _remove_managed_handlers(logger: logging.Logger) -> None:
    remaining_handlers: list[logging.Handler] = []
    for handler in logger.handlers:
        if getattr(handler, MANAGED_HANDLER_ATTRIBUTE, False):
            logger.removeHandler(handler)
            handler.close()
            continue
        remaining_handlers.append(handler)
    logger.handlers = remaining_handlers


def _log_level(level: str) -> int:
    return int(logging.getLevelNamesMapping()[level])


def _record_extras(record: logging.LogRecord) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in RESERVED_RECORD_ATTRIBUTES or _is_sensitive_key(key):
            continue
        if key == "url":
            extras[key] = _sanitize_url(value)
            continue
        extras[key] = _json_safe(value)
    return extras


def _is_sensitive_key(key: str) -> bool:
    normalized_key = key.strip().lower()
    if normalized_key in SENSITIVE_EXTRA_KEYS:
        return True
    return any(term in normalized_key for term in ("secret", "token", "password"))


def _sanitize_url(value: Any) -> str:
    url = str(value)
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _format_timestamp(timestamp: float) -> str:
    return (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
