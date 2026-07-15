import json
from pathlib import Path

import pytest


LNAV_FORMAT_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "lnav"
    / "formats"
    / "installed"
    / "bookhound_log.json"
)


@pytest.mark.revised
def test_bookhound_lnav_format_declares_failure_diagnostic_fields() -> None:
    bookhound_format = _bookhound_lnav_format()
    values = bookhound_format["value"]

    assert values["error"]["kind"] == "string"
    assert values["error_type"]["kind"] == "string"
    assert values["exception"]["kind"] == "string"
    assert values["exception"]["hidden"] is True


@pytest.mark.revised
def test_bookhound_lnav_format_shows_error_summary_in_primary_line() -> None:
    bookhound_format = _bookhound_lnav_format()
    line_fields = _line_format_fields(bookhound_format["line-format"])

    assert "error" in line_fields

    error_field = _line_format_field(bookhound_format["line-format"], "error")
    assert error_field["default-value"] == ""
    assert error_field["overflow"] == "abbrev"
    assert error_field["max-width"] > 0


@pytest.mark.revised
def test_bookhound_lnav_format_declares_incremental_collect_fields() -> None:
    bookhound_format = _bookhound_lnav_format()
    values = bookhound_format["value"]
    line_fields = _line_format_fields(bookhound_format["line-format"])

    expected_fields = {
        "query_id": "integer",
        "step_id": "integer",
        "query_variant_label": "string",
        "source": "string",
        "candidate_count": "integer",
        "new": "integer",
        "updated": "integer",
        "duplicate": "integer",
        "error_count": "integer",
    }
    for field_name, expected_kind in expected_fields.items():
        assert values[field_name]["kind"] == expected_kind

    for field_name in (
        "source",
        "query_variant_label",
        "candidate_count",
        "error_count",
    ):
        assert field_name in line_fields


def _bookhound_lnav_format() -> dict[str, object]:
    payload = json.loads(LNAV_FORMAT_PATH.read_text(encoding="utf-8"))
    return payload["bookhound_log"]


def _line_format_fields(line_format: list[object]) -> list[str]:
    return [
        item["field"]
        for item in line_format
        if isinstance(item, dict) and "field" in item
    ]


def _line_format_field(line_format: list[object], field_name: str) -> dict[str, object]:
    for item in line_format:
        if isinstance(item, dict) and item.get("field") == field_name:
            return item
    raise AssertionError(f"Missing line-format field: {field_name}")
