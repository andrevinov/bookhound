from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from bookhound.database import initialize_database
from bookhound.discovery_pipeline import DiscoveryStepResult
from bookhound.models import (
    DiscoveryMethod,
    ExecutionMode,
    LicenseEvidence,
    RawCandidate,
    SourceKind,
)
from bookhound.query_planner import PlannedQueryVariant, QueryPlan
from bookhound.repositories import RepositorySet


@pytest.mark.revised
def test_incremental_collect_creates_query_before_saving_steps(
    tmp_path: Path,
    count_rows_helper,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    repositories = RepositorySet(initialize_database(database_path))
    query_plan = _query_plan("machine learning")

    try:
        query_id = repositories.begin_collection(query_plan)

        assert query_id > 0
        assert count_rows_helper(repositories.connection, "queries") == 1
        assert count_rows_helper(repositories.connection, "collection_steps") == 0
        assert count_rows_helper(repositories.connection, "documents") == 0
        assert count_rows_helper(repositories.connection, "document_urls") == 0

        with sqlite3.connect(database_path) as observer:
            row = observer.execute(
                """
                SELECT keyword, mode, variants_json
                FROM queries
                WHERE id = ?
                """,
                (query_id,),
            ).fetchone()

        assert row is not None
        assert row[0] == "machine learning"
        assert row[1] == ExecutionMode.COLLECT.value
        assert json.loads(row[2]) == ['"machine learning"']
    finally:
        repositories.close()


@pytest.mark.revised
def test_incremental_collect_saves_completed_step_candidates(
    tmp_path: Path,
    common_crawl_candidate_factory,
    count_rows_helper,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    query_plan = _query_plan("climate policy")
    variant = query_plan.variants[0]
    candidate = common_crawl_candidate_factory(
        title="Climate Policy Report",
        url="https://example.org/reports/climate.pdf?utm_source=newsletter",
        query=variant.query,
        metadata={
            "doi": "10.1234/climate",
            "authors": ["Ada Lovelace"],
            "language": "en",
        },
    )

    try:
        query_id = repositories.begin_collection(query_plan)
        summary = repositories.save_collection_step(
            query_id,
            _step(
                query_plan=query_plan,
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                status="completed",
                candidates=[candidate],
            ),
        )

        assert summary.total == 1
        assert summary.new == 1
        assert summary.updated == 0
        assert summary.duplicate == 0
        assert count_rows_helper(repositories.connection, "collection_steps") == 1
        assert count_rows_helper(repositories.connection, "sources") == 1
        assert count_rows_helper(repositories.connection, "documents") == 1
        assert count_rows_helper(repositories.connection, "document_urls") == 1

        step = _single_collection_step(repositories.connection)
        document = repositories.connection.execute(
            """
            SELECT title, doi, authors_json, language
            FROM documents
            """
        ).fetchone()
        document_url = repositories.connection.execute(
            """
            SELECT url, canonical_url, url_type, discovery_method, metadata_json
            FROM document_urls
            """
        ).fetchone()

        assert step["status"] == "completed"
        assert step["query_id"] == query_id
        assert step["variant_label"] == "quoted"
        assert step["variant_query"] == '"climate policy"'
        assert step["source"] == "common_crawl"
        assert step["discovery_method"] == "public_index"
        assert step["candidate_count"] == 1
        assert step["error_count"] == 0
        assert step["errors"] == []
        assert step["completed_at"] is not None
        assert document == (
            "Climate Policy Report",
            "10.1234/climate",
            '["Ada Lovelace"]',
            "en",
        )
        assert document_url[:4] == (
            "https://example.org/reports/climate.pdf?utm_source=newsletter",
            "https://example.org/reports/climate.pdf",
            "pdf",
            "public_index",
        )
        assert json.loads(document_url[4]) == {
            "query": '"climate policy"',
            "score": 0.75,
            "snippet": None,
        }
    finally:
        repositories.close()


@pytest.mark.revised
def test_incremental_collect_records_failed_step_without_deleting_prior_step(
    tmp_path: Path,
    sitemap_candidate_factory,
    count_rows_helper,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    query_plan = _query_plan("open textbooks")
    variant = query_plan.variants[0]
    completed_candidate = sitemap_candidate_factory(
        title="Open Textbooks",
        url="https://example.org/textbooks.pdf",
        query=variant.query,
    )

    try:
        query_id = repositories.begin_collection(query_plan)
        completed_summary = repositories.save_collection_step(
            query_id,
            _step(
                query_plan=query_plan,
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                status="completed",
                candidates=[completed_candidate],
            ),
        )
        failed_summary = repositories.save_collection_step(
            query_id,
            _step(
                query_plan=query_plan,
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                status="failed",
                errors=["availability: Common Crawl index unavailable."],
            ),
        )

        steps = _collection_step_rows(repositories.connection)

        assert completed_summary.total == 1
        assert completed_summary.new == 1
        assert failed_summary.total == 0
        assert failed_summary.new == 0
        assert count_rows_helper(repositories.connection, "queries") == 1
        assert count_rows_helper(repositories.connection, "collection_steps") == 2
        assert count_rows_helper(repositories.connection, "documents") == 1
        assert count_rows_helper(repositories.connection, "document_urls") == 1
        assert [(step["source"], step["status"]) for step in steps] == [
            ("sitemap", "completed"),
            ("common_crawl", "failed"),
        ]
        assert steps[1]["candidate_count"] == 0
        assert steps[1]["error_count"] == 1
        assert steps[1]["errors"] == [
            "availability: Common Crawl index unavailable."
        ]
    finally:
        repositories.close()


@pytest.mark.revised
def test_failed_step_persistence_rolls_back_only_current_step(
    tmp_path: Path,
    sitemap_candidate_factory,
    common_crawl_candidate_factory,
    count_rows_helper,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    query_plan = _query_plan("rollback safety")
    variant = query_plan.variants[0]
    first_candidate = sitemap_candidate_factory(
        title="Stable Result",
        url="https://example.org/stable.pdf",
        query=variant.query,
    )
    invalid_evidence = LicenseEvidence.model_construct(
        source="common_crawl",
        evidence_type="metadata",
        value="invalid status",
        suggested_status="invalid-license-status",
        confidence=0.2,
        collected_at=datetime(2026, 6, 7, 9, 0, tzinfo=timezone.utc),
    )
    invalid_candidate = common_crawl_candidate_factory(
        title="Broken Evidence Result",
        url="https://example.org/broken.pdf",
        query=variant.query,
        metadata={
            "license_evidence": [
                {
                    "evidence": invalid_evidence,
                    "metadata": {"source_event": "invalid_fixture"},
                }
            ],
        },
    )

    try:
        query_id = repositories.begin_collection(query_plan)
        repositories.save_collection_step(
            query_id,
            _step(
                query_plan=query_plan,
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                status="completed",
                candidates=[first_candidate],
            ),
        )

        with pytest.raises(sqlite3.IntegrityError):
            repositories.save_collection_step(
                query_id,
                _step(
                    query_plan=query_plan,
                    source=SourceKind.COMMON_CRAWL,
                    discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                    status="completed",
                    candidates=[invalid_candidate],
                ),
            )

        steps = _collection_step_rows(repositories.connection)

        assert count_rows_helper(repositories.connection, "queries") == 1
        assert count_rows_helper(repositories.connection, "collection_steps") == 1
        assert count_rows_helper(repositories.connection, "sources") == 1
        assert count_rows_helper(repositories.connection, "documents") == 1
        assert count_rows_helper(repositories.connection, "document_urls") == 1
        assert count_rows_helper(repositories.connection, "license_evidence") == 0
        assert steps[0]["source"] == "sitemap"
        assert steps[0]["status"] == "completed"
    finally:
        repositories.close()


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
    status: str,
    candidates: list[RawCandidate] | None = None,
    errors: list[str] | None = None,
) -> DiscoveryStepResult:
    return DiscoveryStepResult(
        query_plan=query_plan,
        variant=query_plan.variants[0],
        source=source,
        discovery_method=discovery_method,
        status=status,
        candidates=list(candidates or []),
        errors=list(errors or []),
        events=[],
    )


def _single_collection_step(connection: sqlite3.Connection) -> dict[str, object]:
    rows = _collection_step_rows(connection)
    assert len(rows) == 1
    return rows[0]


def _collection_step_rows(
    connection: sqlite3.Connection,
) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT
            query_id,
            variant_label,
            variant_query,
            source,
            discovery_method,
            status,
            completed_at,
            candidate_count,
            error_count,
            errors_json
        FROM collection_steps
        ORDER BY id
        """
    ).fetchall()
    return [
        {
            "query_id": int(row[0]),
            "variant_label": row[1],
            "variant_query": row[2],
            "source": row[3],
            "discovery_method": row[4],
            "status": row[5],
            "completed_at": row[6],
            "candidate_count": int(row[7]),
            "error_count": int(row[8]),
            "errors": json.loads(row[9]),
        }
        for row in rows
    ]
