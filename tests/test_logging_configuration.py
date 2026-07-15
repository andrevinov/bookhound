import io
import json
import logging
import sys
from pathlib import Path

import pytest

from bookhound.config import load_settings


LOGGING_ENV_VARS = (
    "BOOKHOUND_LOG_LEVEL",
    "BOOKHOUND_LOG_FORMAT",
    "BOOKHOUND_LOG_DESTINATION",
    "BOOKHOUND_LOG_FILE",
)


@pytest.fixture(autouse=True)
def restore_bookhound_logger():
    logger = logging.getLogger("bookhound")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    logger.handlers = []

    yield

    for handler in logger.handlers:
        handler.close()
    logger.handlers = original_handlers
    logger.setLevel(original_level)
    logger.propagate = original_propagate


@pytest.mark.revised
def test_logging_defaults_load_without_config_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_logging_env(monkeypatch)

    settings = load_settings(project_root=tmp_path)

    assert settings.logging.level == "WARNING"
    assert settings.logging.format == "text"
    assert settings.logging.destination == "stderr"
    assert settings.logging.file_path is None
    assert not (tmp_path / ".local").exists()
    assert not (tmp_path / "bookhound.log").exists()


@pytest.mark.revised
def test_logging_environment_variables_override_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_logging_env(monkeypatch)
    monkeypatch.setenv("BOOKHOUND_LOG_LEVEL", "info")
    monkeypatch.setenv("BOOKHOUND_LOG_FORMAT", "json")
    monkeypatch.setenv("BOOKHOUND_LOG_DESTINATION", "file")
    monkeypatch.setenv("BOOKHOUND_LOG_FILE", "logs/bookhound.jsonl")

    settings = load_settings(project_root=tmp_path)

    assert settings.logging.level == "INFO"
    assert settings.logging.format == "json"
    assert settings.logging.destination == "file"
    assert settings.logging.file_path == tmp_path / "logs" / "bookhound.jsonl"


@pytest.mark.revised
def test_logging_config_file_values_are_loaded(tmp_path: Path) -> None:
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[logging]
level = "DEBUG"
format = "json"
destination = "file"
file_path = "logs/bookhound.jsonl"
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path, project_root=tmp_path)

    assert settings.logging.level == "DEBUG"
    assert settings.logging.format == "json"
    assert settings.logging.destination == "file"
    assert settings.logging.file_path == tmp_path / "logs" / "bookhound.jsonl"


@pytest.mark.revised
def test_public_dump_includes_logging_without_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_logging_env(monkeypatch)
    monkeypatch.setenv("BOOKHOUND_GOOGLE_API_KEY", "secret-google-key")
    monkeypatch.setenv("BOOKHOUND_GOOGLE_SEARCH_ENGINE_ID", "secret-search-engine")
    monkeypatch.setenv("BOOKHOUND_LOG_LEVEL", "info")
    monkeypatch.setenv("BOOKHOUND_LOG_FORMAT", "json")
    monkeypatch.setenv("BOOKHOUND_LOG_DESTINATION", "file")
    monkeypatch.setenv("BOOKHOUND_LOG_FILE", "logs/bookhound.jsonl")

    settings = load_settings(project_root=tmp_path)

    public_dump = settings.public_dump()

    assert public_dump["logging"] == {
        "level": "INFO",
        "format": "json",
        "destination": "file",
        "file_path": str(tmp_path / "logs" / "bookhound.jsonl"),
    }
    assert "api_key" not in public_dump["sources"]["google"]
    assert "search_engine_id" not in public_dump["sources"]["google"]
    assert "secret-google-key" not in repr(public_dump)
    assert "secret-search-engine" not in repr(public_dump)


@pytest.mark.revised
def test_json_formatter_emits_lnav_friendly_json_line() -> None:
    from bookhound.logging_config import JsonLinesFormatter

    formatter = JsonLinesFormatter()
    record = _log_record(
        "Collection completed.",
        event="collect.completed",
        mode="collect",
        run_id="run-123",
        duration_ms=42,
    )

    line = formatter.format(record)

    assert "\n" not in line
    payload = json.loads(line)
    assert payload["timestamp"].endswith("Z")
    assert payload["level"] == "INFO"
    assert payload["logger"] == "bookhound.cli"
    assert payload["event"] == "collect.completed"
    assert payload["message"] == "Collection completed."
    assert payload["mode"] == "collect"
    assert payload["run_id"] == "run-123"
    assert payload["duration_ms"] == 42


@pytest.mark.revised
def test_json_formatter_sanitizes_sensitive_extras() -> None:
    from bookhound.logging_config import JsonLinesFormatter

    formatter = JsonLinesFormatter()
    record = _log_record(
        "HTTP request failed.",
        event="http.request.failed",
        mode="collect",
        run_id="run-123",
        url="https://example.org/report.pdf?api_key=secret#token",
        api_key="secret-api-key",
        authorization="Bearer secret-token",
        headers={
            "Authorization": "Bearer secret-token",
            "Accept": "application/pdf",
        },
        raw_body=b"%PDF-secret-body",
        result_count=2,
    )

    payload = json.loads(formatter.format(record))
    serialized_payload = json.dumps(payload, sort_keys=True)

    assert payload["url"] == "https://example.org/report.pdf"
    assert payload["result_count"] == 2
    assert "api_key" not in payload
    assert "authorization" not in payload
    assert "headers" not in payload
    assert "raw_body" not in payload
    assert "secret-api-key" not in serialized_payload
    assert "secret-token" not in serialized_payload
    assert "%PDF-secret-body" not in serialized_payload


@pytest.mark.revised
def test_configure_logging_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bookhound.logging_config import configure_logging

    _clear_logging_env(monkeypatch)
    monkeypatch.setenv("BOOKHOUND_LOG_LEVEL", "info")
    monkeypatch.setenv("BOOKHOUND_LOG_FORMAT", "json")
    monkeypatch.setenv("BOOKHOUND_LOG_DESTINATION", "stderr")
    settings = load_settings(project_root=tmp_path)
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)

    configure_logging(settings.logging)
    configure_logging(settings.logging)
    logging.getLogger("bookhound").info(
        "Idempotent logging.",
        extra={
            "event": "logging.idempotent",
            "mode": "test",
            "run_id": "run-123",
        },
    )

    lines = [line for line in stderr.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event"] == "logging.idempotent"
    assert payload["message"] == "Idempotent logging."


def _clear_logging_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_name in LOGGING_ENV_VARS:
        monkeypatch.delenv(env_name, raising=False)


def _log_record(
    message: str,
    **extra: object,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="bookhound.cli",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record
