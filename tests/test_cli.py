import pytest
from typer.testing import CliRunner

from bookhound import __version__
from bookhound.cli import app


@pytest.mark.revised
def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Discover, catalog, and selectively download PDFs by keyword." in result.stdout


@pytest.mark.revised
def test_cli_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert f"bookhound {__version__}" in result.stdout
