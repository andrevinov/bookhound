# Consolidated test module. See docs/audit-tests/03-centralize-test-files.md.
# Consolidated from test_runtime_arxiv_config.py

from pathlib import Path

import pytest

import bookhound.cli as cli
from bookhound.config import load_settings
from bookhound.models import SourceKind


@pytest.mark.revised
def test_arxiv_settings_load_from_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[sources.arxiv]
enabled = true
max_results = 40
page_size = 8
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path, project_root=tmp_path)

    assert settings.sources.arxiv.enabled is True
    assert settings.sources.arxiv.max_results == 40
    assert settings.sources.arxiv.page_size == 8


@pytest.mark.revised
def test_build_search_pipeline_can_disable_arxiv(
    tmp_path: Path,
    monkeypatch,
    clear_optional_source_credentials_helper,
    source_names_helper,
) -> None:
    clear_optional_source_credentials_helper(monkeypatch)
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[sources.arxiv]
enabled = false
""".strip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path=config_path, project_root=tmp_path)

    pipeline = cli.build_search_pipeline(settings)

    assert SourceKind.ARXIV not in source_names_helper(pipeline)


@pytest.mark.revised
def test_build_search_pipeline_passes_arxiv_limits_to_adapter(
    tmp_path: Path,
    monkeypatch,
    clear_optional_source_credentials_helper,
    source_by_name_helper,
) -> None:
    clear_optional_source_credentials_helper(monkeypatch)
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[sources.arxiv]
enabled = true
max_results = 25
page_size = 5
""".strip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path=config_path, project_root=tmp_path)

    pipeline = cli.build_search_pipeline(settings)

    arxiv = source_by_name_helper(pipeline, SourceKind.ARXIV)
    assert arxiv.config.max_results == 25
    assert arxiv.config.page_size == 5


# Consolidated from test_runtime_sitemap_config.py

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
    clear_optional_source_credentials_helper,
    source_names_helper,
    source_by_name_helper,
) -> None:
    clear_optional_source_credentials_helper(monkeypatch)
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

    assert source_names_helper(pipeline) == [
        SourceKind.ARXIV,
        SourceKind.COMMON_CRAWL,
        SourceKind.SITEMAP,
    ]
    sitemap = source_by_name_helper(pipeline, SourceKind.SITEMAP)
    assert sitemap.config.domain_roots == ["https://example.org/"]


@pytest.mark.revised
def test_explicit_and_seed_derived_sitemap_roots_are_combined(
    tmp_path: Path,
    monkeypatch,
    clear_optional_source_credentials_helper,
    source_by_name_helper,
) -> None:
    clear_optional_source_credentials_helper(monkeypatch)
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

    sitemap = source_by_name_helper(pipeline, SourceKind.SITEMAP)
    assert sitemap.config.domain_roots == [
        "https://example.org/",
        "https://archive.example.net/",
        "https://partner.example.net/",
    ]


# Consolidated from test_runtime_search_pipeline.py

from pathlib import Path

import pytest

from bookhound import app_factory
from bookhound.config import load_settings
import bookhound.cli as cli
from bookhound.models import SourceKind


@pytest.mark.revised
def test_build_search_pipeline_includes_default_public_sources(
    tmp_path: Path,
    monkeypatch,
    clear_optional_source_credentials_helper,
    source_names_helper,
) -> None:
    clear_optional_source_credentials_helper(monkeypatch)
    settings = load_settings(project_root=tmp_path)

    pipeline = cli.build_search_pipeline(settings)

    assert source_names_helper(pipeline) == [
        SourceKind.ARXIV,
        SourceKind.COMMON_CRAWL,
    ]


@pytest.mark.revised
def test_build_search_pipeline_adds_seed_and_sitemap_sources_for_seed_urls(
    tmp_path: Path,
    source_names_helper,
    source_by_name_helper,
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

    assert source_names_helper(pipeline) == [
        SourceKind.ARXIV,
        SourceKind.COMMON_CRAWL,
        SourceKind.SEED_CRAWLER,
        SourceKind.SITEMAP,
    ]
    seed_crawler = source_by_name_helper(pipeline, SourceKind.SEED_CRAWLER)
    sitemap = source_by_name_helper(pipeline, SourceKind.SITEMAP)
    assert seed_crawler.config.seed_urls == ["https://example.org/library/"]
    assert seed_crawler.config.allowed_domains == ["partner.example.net"]
    assert seed_crawler.config.max_depth == 2
    assert sitemap.config.domain_roots == ["https://example.org/"]


@pytest.mark.revised
def test_build_search_pipeline_skips_google_without_credentials(
    tmp_path: Path,
    monkeypatch,
    clear_optional_source_credentials_helper,
    source_names_helper,
) -> None:
    clear_optional_source_credentials_helper(monkeypatch)
    settings = load_settings(project_root=tmp_path)

    pipeline = cli.build_search_pipeline(settings)

    assert SourceKind.GOOGLE not in source_names_helper(pipeline)


@pytest.mark.revised
def test_build_search_pipeline_includes_google_with_credentials(
    tmp_path: Path,
    source_by_name_helper,
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

    google = source_by_name_helper(pipeline, SourceKind.GOOGLE)
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


@pytest.mark.revised
def test_app_factory_search_pipeline_uses_injected_http_client(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[sources.seed_crawler]
seed_urls = ["https://example.org/library/"]
""".strip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path=config_path, project_root=tmp_path)
    http_client = object()

    pipeline = app_factory.build_search_pipeline(
        settings,
        http_client=http_client,
    )

    assert all(source.http_client is http_client for source in pipeline.sources)
    assert pipeline.link_expander.http_client is http_client


# Consolidated from test_runtime_seed_crawler_robots.py

from pathlib import Path

import pytest

from bookhound import app_factory
from bookhound.config import load_settings
from bookhound.models import SourceKind


@pytest.mark.revised
def test_runtime_seed_crawler_enforces_robots_disallow_and_records_event(
    tmp_path: Path,
    recording_http_client_factory,
    text_response_factory,
    html_response_factory,
    source_by_name_helper,
) -> None:
    settings = _settings(
        tmp_path,
        seed_url="https://example.org/seed/",
        max_depth=0,
    )
    http_client = recording_http_client_factory.from_mapping(
        {
            "https://example.org/robots.txt": text_response_factory(
                url="https://example.org/robots.txt",
                content=b"""
User-agent: BookhoundRobotsTest
Disallow: /private/

User-agent: *
Allow: /
""",
            ),
            "https://example.org/seed/": html_response_factory(
                url="https://example.org/seed/",
                content=b"""
<a href="/private/blocked.pdf">Blocked PDF</a>
<a href="/reports/open.pdf">Open PDF</a>
""",
            ),
        }
    )
    pipeline = app_factory.build_search_pipeline(settings, http_client=http_client)
    seed_crawler = source_by_name_helper(pipeline, SourceKind.SEED_CRAWLER)

    result = pipeline.search("robots")

    assert [candidate.url for candidate in result.candidates] == [
        "https://example.org/reports/open.pdf"
    ]
    assert "https://example.org/private/blocked.pdf" not in http_client.urls
    assert http_client.urls[:2] == [
        "https://example.org/robots.txt",
        "https://example.org/seed/",
    ]
    assert seed_crawler.events == [
        {
            "event_type": "seed_crawler.robots_disallowed",
            "message": "Skipped URL disallowed by robots policy.",
            "metadata": {
                "url": "https://example.org/private/blocked.pdf",
                "seed_url": "https://example.org/seed/",
            },
        }
    ]


@pytest.mark.revised
def test_runtime_seed_crawler_caches_robots_policy_per_domain(
    tmp_path: Path,
    recording_http_client_factory,
    text_response_factory,
    html_response_factory,
) -> None:
    settings = _settings(
        tmp_path,
        seed_url="https://example.org/seed/",
        max_depth=0,
    )
    http_client = recording_http_client_factory.from_mapping(
        {
            "https://example.org/robots.txt": text_response_factory(
                url="https://example.org/robots.txt",
                content=b"""
User-agent: *
Allow: /
""",
            ),
            "https://example.org/seed/": html_response_factory(
                url="https://example.org/seed/",
                content=b"""
<a href="/reports/first.pdf">First PDF</a>
<a href="/reports/second.pdf">Second PDF</a>
""",
            ),
        }
    )
    pipeline = app_factory.build_search_pipeline(settings, http_client=http_client)

    result = pipeline.search("robots cache")

    assert [candidate.url for candidate in result.candidates] == [
        "https://example.org/reports/first.pdf",
        "https://example.org/reports/second.pdf",
    ]
    assert http_client.urls.count("https://example.org/robots.txt") == 1


def _settings(
    tmp_path: Path,
    *,
    seed_url: str,
    max_depth: int,
):
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        f"""
[http]
user_agent = "BookhoundRobotsTest"

[sources.arxiv]
enabled = false

[sources.common_crawl]
enabled = false

[sources.seed_crawler]
seed_urls = ["{seed_url}"]
max_depth = {max_depth}

[sources.sitemap]
enabled = false

[sources.link_expansion]
enabled = false
""".strip(),
        encoding="utf-8",
    )
    return load_settings(config_path=config_path, project_root=tmp_path)


# Consolidated from test_pipeline_link_expansion.py

from pathlib import Path

import pytest

import bookhound.cli as cli
from bookhound.config import load_settings
from bookhound.discovery_pipeline import DiscoveryPipeline
from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind
from bookhound.query_planner import QueryPlanner, QueryPlannerConfig
from bookhound.sources import FakeSourceAdapter


class FakeLinkExpander:
    def __init__(self, expanded_candidates: list[RawCandidate]) -> None:
        self.expanded_candidates = expanded_candidates
        self.calls: list[dict[str, object]] = []

    def expand(
        self,
        existing_candidates: list[RawCandidate],
        *,
        query: str,
    ) -> list[RawCandidate]:
        self.calls.append(
            {
                "query": query,
                "urls": [candidate.url for candidate in existing_candidates],
            }
        )
        return self.expanded_candidates


@pytest.mark.revised
def test_pipeline_calls_link_expansion_after_primary_sources(
    raw_candidate_factory,
) -> None:
    expander = FakeLinkExpander(
        [
            raw_candidate_factory(
                title="Expanded PDF",
                url="https://example.org/report.pdf",
                source=SourceKind.LINK_EXPANSION,
                discovery_method=DiscoveryMethod.LINK_EXPANSION,
                query="old query",
                score=0.70,
            )
        ]
    )
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[
                    raw_candidate_factory(
                        title="Landing Page",
                        url="https://example.org/report",
                        source=SourceKind.SITEMAP,
                        discovery_method=DiscoveryMethod.SITEMAP,
                        query="old query",
                        score=0.80,
                    )
                ],
            )
        ],
        link_expander=expander,
        query_planner=QueryPlanner(QueryPlannerConfig(max_variants=1)),
    )

    pipeline.search("climate policy")

    assert expander.calls == [
        {
            "query": '"climate policy"',
            "urls": ["https://example.org/report"],
        }
    ]


@pytest.mark.revised
def test_expanded_candidates_are_included_in_pipeline_results(
    raw_candidate_factory,
) -> None:
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[
                    raw_candidate_factory(
                        title="Landing Page",
                        url="https://example.org/report",
                        source=SourceKind.SITEMAP,
                        discovery_method=DiscoveryMethod.SITEMAP,
                        query="old query",
                        score=0.80,
                    )
                ],
            )
        ],
        link_expander=FakeLinkExpander(
            [
                raw_candidate_factory(
                    title="Expanded PDF",
                    url="https://example.org/report.pdf",
                    source=SourceKind.LINK_EXPANSION,
                    discovery_method=DiscoveryMethod.LINK_EXPANSION,
                    query="old query",
                    score=0.70,
                )
            ]
        ),
        query_planner=QueryPlanner(QueryPlannerConfig(max_variants=1)),
    )

    result = pipeline.search("climate policy")

    assert [(candidate.title, candidate.source) for candidate in result.candidates] == [
        ("Landing Page", SourceKind.SITEMAP),
        ("Expanded PDF", SourceKind.LINK_EXPANSION),
    ]
    expanded = result.candidates[1]
    assert expanded.metadata["canonical_url"] == "https://example.org/report.pdf"
    assert expanded.metadata["source_occurrences"] == [
        {
            "source": "link_expansion",
            "discovery_method": "link_expansion",
            "query_variant_label": "quoted",
            "query": "old query",
        }
    ]


@pytest.mark.revised
def test_runtime_pipeline_can_disable_link_expansion(
    tmp_path: Path,
    monkeypatch,
    clear_optional_source_credentials_helper,
) -> None:
    clear_optional_source_credentials_helper(monkeypatch)
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[sources.link_expansion]
enabled = false
""".strip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path=config_path, project_root=tmp_path)

    pipeline = cli.build_search_pipeline(settings)

    assert pipeline.link_expander is None


@pytest.mark.revised
def test_runtime_pipeline_configures_link_expansion_from_settings(
    tmp_path: Path,
    monkeypatch,
    clear_optional_source_credentials_helper,
) -> None:
    clear_optional_source_credentials_helper(monkeypatch)
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[sources.link_expansion]
enabled = true
max_depth = 3
max_candidates = 25
same_domain_only = false
""".strip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path=config_path, project_root=tmp_path)

    pipeline = cli.build_search_pipeline(settings)

    assert pipeline.link_expander is not None
    assert pipeline.link_expander.config.max_depth == 3
    assert pipeline.link_expander.config.max_candidates == 25
    assert pipeline.link_expander.config.same_domain_only is False
