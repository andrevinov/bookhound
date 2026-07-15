from pathlib import Path
import sqlite3
from typing import Iterable

from typer.testing import CliRunner

import pytest

import bookhound.cli as cli
from bookhound.discovery_pipeline import DiscoveryPipeline, DiscoveryStepResult
from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind
from bookhound.query_planner import (
    PlannedQueryVariant,
    QueryPlan,
    QueryPlanner,
    QueryPlannerConfig,
)
from bookhound.sources import FakeSourceAdapter


@pytest.mark.revised
def test_collect_initializes_database_before_pipeline_finishes(
    tmp_path: Path,
    monkeypatch,
    sitemap_candidate_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    query_plan = _query_plan("early database")
    pipeline = InspectingIncrementalPipeline(
        database_path=database_path,
        steps=[
            _step(
                query_plan=query_plan,
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[
                    sitemap_candidate_factory(
                        title="Early Database Report",
                        url="https://example.org/early.pdf",
                        query=query_plan.variants[0].query,
                    )
                ],
            )
        ],
    )
    monkeypatch.setattr(
        cli,
        "build_search_pipeline",
        lambda: pipeline,
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["collect", "early database"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert result.exit_code == 0
    assert pipeline.iterated_keywords == ["early database"]
    assert pipeline.database_existed_before_first_step is True


@pytest.mark.revised
def test_collect_preserves_completed_step_when_later_step_crashes(
    tmp_path: Path,
    monkeypatch,
    sitemap_candidate_factory,
    count_rows_helper,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    query_plan = _query_plan("partial failure")
    pipeline = CrashingIncrementalPipeline(
        steps=[
            _step(
                query_plan=query_plan,
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[
                    sitemap_candidate_factory(
                        title="Persisted Before Crash",
                        url="https://example.org/persisted.pdf",
                        query=query_plan.variants[0].query,
                    )
                ],
            )
        ],
        error=RuntimeError("simulated crash after first step"),
    )
    monkeypatch.setattr(
        cli,
        "build_search_pipeline",
        lambda: pipeline,
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["collect", "partial failure"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.stdout

    with sqlite3.connect(database_path) as connection:
        assert count_rows_helper(connection, "queries") == 1
        assert count_rows_helper(connection, "collection_steps") == 1
        assert count_rows_helper(connection, "documents") == 1
        assert count_rows_helper(connection, "document_urls") == 1
        step = connection.execute(
            """
            SELECT source, discovery_method, status, candidate_count, error_count
            FROM collection_steps
            """
        ).fetchone()

    assert step == ("sitemap", "sitemap", "completed", 1, 0)


@pytest.mark.revised
def test_collect_accumulates_summary_from_incremental_steps(
    tmp_path: Path,
    monkeypatch,
    sitemap_candidate_factory,
    common_crawl_candidate_factory,
    count_rows_helper,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    query_plan = _query_plan("summary")
    pipeline = StaticIncrementalPipeline(
        [
            _step(
                query_plan=query_plan,
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[
                    sitemap_candidate_factory(
                        title="Sitemap Summary",
                        url="https://example.org/sitemap-summary.pdf",
                        query=query_plan.variants[0].query,
                    )
                ],
            ),
            _step(
                query_plan=query_plan,
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                candidates=[
                    common_crawl_candidate_factory(
                        title="Common Crawl Summary",
                        url="https://example.org/common-crawl-summary.pdf",
                        query=query_plan.variants[0].query,
                    )
                ],
            ),
        ]
    )
    monkeypatch.setattr(
        cli,
        "build_search_pipeline",
        lambda: pipeline,
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["collect", "summary"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert result.exit_code == 0
    assert (
        "Collected 2 candidates: new: 2, updated: 0, duplicate: 0"
        in result.stdout
    )
    assert pipeline.iterated_keywords == ["summary"]

    with sqlite3.connect(database_path) as connection:
        assert count_rows_helper(connection, "queries") == 1
        assert count_rows_helper(connection, "collection_steps") == 2
        assert count_rows_helper(connection, "documents") == 2
        assert count_rows_helper(connection, "document_urls") == 2
        steps = connection.execute(
            """
            SELECT source, status, candidate_count, errors_json
            FROM collection_steps
            ORDER BY id
            """
        ).fetchall()

    assert steps == [
        ("sitemap", "completed", 1, "[]"),
        ("common_crawl", "completed", 1, "[]"),
    ]


@pytest.mark.revised
def test_collect_persists_link_expansion_step_as_own_collection_step(
    tmp_path: Path,
    monkeypatch,
    sitemap_candidate_factory,
    raw_candidate_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    landing_candidate = sitemap_candidate_factory(
        title="Landing Page",
        url="https://example.org/reports/landing",
        query='"expanded links"',
    )
    expanded_candidate = raw_candidate_factory(
        title="Expanded PDF",
        url="https://example.org/reports/expanded.pdf",
        source=SourceKind.LINK_EXPANSION,
        discovery_method=DiscoveryMethod.LINK_EXPANSION,
        query='"expanded links"',
        score=0.88,
    )
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[landing_candidate],
            )
        ],
        link_expander=RecordingLinkExpander([expanded_candidate]),
        query_planner=QueryPlanner(QueryPlannerConfig(max_variants=1)),
    )
    monkeypatch.setattr(
        cli,
        "build_search_pipeline",
        lambda: pipeline,
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["collect", "expanded links"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert result.exit_code == 0
    with sqlite3.connect(database_path) as connection:
        steps = connection.execute(
            """
            SELECT source, discovery_method, status, candidate_count
            FROM collection_steps
            ORDER BY id
            """
        ).fetchall()
        expanded_url = connection.execute(
            """
            SELECT url, canonical_url, url_type, discovery_method
            FROM document_urls
            WHERE canonical_url = ?
            """,
            ("https://example.org/reports/expanded.pdf",),
        ).fetchone()

    assert steps == [
        ("sitemap", "sitemap", "completed", 1),
        ("link_expansion", "link_expansion", "completed", 1),
    ]
    assert expanded_url == (
        "https://example.org/reports/expanded.pdf",
        "https://example.org/reports/expanded.pdf",
        "pdf",
        "link_expansion",
    )


@pytest.mark.revised
def test_link_expansion_failure_does_not_delete_source_steps(
    tmp_path: Path,
    monkeypatch,
    sitemap_candidate_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[
                    sitemap_candidate_factory(
                        title="Persisted Landing Page",
                        url="https://example.org/reports/landing",
                        query='"link failure"',
                    )
                ],
            )
        ],
        link_expander=FailingLinkExpander(
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        ),
        query_planner=QueryPlanner(QueryPlannerConfig(max_variants=1)),
    )
    monkeypatch.setattr(
        cli,
        "build_search_pipeline",
        lambda: pipeline,
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["collect", "link failure"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert result.exit_code == 0
    with sqlite3.connect(database_path) as connection:
        steps = connection.execute(
            """
            SELECT
                source,
                discovery_method,
                status,
                candidate_count,
                error_count,
                errors_json
            FROM collection_steps
            ORDER BY id
            """
        ).fetchall()
        persisted_url_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM document_urls
            WHERE canonical_url = ?
            """,
            ("https://example.org/reports/landing",),
        ).fetchone()

    assert len(steps) == 2
    assert steps[0] == ("sitemap", "sitemap", "completed", 1, 0, "[]")
    assert steps[1][:5] == (
        "link_expansion",
        "link_expansion",
        "failed",
        0,
        1,
    )
    assert "utf-8" in json.loads(steps[1][5])[0]
    assert persisted_url_count == (1,)


class StaticIncrementalPipeline:
    def __init__(self, steps: Iterable[DiscoveryStepResult]) -> None:
        self.steps = list(steps)
        self.iterated_keywords: list[str] = []

    def search(self, keyword: str) -> object:
        raise AssertionError("collect must consume iter_search, not search")

    def iter_search(self, keyword: str):
        self.iterated_keywords.append(keyword)
        yield from self.steps


class InspectingIncrementalPipeline(StaticIncrementalPipeline):
    def __init__(
        self,
        *,
        database_path: Path,
        steps: Iterable[DiscoveryStepResult],
    ) -> None:
        super().__init__(steps)
        self.database_path = database_path
        self.database_existed_before_first_step = False

    def iter_search(self, keyword: str):
        self.iterated_keywords.append(keyword)
        self.database_existed_before_first_step = self.database_path.exists()
        yield from self.steps


class CrashingIncrementalPipeline(StaticIncrementalPipeline):
    def __init__(
        self,
        *,
        steps: Iterable[DiscoveryStepResult],
        error: BaseException,
    ) -> None:
        super().__init__(steps)
        self.error = error

    def iter_search(self, keyword: str):
        self.iterated_keywords.append(keyword)
        yield from self.steps
        raise self.error


class RecordingLinkExpander:
    def __init__(self, candidates: Iterable[RawCandidate]) -> None:
        self.candidates = list(candidates)

    def expand(
        self,
        existing_candidates: list[RawCandidate],
        *,
        query: str,
    ) -> list[RawCandidate]:
        return self.candidates


class FailingLinkExpander:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def expand(
        self,
        existing_candidates: list[RawCandidate],
        *,
        query: str,
    ) -> list[RawCandidate]:
        raise self.error


def _query_plan(keyword: str) -> QueryPlan:
    return QueryPlan(
        keyword=keyword,
        variants=[PlannedQueryVariant(label="quoted", query=f'"{keyword}"')],
    )


def _step(
    *,
    query_plan: QueryPlan,
    source: SourceKind,
    discovery_method: DiscoveryMethod,
    candidates: list[RawCandidate],
) -> DiscoveryStepResult:
    return DiscoveryStepResult(
        query_plan=query_plan,
        variant=query_plan.variants[0],
        source=source,
        discovery_method=discovery_method,
        status="completed",
        candidates=candidates,
        errors=[],
        events=[],
    )
