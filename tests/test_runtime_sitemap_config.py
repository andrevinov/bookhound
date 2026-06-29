from pathlib import Path

import pytest

import bookhound.cli as cli
from bookhound.config import load_settings
from bookhound.models import SourceKind


@pytest.mark.revised
def test_sitemap_domain_roots_load_from_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[sources.sitemap]
enabled = true
domain_roots = [
  "https://example.org/",
  "https://archive.example.net/",
]
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path, project_root=tmp_path)

    assert settings.sources.sitemap.enabled is True
    assert settings.sources.sitemap.domain_roots == [
        "https://example.org/",
        "https://archive.example.net/",
    ]


@pytest.mark.revised
def test_pipeline_includes_sitemap_from_explicit_roots_without_seed_urls(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_optional_source_credentials(monkeypatch)
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[sources.sitemap]
enabled = true
domain_roots = ["https://example.org/"]
""".strip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path=config_path, project_root=tmp_path)

    pipeline = cli.build_search_pipeline(settings)

    assert _source_names(pipeline) == [
        SourceKind.ARXIV,
        SourceKind.COMMON_CRAWL,
        SourceKind.SITEMAP,
    ]
    sitemap = _source_by_name(pipeline, SourceKind.SITEMAP)
    assert sitemap.config.domain_roots == ["https://example.org/"]


@pytest.mark.revised
def test_explicit_and_seed_derived_sitemap_roots_are_combined(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_optional_source_credentials(monkeypatch)
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[sources.seed_crawler]
seed_urls = [
  "https://example.org/library/",
  "https://partner.example.net/reports/",
]

[sources.sitemap]
enabled = true
domain_roots = [
  "https://example.org/",
  "https://archive.example.net/",
]
""".strip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path=config_path, project_root=tmp_path)

    pipeline = cli.build_search_pipeline(settings)

    sitemap = _source_by_name(pipeline, SourceKind.SITEMAP)
    assert sitemap.config.domain_roots == [
        "https://example.org/",
        "https://archive.example.net/",
        "https://partner.example.net/",
    ]


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
