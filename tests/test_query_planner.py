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
