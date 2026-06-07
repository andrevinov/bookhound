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
    monkeypatch.delenv("BOOKHOUND_COMMON_CRAWL_RESULT_LIMIT", raising=False)
    monkeypatch.delenv("BOOKHOUND_SEED_CRAWLER_MAX_DEPTH", raising=False)
    monkeypatch.delenv("BOOKHOUND_SEED_CRAWLER_MAX_PAGES_PER_SEED", raising=False)
    monkeypatch.delenv("BOOKHOUND_LINK_EXPANSION_MAX_DEPTH", raising=False)
    monkeypatch.delenv("BOOKHOUND_LINK_EXPANSION_MAX_CANDIDATES", raising=False)

    settings = load_settings(project_root=tmp_path)

    assert isinstance(settings, AppSettings)
    assert settings.database_path == tmp_path / "bookhound.sqlite3"
    assert settings.pdf_directory == tmp_path / "pdfs"
    assert settings.user_agent == "Bookhound/0.1.0"
    assert settings.request_timeout_seconds == 30.0
    assert settings.global_rate_limit_per_second == 5.0
    assert settings.per_domain_rate_limit_per_second == 1.0
    assert settings.sources.common_crawl.enabled is True
    assert settings.sources.common_crawl.result_limit == 1000
    assert settings.sources.common_crawl.crawl_indexes == []
    assert settings.sources.seed_crawler.enabled is True
    assert settings.sources.seed_crawler.seed_urls == []
    assert settings.sources.seed_crawler.allowed_domains == []
    assert settings.sources.seed_crawler.same_domain_only is True
    assert settings.sources.seed_crawler.max_depth == 1
    assert settings.sources.seed_crawler.max_pages_per_seed == 50
    assert settings.sources.sitemap.enabled is True
    assert settings.sources.sitemap.request_timeout_seconds == settings.request_timeout_seconds
    assert settings.sources.sitemap.rate_limit_per_second == settings.per_domain_rate_limit_per_second
    assert settings.sources.link_expansion.enabled is True
    assert settings.sources.link_expansion.max_depth == 1
    assert settings.sources.link_expansion.max_candidates == 100
    assert settings.sources.link_expansion.same_domain_only is True


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
    monkeypatch.setenv("BOOKHOUND_COMMON_CRAWL_RESULT_LIMIT", "250")
    monkeypatch.setenv("BOOKHOUND_SEED_CRAWLER_MAX_DEPTH", "2")
    monkeypatch.setenv("BOOKHOUND_SEED_CRAWLER_MAX_PAGES_PER_SEED", "25")
    monkeypatch.setenv("BOOKHOUND_LINK_EXPANSION_MAX_DEPTH", "3")
    monkeypatch.setenv("BOOKHOUND_LINK_EXPANSION_MAX_CANDIDATES", "75")

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
    assert settings.sources.common_crawl.result_limit == 250
    assert settings.sources.seed_crawler.max_depth == 2
    assert settings.sources.seed_crawler.max_pages_per_seed == 25
    assert settings.sources.link_expansion.max_depth == 3
    assert settings.sources.link_expansion.max_candidates == 75


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

[sources.common_crawl]
enabled = false
result_limit = 500
crawl_indexes = ["CC-MAIN-2026-10"]

[sources.seed_crawler]
seed_urls = ["https://example.org/publications"]
allowed_domains = ["example.org"]
same_domain_only = true
max_depth = 2
max_pages_per_seed = 25

[sources.sitemap]
enabled = false

[sources.link_expansion]
max_depth = 3
max_candidates = 75
same_domain_only = false
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
    assert settings.sources.common_crawl.enabled is False
    assert settings.sources.common_crawl.result_limit == 500
    assert settings.sources.common_crawl.crawl_indexes == ["CC-MAIN-2026-10"]
    assert settings.sources.seed_crawler.seed_urls == ["https://example.org/publications"]
    assert settings.sources.seed_crawler.allowed_domains == ["example.org"]
    assert settings.sources.seed_crawler.same_domain_only is True
    assert settings.sources.seed_crawler.max_depth == 2
    assert settings.sources.seed_crawler.max_pages_per_seed == 25
    assert settings.sources.sitemap.enabled is False
    assert settings.sources.sitemap.request_timeout_seconds == settings.request_timeout_seconds
    assert settings.sources.sitemap.rate_limit_per_second == settings.per_domain_rate_limit_per_second
    assert settings.sources.link_expansion.max_depth == 3
    assert settings.sources.link_expansion.max_candidates == 75
    assert settings.sources.link_expansion.same_domain_only is False


@pytest.mark.revised
def test_missing_credentials_disable_paid_adapters_without_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BOOKHOUND_GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("BOOKHOUND_GOOGLE_SEARCH_ENGINE_ID", raising=False)
    monkeypatch.delenv("BOOKHOUND_UNPAYWALL_EMAIL", raising=False)

    settings = load_settings(project_root=tmp_path)

    assert settings.sources.google.enabled is False
    assert settings.sources.google.api_key is None
    assert settings.sources.google.search_engine_id is None
    assert settings.sources.unpaywall.enabled is False
    assert settings.sources.unpaywall.email is None
    assert settings.sources.common_crawl.enabled is True
    assert settings.sources.seed_crawler.enabled is True
    assert settings.sources.sitemap.enabled is True
    assert settings.sources.link_expansion.enabled is True


@pytest.mark.revised
def test_public_dump_excludes_secret_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BOOKHOUND_GOOGLE_API_KEY", "secret-google-key")
    monkeypatch.setenv("BOOKHOUND_GOOGLE_SEARCH_ENGINE_ID", "secret-search-engine")

    settings = load_settings(project_root=tmp_path)

    public_dump = settings.public_dump()

    assert public_dump["sources"]["google"]["enabled"] is True
    assert "api_key" not in public_dump["sources"]["google"]
    assert "search_engine_id" not in public_dump["sources"]["google"]
    assert "common_crawl" in public_dump["sources"]
    assert "seed_crawler" in public_dump["sources"]
    assert "sitemap" in public_dump["sources"]
    assert "link_expansion" in public_dump["sources"]
    assert "secret-google-key" not in repr(public_dump)
    assert "secret-search-engine" not in repr(public_dump)
