# Consolidated test module. See docs/audit-tests/03-centralize-test-files.md.
# Consolidated from test_query_planner.py

import pytest

from bookhound.query_planner import QueryPlanner, QueryPlannerConfig


@pytest.mark.revised
def test_simple_keyword_generates_expected_variants() -> None:
    planner = QueryPlanner()

    plan = planner.plan_queries("machine learning")

    assert [variant.query for variant in plan.variants] == [
        '"machine learning"',
        '"machine learning" filetype:pdf',
        '"machine learning" ext:pdf',
        '"machine learning" "PDF"',
        '"machine learning" site:edu filetype:pdf',
        '"machine learning" site:gov filetype:pdf',
    ]


@pytest.mark.revised
def test_keyword_with_quotes_does_not_duplicate_quotes_in_variants() -> None:
    planner = QueryPlanner()

    plan = planner.plan_queries('"machine learning"')

    assert [variant.query for variant in plan.variants] == [
        '"machine learning"',
        '"machine learning" filetype:pdf',
        '"machine learning" ext:pdf',
        '"machine learning" "PDF"',
        '"machine learning" site:edu filetype:pdf',
        '"machine learning" site:gov filetype:pdf',
    ]


@pytest.mark.revised
def test_variant_limit_is_respected() -> None:
    planner = QueryPlanner(QueryPlannerConfig(max_variants=3))

    plan = planner.plan_queries("machine learning")

    assert [variant.query for variant in plan.variants] == [
        '"machine learning"',
        '"machine learning" filetype:pdf',
        '"machine learning" ext:pdf',
    ]


@pytest.mark.revised
def test_variants_can_be_enabled_or_disabled_through_configuration() -> None:
    planner = QueryPlanner(
        QueryPlannerConfig(
            include_pdf_phrase=False,
            include_site_edu=False,
            include_site_gov=True,
            include_ext_pdf=False,
        )
    )

    plan = planner.plan_queries("public health")

    assert [variant.query for variant in plan.variants] == [
        '"public health"',
        '"public health" filetype:pdf',
        '"public health" site:gov filetype:pdf',
    ]

@pytest.mark.revised
def test_planned_variants_include_labels_for_persistence() -> None:
    planner = QueryPlanner()

    plan = planner.plan_queries("statistics")

    assert plan.keyword == "statistics"
    assert [(variant.label, variant.query) for variant in plan.variants] == [
        ("quoted", '"statistics"'),
        ("filetype_pdf", '"statistics" filetype:pdf'),
        ("ext_pdf", '"statistics" ext:pdf'),
        ("pdf_phrase", '"statistics" "PDF"'),
        ("site_edu_pdf", '"statistics" site:edu filetype:pdf'),
        ("site_gov_pdf", '"statistics" site:gov filetype:pdf'),
    ]


# Consolidated from test_relevance_scoring.py

import pytest

from bookhound.models import DiscoveryMethod, SourceKind
from bookhound.relevance_scoring import RelevanceScorer, RelevanceScoringConfig


@pytest.mark.revised
def test_higher_adapter_score_improves_final_score(raw_candidate_factory) -> None:
    scorer = RelevanceScorer()
    low_score = scorer.score(
        raw_candidate_factory(
            title="Machine Learning Notes",
            url="https://example.org/notes.pdf",
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            query='"machine learning"',
            adapter_score=0.20,
            score=None,
        ),
        keyword="machine learning",
    )
    high_score = scorer.score(
        raw_candidate_factory(
            title="Machine Learning Notes",
            url="https://example.org/notes.pdf",
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            query='"machine learning"',
            adapter_score=0.80,
            score=None,
        ),
        keyword="machine learning",
    )

    assert high_score.score > low_score.score


@pytest.mark.revised
def test_direct_pdf_url_scores_higher_than_weak_landing_page_candidate(
    raw_candidate_factory,
) -> None:
    scorer = RelevanceScorer()
    landing_page = scorer.score(
        raw_candidate_factory(
            title="Course Materials",
            url="https://example.org/materials",
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            query='"machine learning"',
            adapter_score=0.50,
            score=None,
        ),
        keyword="machine learning",
    )
    direct_pdf = scorer.score(
        raw_candidate_factory(
            title="Course Materials",
            url="https://example.org/materials.pdf",
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            query='"machine learning"',
            adapter_score=0.50,
            score=None,
        ),
        keyword="machine learning",
    )

    assert direct_pdf.score > landing_page.score


@pytest.mark.revised
def test_title_and_snippet_keyword_matches_improve_relevance(
    raw_candidate_factory,
) -> None:
    scorer = RelevanceScorer()
    neutral = scorer.score(
        raw_candidate_factory(
            title="Course Materials",
            url="https://example.org/materials",
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            query='"machine learning"',
            snippet="Lecture notes and bibliography.",
            adapter_score=0.40,
            score=None,
        ),
        keyword="machine learning",
    )
    title_match = scorer.score(
        raw_candidate_factory(
            title="Machine Learning Materials",
            url="https://example.org/title-match",
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            query='"machine learning"',
            snippet="Lecture notes and bibliography.",
            adapter_score=0.40,
            score=None,
        ),
        keyword="machine learning",
    )
    snippet_match = scorer.score(
        raw_candidate_factory(
            title="Course Materials",
            url="https://example.org/snippet-match",
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            query='"machine learning"',
            snippet="Lecture notes about machine learning.",
            adapter_score=0.40,
            score=None,
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
def test_metadata_completeness_boosts_relevance(
    metadata: dict[str, object],
    raw_candidate_factory,
) -> None:
    scorer = RelevanceScorer()
    sparse = scorer.score(
        raw_candidate_factory(
            title="Climate Policy Report",
            url="https://example.org/report.pdf",
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            query='"machine learning"',
            adapter_score=0.50,
            score=None,
        ),
        keyword="climate policy",
    )
    enriched = scorer.score(
        raw_candidate_factory(
            title="Climate Policy Report",
            url="https://example.org/report.pdf",
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            query='"machine learning"',
            adapter_score=0.50,
            score=None,
            metadata=metadata,
        ),
        keyword="climate policy",
    )

    assert enriched.score > sparse.score


@pytest.mark.revised
def test_score_explanation_includes_signals_that_affected_the_score(
    raw_candidate_factory,
) -> None:
    scorer = RelevanceScorer(
        RelevanceScoringConfig(
            source_trust={
                SourceKind.ARXIV: 0.10,
                SourceKind.COMMON_CRAWL: 0.01,
            }
        )
    )

    scored = scorer.score(
        raw_candidate_factory(
            title="Machine Learning Survey",
            url="https://arxiv.org/pdf/2401.00001.pdf",
            source=SourceKind.ARXIV,
            discovery_method=DiscoveryMethod.API,
            query='"machine learning"',
            snippet="A survey about machine learning systems.",
            adapter_score=0.70,
            score=None,
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
def test_sorting_is_deterministic_when_scores_are_tied(
    raw_candidate_factory,
) -> None:
    scorer = RelevanceScorer(
        RelevanceScoringConfig(
            source_trust={
                SourceKind.COMMON_CRAWL: 0.0,
                SourceKind.SITEMAP: 0.0,
            }
        )
    )
    candidates = [
        raw_candidate_factory(
            title="Tie",
            url="https://z.example.org/report.pdf",
            source=SourceKind.SITEMAP,
            discovery_method=DiscoveryMethod.SITEMAP,
            query='"machine learning"',
            adapter_score=0.50,
            score=None,
        ),
        raw_candidate_factory(
            title="Tie",
            url="https://b.example.org/report.pdf",
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            query='"machine learning"',
            adapter_score=0.50,
            score=None,
        ),
        raw_candidate_factory(
            title="Tie",
            url="https://a.example.org/report.pdf",
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            query='"machine learning"',
            adapter_score=0.50,
            score=None,
        ),
    ]

    ranked = scorer.rank(candidates, keyword="unrelated")

    assert [(candidate.source, candidate.url) for candidate in ranked] == [
        (SourceKind.COMMON_CRAWL, "https://a.example.org/report.pdf"),
        (SourceKind.COMMON_CRAWL, "https://b.example.org/report.pdf"),
        (SourceKind.SITEMAP, "https://z.example.org/report.pdf"),
    ]
