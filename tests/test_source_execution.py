# Consolidated test module. See docs/audit-tests/03-centralize-test-files.md.
# Consolidated from test_source_adapters.py

import pytest

from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind
from bookhound.sources import (
    DisabledSourceAdapter,
    FakeSourceAdapter,
    SourceAdapter,
    SourceAvailabilityError,
    SourceQuotaError,
    run_source_search,
)

@pytest.mark.revised
def test_fake_source_returns_normalized_candidates() -> None:
    adapter = FakeSourceAdapter(
        source=SourceKind.COMMON_CRAWL,
        discovery_method=DiscoveryMethod.PUBLIC_INDEX,
        candidates=[
            RawCandidate(
                title="Machine Learning Notes",
                url="https://example.org/notes.pdf",
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                query="machine learning",
                score=0.9,
            )
        ],
    )

    result = run_source_search(adapter, query="machine learning")

    assert isinstance(adapter, SourceAdapter)
    assert adapter.enabled is True
    assert adapter.source_name == SourceKind.COMMON_CRAWL
    assert adapter.rate_limit_key == "source:common_crawl"
    assert result.source is SourceKind.COMMON_CRAWL
    assert result.discovery_method is DiscoveryMethod.PUBLIC_INDEX
    assert result.errors == []
    assert len(result.candidates) == 1
    assert result.candidates[0].query == "machine learning"


@pytest.mark.revised
def test_fake_source_rewrites_candidate_query_for_current_search() -> None:
    adapter = FakeSourceAdapter(
        source=SourceKind.SITEMAP,
        discovery_method=DiscoveryMethod.SITEMAP,
        candidates=[
            RawCandidate(
                title="Old Search Result",
                url="https://example.org/old.pdf",
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                query="old query",
            )
        ],
    )

    result = run_source_search(adapter, query="new query")

    assert result.candidates[0].query == "new query"


@pytest.mark.revised
def test_disabled_adapter_does_not_run() -> None:
    adapter = DisabledSourceAdapter(
        source=SourceKind.GOOGLE,
        discovery_method=DiscoveryMethod.API,
        reason="Missing API key.",
    )

    result = run_source_search(adapter, query="machine learning")

    assert adapter.enabled is False
    assert result.source is SourceKind.GOOGLE
    assert result.discovery_method is DiscoveryMethod.API
    assert result.candidates == []
    assert result.errors == ["Source google is disabled: Missing API key."]


@pytest.mark.revised
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            SourceQuotaError(SourceKind.GOOGLE, "Daily quota exceeded."),
            "quota: Daily quota exceeded.",
        ),
        (
            SourceAvailabilityError(SourceKind.COMMON_CRAWL, "Index unavailable."),
            "availability: Index unavailable.",
        ),
    ],
)
def test_source_errors_are_represented_without_taking_down_pipeline(
    error,
    expected: str,
) -> None:
    adapter = FakeSourceAdapter(
        source=error.source,
        discovery_method=DiscoveryMethod.API,
        candidates=[],
        error=error,
    )

    result = run_source_search(adapter, query="machine learning")

    assert result.candidates == []
    assert result.errors == [expected]


@pytest.mark.revised
def test_source_search_propagates_adapter_operational_events() -> None:
    adapter = EventedSourceAdapter(
        source=SourceKind.SITEMAP,
        discovery_method=DiscoveryMethod.SITEMAP,
        events=[
            {
                "event_type": "sitemap.frontier_rejected",
                "message": "Rejected sitemap URL outside the configured frontier.",
                "metadata": {
                    "url": "https://outside.example/sitemap.xml",
                },
            }
        ],
    )

    result = run_source_search(adapter, query="frontier")

    assert result.candidates == []
    assert result.errors == []
    assert result.events == [
        {
            "source": "sitemap",
            "event_type": "sitemap.frontier_rejected",
            "message": "Rejected sitemap URL outside the configured frontier.",
            "metadata": {
                "url": "https://outside.example/sitemap.xml",
            },
        }
    ]


class EventedSourceAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        source: SourceKind,
        discovery_method: DiscoveryMethod,
        events: list[dict[str, object]],
    ) -> None:
        super().__init__(source=source, discovery_method=discovery_method)
        self._events = events
        self.events: list[dict[str, object]] = []

    def search(self, query: str) -> list[RawCandidate]:
        self.events = list(self._events)
        return []


# Consolidated from test_source_failure_boundary.py

import pytest

from bookhound.discovery_pipeline import DiscoveryPipeline
from bookhound.google_search import GoogleSearchAdapter, GoogleSearchAdapterConfig
from bookhound.http_client import HttpClientError, HttpTimeoutError
from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind
from bookhound.query_planner import QueryPlanner, QueryPlannerConfig
from bookhound.sitemap import SitemapAdapter, SitemapAdapterConfig
from bookhound.sources import SourceAdapter, run_source_search


class BuggyAdapter(SourceAdapter):
    def __init__(self) -> None:
        super().__init__(
            source=SourceKind.SITEMAP,
            discovery_method=DiscoveryMethod.SITEMAP,
        )

    def search(self, query: str) -> list[RawCandidate]:
        raise AssertionError("Internal source invariant failed.")


@pytest.mark.revised
def test_http_timeout_error_is_reported_without_escaping_source_runner(
    recording_http_client_factory,
) -> None:
    adapter = _google_adapter(
        recording_http_client_factory.raising(
            HttpTimeoutError("https://api.example.org/slow")
        )
    )

    result = run_source_search(adapter, query='"climate policy"')

    assert result.source is SourceKind.GOOGLE
    assert result.candidates == []
    _assert_single_availability_error(result.errors, "timed out")


@pytest.mark.revised
def test_connection_error_is_reported_without_escaping_source_runner(
    recording_http_client_factory,
) -> None:
    adapter = _google_adapter(
        recording_http_client_factory.raising(HttpClientError("Connection failed."))
    )

    result = run_source_search(adapter, query='"climate policy"')

    assert result.source is SourceKind.GOOGLE
    assert result.candidates == []
    _assert_single_availability_error(result.errors, "connection")


@pytest.mark.revised
def test_malformed_google_json_is_reported_as_source_error(
    recording_http_client_factory,
    http_response_factory,
) -> None:
    adapter = _google_adapter(
        recording_http_client_factory.single(
            http_response_factory(
                url="https://www.googleapis.com/customsearch/v1",
                content=b'{"items": [',
                content_type="application/json",
            )
        )
    )

    result = run_source_search(adapter, query='"climate policy"')

    assert result.source is SourceKind.GOOGLE
    assert result.candidates == []
    _assert_single_availability_error(result.errors, "json")


@pytest.mark.revised
def test_malformed_sitemap_xml_is_reported_as_source_error(
    recording_http_client_factory,
    text_response_factory,
    xml_response_factory,
) -> None:
    adapter = SitemapAdapter(
        http_client=recording_http_client_factory.from_mapping(
            {
                "https://example.org/robots.txt": text_response_factory(
                    url="https://example.org/robots.txt",
                    content=b"Sitemap: https://example.org/bad-sitemap.xml",
                ),
                "https://example.org/bad-sitemap.xml": xml_response_factory(
                    url="https://example.org/bad-sitemap.xml",
                    content=b"<urlset><url>",
                ),
            }
        ),
        config=SitemapAdapterConfig(domain_roots=["https://example.org/"]),
    )

    result = run_source_search(adapter, query="climate policy")

    assert result.source is SourceKind.SITEMAP
    assert result.candidates == []
    _assert_single_availability_error(result.errors, "xml")


@pytest.mark.revised
def test_pipeline_continues_after_real_adapter_failure(
    recording_http_client_factory,
    http_response_factory,
    text_response_factory,
    xml_response_factory,
    sitemap_urlset_xml_factory,
) -> None:
    failing_google = _google_adapter(
        recording_http_client_factory.single(
            http_response_factory(
                url="https://www.googleapis.com/customsearch/v1",
                content=b'{"items": [',
                content_type="application/json",
            )
        )
    )
    sitemap = SitemapAdapter(
        http_client=recording_http_client_factory.from_mapping(
            {
                "https://example.org/robots.txt": text_response_factory(
                    url="https://example.org/robots.txt",
                    content=b"Sitemap: https://example.org/sitemap.xml",
                ),
                "https://example.org/sitemap.xml": xml_response_factory(
                    url="https://example.org/sitemap.xml",
                    content=sitemap_urlset_xml_factory(
                        ["https://example.org/reports/resilience.pdf"]
                    ),
                ),
            }
        ),
        config=SitemapAdapterConfig(domain_roots=["https://example.org/"]),
    )
    pipeline = DiscoveryPipeline(
        sources=[failing_google, sitemap],
        query_planner=QueryPlanner(QueryPlannerConfig(max_variants=1)),
    )

    result = pipeline.search("resilience")

    assert [candidate.url for candidate in result.candidates] == [
        "https://example.org/reports/resilience.pdf"
    ]
    assert len(result.errors) == 1
    assert result.errors[0].startswith("google: availability:")
    assert "json" in result.errors[0].lower()


@pytest.mark.revised
def test_programmer_errors_are_not_converted_to_source_result() -> None:
    adapter = BuggyAdapter()

    with pytest.raises(AssertionError, match="Internal source invariant failed"):
        run_source_search(adapter, query="climate policy")


def _google_adapter(http_client: object) -> GoogleSearchAdapter:
    return GoogleSearchAdapter(
        http_client=http_client,
        config=GoogleSearchAdapterConfig(
            api_key="test-api-key",
            search_engine_id="test-search-engine",
            result_limit=1,
        ),
    )


def _assert_single_availability_error(errors: list[str], expected_term: str) -> None:
    assert len(errors) == 1
    assert errors[0].startswith("availability:")
    assert expected_term.lower() in errors[0].lower()


# Consolidated from test_discovery_pipeline.py

import pytest

from bookhound.discovery_pipeline import DiscoveryPipeline
from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind
from bookhound.query_planner import QueryPlanner, QueryPlannerConfig
from bookhound.sources import FakeSourceAdapter, SourceAvailabilityError


@pytest.mark.revised
def test_pipeline_returns_ordered_candidates_from_multiple_fake_sources(
    raw_candidate_factory,
) -> None:
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                source=SourceKind.GOOGLE,
                discovery_method=DiscoveryMethod.API,
                candidates=[
                    raw_candidate_factory(
                        title="Google Result",
                        url="https://google.example/report.pdf",
                        source=SourceKind.GOOGLE,
                        discovery_method=DiscoveryMethod.API,
                        query="old query",
                        score=0.80,
                    )
                ],
            ),
            FakeSourceAdapter(
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                candidates=[
                    raw_candidate_factory(
                        title="Common Crawl Result",
                        url="https://common.example/report.pdf",
                        source=SourceKind.COMMON_CRAWL,
                        discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                        query="old query",
                        score=0.80,
                    )
                ],
            ),
            FakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[
                    raw_candidate_factory(
                        title="Sitemap Result",
                        url="https://sitemap.example/report.pdf",
                        source=SourceKind.SITEMAP,
                        discovery_method=DiscoveryMethod.SITEMAP,
                        query="old query",
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
def test_one_failing_source_does_not_prevent_others_from_running(
    raw_candidate_factory,
) -> None:
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
                    raw_candidate_factory(
                        title="Sitemap Result",
                        url="https://example.org/report.pdf",
                        source=SourceKind.SITEMAP,
                        discovery_method=DiscoveryMethod.SITEMAP,
                        query="old query",
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
def test_duplicate_candidates_are_merged_by_canonical_url(
    raw_candidate_factory,
) -> None:
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                candidates=[
                    raw_candidate_factory(
                        title="Older Metadata",
                        url="https://Example.org/reports/paper.pdf?utm_source=newsletter#page=1",
                        source=SourceKind.COMMON_CRAWL,
                        discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                        query="old query",
                        score=0.60,
                    )
                ],
            ),
            FakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[
                    raw_candidate_factory(
                        title="Better Metadata",
                        url="https://example.org/reports/paper.pdf",
                        source=SourceKind.SITEMAP,
                        discovery_method=DiscoveryMethod.SITEMAP,
                        query="old query",
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
def test_result_includes_source_and_query_variant_used(
    raw_candidate_factory,
) -> None:
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[
                    raw_candidate_factory(
                        title="Quoted Variant Result",
                        url="https://example.org/quoted.pdf",
                        source=SourceKind.SITEMAP,
                        discovery_method=DiscoveryMethod.SITEMAP,
                        query="old query",
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


@pytest.mark.revised
def test_pipeline_preserves_operational_events_from_sources() -> None:
    pipeline = DiscoveryPipeline(
        sources=[
            EventedFakeSourceAdapter(
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                events=[
                    {
                        "event_type": "common_crawl.malformed_line",
                        "message": "Ignored malformed CDXJ line.",
                        "metadata": {
                            "crawl_index": "CC-MAIN-2026-25",
                            "line_number": 7,
                        },
                    }
                ],
            ),
            EventedFakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                events=[
                    {
                        "event_type": "sitemap.traversal_limit_reached",
                        "message": (
                            "Skipped sitemap URL because the sitemap file limit "
                            "was reached."
                        ),
                        "metadata": {
                            "url": "https://example.org/sitemap-101.xml",
                            "limit": 100,
                        },
                    }
                ],
            ),
        ],
        query_planner=QueryPlanner(QueryPlannerConfig(max_variants=1)),
    )

    result = pipeline.search("observability")

    assert result.candidates == []
    assert result.errors == []
    assert result.events == [
        {
            "source": "common_crawl",
            "event_type": "common_crawl.malformed_line",
            "message": "Ignored malformed CDXJ line.",
            "metadata": {
                "crawl_index": "CC-MAIN-2026-25",
                "line_number": 7,
            },
        },
        {
            "source": "sitemap",
            "event_type": "sitemap.traversal_limit_reached",
            "message": "Skipped sitemap URL because the sitemap file limit was reached.",
            "metadata": {
                "url": "https://example.org/sitemap-101.xml",
                "limit": 100,
            },
        },
    ]


@pytest.mark.revised
def test_pipeline_iter_search_emits_step_per_variant_and_source(
    raw_candidate_factory,
) -> None:
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                source=SourceKind.GOOGLE,
                discovery_method=DiscoveryMethod.API,
                candidates=[
                    raw_candidate_factory(
                        title="Google Result",
                        url="https://google.example/report.pdf",
                        source=SourceKind.GOOGLE,
                        discovery_method=DiscoveryMethod.API,
                        query="old query",
                        score=0.80,
                    )
                ],
            ),
            EventedFakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                events=[
                    {
                        "event_type": "sitemap.frontier_rejected",
                        "message": "Rejected sitemap URL outside the configured frontier.",
                        "metadata": {
                            "url": "https://outside.example/sitemap.xml",
                        },
                    }
                ],
            ),
        ],
        query_planner=QueryPlanner(QueryPlannerConfig(max_variants=1)),
    )

    steps = list(pipeline.iter_search("machine learning"))

    assert len(steps) == 2
    assert all(step.query_plan.keyword == "machine learning" for step in steps)
    assert [step.variant.label for step in steps] == ["quoted", "quoted"]
    assert [step.variant.query for step in steps] == [
        '"machine learning"',
        '"machine learning"',
    ]
    assert [step.source for step in steps] == [
        SourceKind.GOOGLE,
        SourceKind.SITEMAP,
    ]
    assert [step.discovery_method for step in steps] == [
        DiscoveryMethod.API,
        DiscoveryMethod.SITEMAP,
    ]
    assert [step.status for step in steps] == ["completed", "completed"]
    assert [candidate.url for candidate in steps[0].candidates] == [
        "https://google.example/report.pdf"
    ]
    assert steps[0].candidates[0].query == '"machine learning"'
    assert steps[0].errors == []
    assert steps[0].events == []
    assert steps[1].candidates == []
    assert steps[1].errors == []
    assert steps[1].events == [
        {
            "source": "sitemap",
            "event_type": "sitemap.frontier_rejected",
            "message": "Rejected sitemap URL outside the configured frontier.",
            "metadata": {
                "url": "https://outside.example/sitemap.xml",
            },
        }
    ]


@pytest.mark.revised
def test_pipeline_iter_search_emits_link_expansion_step_after_variant_sources(
    raw_candidate_factory,
) -> None:
    landing_candidate = raw_candidate_factory(
        title="Landing Page",
        url="https://example.org/reports/landing",
        source=SourceKind.SITEMAP,
        discovery_method=DiscoveryMethod.SITEMAP,
        query="old query",
        score=0.70,
    )
    expanded_candidate = raw_candidate_factory(
        title="Expanded PDF",
        url="https://example.org/reports/expanded.pdf",
        source=SourceKind.LINK_EXPANSION,
        discovery_method=DiscoveryMethod.LINK_EXPANSION,
        query='"machine learning"',
        score=0.85,
    )
    link_expander = RecordingLinkExpander([expanded_candidate])
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[landing_candidate],
            ),
            FakeSourceAdapter(
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                candidates=[],
            ),
        ],
        link_expander=link_expander,
        query_planner=QueryPlanner(QueryPlannerConfig(max_variants=1)),
    )

    steps = list(pipeline.iter_search("machine learning"))

    assert [step.source for step in steps] == [
        SourceKind.SITEMAP,
        SourceKind.COMMON_CRAWL,
        SourceKind.LINK_EXPANSION,
    ]
    assert [step.discovery_method for step in steps] == [
        DiscoveryMethod.SITEMAP,
        DiscoveryMethod.PUBLIC_INDEX,
        DiscoveryMethod.LINK_EXPANSION,
    ]
    assert [step.status for step in steps] == [
        "completed",
        "completed",
        "completed",
    ]
    assert link_expander.calls == [
        {
            "query": '"machine learning"',
            "candidate_urls": ["https://example.org/reports/landing"],
        }
    ]
    assert [candidate.url for candidate in steps[2].candidates] == [
        "https://example.org/reports/expanded.pdf"
    ]


@pytest.mark.revised
def test_pipeline_search_matches_incremental_steps_final_result(
    raw_candidate_factory,
) -> None:
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                candidates=[
                    raw_candidate_factory(
                        title="Older Metadata",
                        url="https://Example.org/reports/paper.pdf?utm_source=newsletter#page=1",
                        source=SourceKind.COMMON_CRAWL,
                        discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                        query="old query",
                        score=0.60,
                    )
                ],
            ),
            FakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[
                    raw_candidate_factory(
                        title="Better Metadata",
                        url="https://example.org/reports/paper.pdf",
                        source=SourceKind.SITEMAP,
                        discovery_method=DiscoveryMethod.SITEMAP,
                        query="old query",
                        score=0.90,
                    )
                ],
            ),
        ],
        query_planner=QueryPlanner(QueryPlannerConfig(max_variants=1)),
    )

    steps = list(pipeline.iter_search("metadata"))
    result = pipeline.search("metadata")

    assert [len(step.candidates) for step in steps] == [1, 1]
    assert [step.status for step in steps] == ["completed", "completed"]
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
def test_pipeline_iter_search_represents_recoverable_source_failure_as_failed_step(
    raw_candidate_factory,
) -> None:
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
                    raw_candidate_factory(
                        title="Sitemap Result",
                        url="https://example.org/report.pdf",
                        source=SourceKind.SITEMAP,
                        discovery_method=DiscoveryMethod.SITEMAP,
                        query="old query",
                        score=0.70,
                    )
                ],
            ),
        ],
        query_planner=QueryPlanner(QueryPlannerConfig(max_variants=1)),
    )

    steps = list(pipeline.iter_search("renewable energy"))

    assert len(steps) == 2
    failed_step, completed_step = steps
    assert failed_step.source is SourceKind.COMMON_CRAWL
    assert failed_step.discovery_method is DiscoveryMethod.PUBLIC_INDEX
    assert failed_step.status == "failed"
    assert failed_step.candidates == []
    assert failed_step.errors == ["availability: Index unavailable."]
    assert failed_step.events == []

    assert completed_step.source is SourceKind.SITEMAP
    assert completed_step.discovery_method is DiscoveryMethod.SITEMAP
    assert completed_step.status == "completed"
    assert [candidate.title for candidate in completed_step.candidates] == [
        "Sitemap Result"
    ]
    assert completed_step.errors == []


class EventedFakeSourceAdapter(FakeSourceAdapter):
    def __init__(
        self,
        *,
        source: SourceKind,
        discovery_method: DiscoveryMethod,
        events: list[dict[str, object]],
    ) -> None:
        super().__init__(
            source=source,
            discovery_method=discovery_method,
            candidates=[],
        )
        self._events = events
        self.events: list[dict[str, object]] = []

    def search(self, query: str) -> list[RawCandidate]:
        self.events = list(self._events)
        return super().search(query)


class RecordingLinkExpander:
    def __init__(self, candidates: list[RawCandidate]) -> None:
        self.candidates = candidates
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
                "candidate_urls": [
                    candidate.url for candidate in existing_candidates
                ],
            }
        )
        return self.candidates
