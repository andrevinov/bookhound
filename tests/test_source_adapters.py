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
