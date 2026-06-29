from pathlib import Path

import pytest

from bookhound.config import load_settings
import bookhound.cli as cli
from bookhound.models import SourceKind


@pytest.mark.revised
def test_build_search_pipeline_includes_default_public_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_optional_source_credentials(monkeypatch)
    settings = load_settings(project_root=tmp_path)

    pipeline = cli.build_search_pipeline(settings)

    assert _source_names(pipeline) == [
        SourceKind.ARXIV,
        SourceKind.COMMON_CRAWL,
    ]


@pytest.mark.revised
def test_build_search_pipeline_adds_seed_and_sitemap_sources_for_seed_urls(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[sources.seed_crawler]
seed_urls = ["https://example.org/library/"]
allowed_domains = ["partner.example.net"]
max_depth = 2

[sources.sitemap]
enabled = true
""".strip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path=config_path, project_root=tmp_path)

    pipeline = cli.build_search_pipeline(settings)

    assert _source_names(pipeline) == [
        SourceKind.ARXIV,
        SourceKind.COMMON_CRAWL,
        SourceKind.SEED_CRAWLER,
        SourceKind.SITEMAP,
    ]
    seed_crawler = _source_by_name(pipeline, SourceKind.SEED_CRAWLER)
    sitemap = _source_by_name(pipeline, SourceKind.SITEMAP)
    assert seed_crawler.config.seed_urls == ["https://example.org/library/"]
    assert seed_crawler.config.allowed_domains == ["partner.example.net"]
    assert seed_crawler.config.max_depth == 2
    assert sitemap.config.domain_roots == ["https://example.org/"]


@pytest.mark.revised
def test_build_search_pipeline_skips_google_without_credentials(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_optional_source_credentials(monkeypatch)
    settings = load_settings(project_root=tmp_path)

    pipeline = cli.build_search_pipeline(settings)

    assert SourceKind.GOOGLE not in _source_names(pipeline)


@pytest.mark.revised
def test_build_search_pipeline_includes_google_with_credentials(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[sources.google]
api_key = "google-api-key"
search_engine_id = "google-search-engine"
""".strip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path=config_path, project_root=tmp_path)

    pipeline = cli.build_search_pipeline(settings)

    google = _source_by_name(pipeline, SourceKind.GOOGLE)
    assert google.config.api_key == "google-api-key"
    assert google.config.search_engine_id == "google-search-engine"
    assert google.enabled is True


@pytest.mark.revised
def test_build_search_pipeline_reuses_the_runtime_http_client(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[http]
user_agent = "BookhoundPipelineTest/1.0"
request_timeout_seconds = 11

[rate_limits]
per_domain_rate_limit_per_second = 0.5

[sources.google]
api_key = "google-api-key"
search_engine_id = "google-search-engine"

[sources.seed_crawler]
seed_urls = ["https://example.org/library/"]
""".strip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path=config_path, project_root=tmp_path)

    pipeline = cli.build_search_pipeline(settings)

    http_clients = [source.http_client for source in pipeline.sources]
    assert len({id(http_client) for http_client in http_clients}) == 1
    assert http_clients[0].config.user_agent == "BookhoundPipelineTest/1.0"
    assert http_clients[0].config.timeout_seconds == 11.0
    assert http_clients[0].config.rate_limit_per_second == 0.5


def _source_names(pipeline) -> list[SourceKind]:
    return [source.source_name for source in pipeline.sources]


def _source_by_name(pipeline, source_name: SourceKind):
    for source in pipeline.sources:
        if source.source_name is source_name:
            return source
    raise AssertionError(f"Missing source: {source_name.value}")


def _clear_optional_source_credentials(monkeypatch) -> None:
    monkeypatch.delenv("BOOKHOUND_GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("BOOKHOUND_GOOGLE_SEARCH_ENGINE_ID", raising=False)
    monkeypatch.delenv("BOOKHOUND_UNPAYWALL_EMAIL", raising=False)
