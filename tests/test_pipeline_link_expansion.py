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
def test_pipeline_calls_link_expansion_after_primary_sources() -> None:
    expander = FakeLinkExpander(
        [
            _candidate(
                title="Expanded PDF",
                url="https://example.org/report.pdf",
                source=SourceKind.LINK_EXPANSION,
                discovery_method=DiscoveryMethod.LINK_EXPANSION,
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
                    _candidate(
                        title="Landing Page",
                        url="https://example.org/report",
                        source=SourceKind.SITEMAP,
                        discovery_method=DiscoveryMethod.SITEMAP,
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
def test_expanded_candidates_are_included_in_pipeline_results() -> None:
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[
                    _candidate(
                        title="Landing Page",
                        url="https://example.org/report",
                        source=SourceKind.SITEMAP,
                        discovery_method=DiscoveryMethod.SITEMAP,
                        score=0.80,
                    )
                ],
            )
        ],
        link_expander=FakeLinkExpander(
            [
                _candidate(
                    title="Expanded PDF",
                    url="https://example.org/report.pdf",
                    source=SourceKind.LINK_EXPANSION,
                    discovery_method=DiscoveryMethod.LINK_EXPANSION,
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
) -> None:
    _clear_optional_source_credentials(monkeypatch)
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
) -> None:
    _clear_optional_source_credentials(monkeypatch)
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


def _candidate(
    *,
    title: str,
    url: str,
    source: SourceKind,
    discovery_method: DiscoveryMethod,
    score: float,
) -> RawCandidate:
    return RawCandidate(
        title=title,
        url=url,
        source=source,
        discovery_method=discovery_method,
        query="old query",
        score=score,
    )


def _clear_optional_source_credentials(monkeypatch) -> None:
    monkeypatch.delenv("BOOKHOUND_GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("BOOKHOUND_GOOGLE_SEARCH_ENGINE_ID", raising=False)
    monkeypatch.delenv("BOOKHOUND_UNPAYWALL_EMAIL", raising=False)
