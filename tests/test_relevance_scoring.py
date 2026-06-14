import pytest

from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind
from bookhound.relevance_scoring import RelevanceScorer, RelevanceScoringConfig


@pytest.mark.revised
def test_higher_adapter_score_improves_final_score() -> None:
    scorer = RelevanceScorer()
    low_score = scorer.score(
        _candidate(
            title="Machine Learning Notes",
            url="https://example.org/notes.pdf",
            adapter_score=0.20,
        ),
        keyword="machine learning",
    )
    high_score = scorer.score(
        _candidate(
            title="Machine Learning Notes",
            url="https://example.org/notes.pdf",
            adapter_score=0.80,
        ),
        keyword="machine learning",
    )

    assert high_score.score > low_score.score


@pytest.mark.revised
def test_direct_pdf_url_scores_higher_than_weak_landing_page_candidate() -> None:
    scorer = RelevanceScorer()
    landing_page = scorer.score(
        _candidate(
            title="Course Materials",
            url="https://example.org/materials",
            adapter_score=0.50,
        ),
        keyword="machine learning",
    )
    direct_pdf = scorer.score(
        _candidate(
            title="Course Materials",
            url="https://example.org/materials.pdf",
            adapter_score=0.50,
        ),
        keyword="machine learning",
    )

    assert direct_pdf.score > landing_page.score


@pytest.mark.revised
def test_title_and_snippet_keyword_matches_improve_relevance() -> None:
    scorer = RelevanceScorer()
    neutral = scorer.score(
        _candidate(
            title="Course Materials",
            url="https://example.org/materials",
            snippet="Lecture notes and bibliography.",
            adapter_score=0.40,
        ),
        keyword="machine learning",
    )
    title_match = scorer.score(
        _candidate(
            title="Machine Learning Materials",
            url="https://example.org/title-match",
            snippet="Lecture notes and bibliography.",
            adapter_score=0.40,
        ),
        keyword="machine learning",
    )
    snippet_match = scorer.score(
        _candidate(
            title="Course Materials",
            url="https://example.org/snippet-match",
            snippet="Lecture notes about machine learning.",
            adapter_score=0.40,
        ),
        keyword="machine learning",
    )

    assert title_match.score > neutral.score
    assert snippet_match.score > neutral.score


@pytest.mark.revised
@pytest.mark.parametrize(
    "metadata",
    [
        {"doi": "10.1234/example"},
        {"isbn": "978-0-123456-47-2"},
        {"year": 2024},
    ],
)
def test_metadata_completeness_boosts_relevance(metadata: dict[str, object]) -> None:
    scorer = RelevanceScorer()
    sparse = scorer.score(
        _candidate(
            title="Climate Policy Report",
            url="https://example.org/report.pdf",
            adapter_score=0.50,
        ),
        keyword="climate policy",
    )
    enriched = scorer.score(
        _candidate(
            title="Climate Policy Report",
            url="https://example.org/report.pdf",
            adapter_score=0.50,
            metadata=metadata,
        ),
        keyword="climate policy",
    )

    assert enriched.score > sparse.score


@pytest.mark.revised
def test_score_explanation_includes_signals_that_affected_the_score() -> None:
    scorer = RelevanceScorer(
        RelevanceScoringConfig(
            source_trust={
                SourceKind.ARXIV: 0.10,
                SourceKind.COMMON_CRAWL: 0.01,
            }
        )
    )

    scored = scorer.score(
        _candidate(
            title="Machine Learning Survey",
            url="https://arxiv.org/pdf/2401.00001.pdf",
            source=SourceKind.ARXIV,
            discovery_method=DiscoveryMethod.API,
            snippet="A survey about machine learning systems.",
            adapter_score=0.70,
            metadata={"doi": "10.1234/example"},
        ),
        keyword="machine learning",
    )

    explanation = scored.metadata["relevance_score"]

    assert explanation["adapter_score"] == 0.70
    assert set(explanation["signals"]) >= {
        "adapter_score",
        "source_trust",
        "direct_pdf_url",
        "title_keyword_match",
        "snippet_keyword_match",
        "metadata_completeness",
    }


@pytest.mark.revised
def test_sorting_is_deterministic_when_scores_are_tied() -> None:
    scorer = RelevanceScorer(
        RelevanceScoringConfig(
            source_trust={
                SourceKind.COMMON_CRAWL: 0.0,
                SourceKind.SITEMAP: 0.0,
            }
        )
    )
    candidates = [
        _candidate(
            title="Tie",
            url="https://z.example.org/report.pdf",
            source=SourceKind.SITEMAP,
            discovery_method=DiscoveryMethod.SITEMAP,
            adapter_score=0.50,
        ),
        _candidate(
            title="Tie",
            url="https://b.example.org/report.pdf",
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            adapter_score=0.50,
        ),
        _candidate(
            title="Tie",
            url="https://a.example.org/report.pdf",
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            adapter_score=0.50,
        ),
    ]

    ranked = scorer.rank(candidates, keyword="unrelated")

    assert [(candidate.source, candidate.url) for candidate in ranked] == [
        (SourceKind.COMMON_CRAWL, "https://a.example.org/report.pdf"),
        (SourceKind.COMMON_CRAWL, "https://b.example.org/report.pdf"),
        (SourceKind.SITEMAP, "https://z.example.org/report.pdf"),
    ]


def _candidate(
    *,
    title: str,
    url: str,
    adapter_score: float,
    source: SourceKind = SourceKind.COMMON_CRAWL,
    discovery_method: DiscoveryMethod = DiscoveryMethod.PUBLIC_INDEX,
    snippet: str | None = None,
    metadata: dict[str, object] | None = None,
) -> RawCandidate:
    return RawCandidate(
        title=title,
        url=url,
        source=source,
        discovery_method=discovery_method,
        query='"machine learning"',
        snippet=snippet,
        adapter_score=adapter_score,
        metadata=metadata or {},
    )
