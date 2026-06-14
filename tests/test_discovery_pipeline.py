import pytest

from bookhound.discovery_pipeline import DiscoveryPipeline
from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind
from bookhound.query_planner import QueryPlanner, QueryPlannerConfig
from bookhound.sources import FakeSourceAdapter, SourceAvailabilityError


@pytest.mark.revised
def test_pipeline_returns_ordered_candidates_from_multiple_fake_sources() -> None:
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                source=SourceKind.GOOGLE,
                discovery_method=DiscoveryMethod.API,
                candidates=[
                    _candidate(
                        title="Google Result",
                        url="https://google.example/report.pdf",
                        source=SourceKind.GOOGLE,
                        discovery_method=DiscoveryMethod.API,
                        score=0.80,
                    )
                ],
            ),
            FakeSourceAdapter(
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                candidates=[
                    _candidate(
                        title="Common Crawl Result",
                        url="https://common.example/report.pdf",
                        source=SourceKind.COMMON_CRAWL,
                        discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                        score=0.80,
                    )
                ],
            ),
            FakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[
                    _candidate(
                        title="Sitemap Result",
                        url="https://sitemap.example/report.pdf",
                        source=SourceKind.SITEMAP,
                        discovery_method=DiscoveryMethod.SITEMAP,
                        score=0.95,
                    )
                ],
            ),
        ],
        query_planner=QueryPlanner(QueryPlannerConfig(max_variants=1)),
    )

    result = pipeline.search("machine learning")

    assert result.query_plan.keyword == "machine learning"
    assert [variant.query for variant in result.query_plan.variants] == ['"machine learning"']
    assert [(candidate.title, candidate.source) for candidate in result.candidates] == [
        ("Sitemap Result", SourceKind.SITEMAP),
        ("Common Crawl Result", SourceKind.COMMON_CRAWL),
        ("Google Result", SourceKind.GOOGLE),
    ]
    assert result.errors == []


@pytest.mark.revised
def test_one_failing_source_does_not_prevent_others_from_running() -> None:
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                candidates=[],
                error=SourceAvailabilityError(
                    SourceKind.COMMON_CRAWL,
                    "Index unavailable.",
                ),
            ),
            FakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[
                    _candidate(
                        title="Sitemap Result",
                        url="https://example.org/report.pdf",
                        source=SourceKind.SITEMAP,
                        discovery_method=DiscoveryMethod.SITEMAP,
                        score=0.70,
                    )
                ],
            ),
        ],
        query_planner=QueryPlanner(QueryPlannerConfig(max_variants=1)),
    )

    result = pipeline.search("renewable energy")

    assert [candidate.title for candidate in result.candidates] == ["Sitemap Result"]
    assert result.errors == ["common_crawl: availability: Index unavailable."]


@pytest.mark.revised
def test_duplicate_candidates_are_merged_by_canonical_url() -> None:
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                candidates=[
                    _candidate(
                        title="Older Metadata",
                        url="https://Example.org/reports/paper.pdf?utm_source=newsletter#page=1",
                        source=SourceKind.COMMON_CRAWL,
                        discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                        score=0.60,
                    )
                ],
            ),
            FakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[
                    _candidate(
                        title="Better Metadata",
                        url="https://example.org/reports/paper.pdf",
                        source=SourceKind.SITEMAP,
                        discovery_method=DiscoveryMethod.SITEMAP,
                        score=0.90,
                    )
                ],
            ),
        ],
        query_planner=QueryPlanner(QueryPlannerConfig(max_variants=1)),
    )

    result = pipeline.search("metadata")

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.title == "Better Metadata"
    assert candidate.score == 0.90
    assert candidate.metadata["canonical_url"] == "https://example.org/reports/paper.pdf"
    assert candidate.metadata["merged_count"] == 2
    assert candidate.metadata["source_occurrences"] == [
        {
            "source": "common_crawl",
            "discovery_method": "public_index",
            "query_variant_label": "quoted",
            "query": '"metadata"',
        },
        {
            "source": "sitemap",
            "discovery_method": "sitemap",
            "query_variant_label": "quoted",
            "query": '"metadata"',
        },
    ]


@pytest.mark.revised
def test_result_includes_source_and_query_variant_used() -> None:
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[
                    _candidate(
                        title="Quoted Variant Result",
                        url="https://example.org/quoted.pdf",
                        source=SourceKind.SITEMAP,
                        discovery_method=DiscoveryMethod.SITEMAP,
                        score=0.90,
                    )
                ],
            )
        ],
        query_planner=QueryPlanner(QueryPlannerConfig(max_variants=1)),
    )

    result = pipeline.search("statistics")

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.source is SourceKind.SITEMAP
    assert candidate.discovery_method is DiscoveryMethod.SITEMAP
    assert candidate.query == '"statistics"'
    assert candidate.metadata["query_variant_label"] == "quoted"
    assert candidate.metadata["source_occurrences"] == [
        {
            "source": "sitemap",
            "discovery_method": "sitemap",
            "query_variant_label": "quoted",
            "query": '"statistics"',
        }
    ]


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
