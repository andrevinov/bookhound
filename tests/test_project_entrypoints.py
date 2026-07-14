# Consolidated test module. See docs/audit-tests/03-centralize-test-files.md.
# Consolidated from test_package.py

import pytest
import bookhound

@pytest.mark.revised
def test_package_import_exposes_version() -> None:
    assert bookhound.__version__ == "0.1.0"


# Consolidated from test_cli.py

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
