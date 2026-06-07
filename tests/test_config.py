import pytest
from pathlib import Path

from bookhound.config import AppSettings, load_settings

@pytest.mark.revised
def test_defaults_load_without_config_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BOOKHOUND_DATABASE_PATH", raising=False)
    monkeypatch.delenv("BOOKHOUND_PDF_DIRECTORY", raising=False)
    monkeypatch.delenv("BOOKHOUND_USER_AGENT", raising=False)
    monkeypatch.delenv("BOOKHOUND_REQUEST_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("BOOKHOUND_GLOBAL_RATE_LIMIT_PER_SECOND", raising=False)
    monkeypatch.delenv("BOOKHOUND_PER_DOMAIN_RATE_LIMIT_PER_SECOND", raising=False)

    settings = load_settings(project_root=tmp_path)

    assert isinstance(settings, AppSettings)
    assert settings.database_path == tmp_path / "bookhound.sqlite3"
    assert settings.pdf_directory == tmp_path / "pdfs"
    assert settings.user_agent == "Bookhound/0.1.0"
    assert settings.request_timeout_seconds == 30.0
    assert settings.global_rate_limit_per_second == 5.0
    assert settings.per_domain_rate_limit_per_second == 1.0


@pytest.mark.revised
def test_environment_variables_override_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BOOKHOUND_DATABASE_PATH", str(tmp_path / "state" / "custom.sqlite3"))
    monkeypatch.setenv("BOOKHOUND_PDF_DIRECTORY", str(tmp_path / "library"))
    monkeypatch.setenv("BOOKHOUND_USER_AGENT", "BookhoundTest/1.0")
    monkeypatch.setenv("BOOKHOUND_REQUEST_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("BOOKHOUND_GLOBAL_RATE_LIMIT_PER_SECOND", "2.5")
    monkeypatch.setenv("BOOKHOUND_PER_DOMAIN_RATE_LIMIT_PER_SECOND", "0.5")
    monkeypatch.setenv("BOOKHOUND_GOOGLE_API_KEY", "env-google-key")
    monkeypatch.setenv("BOOKHOUND_GOOGLE_SEARCH_ENGINE_ID", "env-search-engine")
    monkeypatch.setenv("BOOKHOUND_UNPAYWALL_EMAIL", "researcher@example.org")

    settings = load_settings(project_root=tmp_path)

    assert settings.database_path == tmp_path / "state" / "custom.sqlite3"
    assert settings.pdf_directory == tmp_path / "library"
    assert settings.user_agent == "BookhoundTest/1.0"
    assert settings.request_timeout_seconds == 12.5
    assert settings.global_rate_limit_per_second == 2.5
    assert settings.per_domain_rate_limit_per_second == 0.5
    assert settings.sources.google.enabled is True
    assert settings.sources.google.api_key.get_secret_value() == "env-google-key"
    assert settings.sources.google.search_engine_id.get_secret_value() == "env-search-engine"
    assert settings.sources.unpaywall.enabled is True
    assert settings.sources.unpaywall.email == "researcher@example.org"


@pytest.mark.revised
def test_relative_paths_from_config_file_are_resolved_against_project_root(tmp_path: Path) -> None:
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[paths]
database_path = "state/bookhound.sqlite3"
pdf_directory = "library/pdfs"

[http]
user_agent = "BookhoundLocal/1.0"
request_timeout_seconds = 20

[rate_limits]
global_rate_limit_per_second = 3
per_domain_rate_limit_per_second = 0.75
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path, project_root=tmp_path)

    assert settings.database_path == tmp_path / "state" / "bookhound.sqlite3"
    assert settings.pdf_directory == tmp_path / "library" / "pdfs"
    assert settings.user_agent == "BookhoundLocal/1.0"
    assert settings.request_timeout_seconds == 20.0
    assert settings.global_rate_limit_per_second == 3.0
    assert settings.per_domain_rate_limit_per_second == 0.75


@pytest.mark.revised
def test_missing_credentials_disable_paid_adapters_without_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BOOKHOUND_GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("BOOKHOUND_GOOGLE_SEARCH_ENGINE_ID", raising=False)
    monkeypatch.delenv("BOOKHOUND_BING_API_KEY", raising=False)
    monkeypatch.delenv("BOOKHOUND_UNPAYWALL_EMAIL", raising=False)

    settings = load_settings(project_root=tmp_path)

    assert settings.sources.google.enabled is False
    assert settings.sources.google.api_key is None
    assert settings.sources.google.search_engine_id is None
    assert settings.sources.bing.enabled is False
    assert settings.sources.bing.api_key is None
    assert settings.sources.unpaywall.enabled is False
    assert settings.sources.unpaywall.email is None


@pytest.mark.revised
def test_public_dump_excludes_secret_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BOOKHOUND_GOOGLE_API_KEY", "secret-google-key")
    monkeypatch.setenv("BOOKHOUND_GOOGLE_SEARCH_ENGINE_ID", "secret-search-engine")
    monkeypatch.setenv("BOOKHOUND_BING_API_KEY", "secret-bing-key")

    settings = load_settings(project_root=tmp_path)

    public_dump = settings.public_dump()

    assert public_dump["sources"]["google"]["enabled"] is True
    assert public_dump["sources"]["bing"]["enabled"] is True
    assert "api_key" not in public_dump["sources"]["google"]
    assert "search_engine_id" not in public_dump["sources"]["google"]
    assert "api_key" not in public_dump["sources"]["bing"]
    assert "secret-google-key" not in repr(public_dump)
    assert "secret-search-engine" not in repr(public_dump)
    assert "secret-bing-key" not in repr(public_dump)
