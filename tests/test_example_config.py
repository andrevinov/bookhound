from pathlib import Path

from bookhound.config import load_settings


def test_example_config_loads_with_public_runtime_defaults(tmp_path: Path) -> None:
    config_path = Path("config/bookhound.example.toml")

    settings = load_settings(config_path=config_path, project_root=tmp_path)

    assert settings.database_path == tmp_path / "bookhound.sqlite3"
    assert settings.pdf_directory == tmp_path / "pdfs"
    assert settings.user_agent.startswith("Bookhound/0.1.0")
    assert settings.request_timeout_seconds == 30.0
    assert settings.global_rate_limit_per_second == 5.0
    assert settings.per_domain_rate_limit_per_second == 1.0
    assert settings.sources.google.enabled is False
    assert settings.sources.unpaywall.enabled is False
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
    assert settings.sources.link_expansion.enabled is True
    assert settings.sources.link_expansion.max_depth == 1
    assert settings.sources.link_expansion.max_candidates == 100
    assert settings.sources.link_expansion.same_domain_only is True
