# Consolidated test module. See docs/audit-tests/03-centralize-test-files.md.
# Consolidated from test_collect_cli.py

import json
import pytest
from pathlib import Path
import sqlite3

from typer.testing import CliRunner

import bookhound.cli as cli
from bookhound.collect_service import CollectService
from bookhound.database import initialize_database
from bookhound.discovery_pipeline import DiscoveryPipelineResult
from bookhound.query_planner import PlannedQueryVariant, QueryPlan
from bookhound.repositories import RepositorySet


@pytest.mark.revised
def test_collect_saves_candidates_to_sqlite(
    tmp_path: Path,
    monkeypatch,
    recording_pipeline_factory,
    sitemap_candidate_factory,
    count_rows_helper,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    pipeline = recording_pipeline_factory(
        [
            sitemap_candidate_factory(
                title="Open Climate Policy",
                url="https://example.org/climate.pdf",
                score=0.75,
                metadata={
                    "doi": "10.1234/climate",
                    "authors": ["Ada Lovelace"],
                    "year": 2026,
                    "language": "en",
                },
            )
        ]
    )
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    result = CliRunner().invoke(
        cli.app,
        ["collect", "climate policy"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert result.exit_code == 0
    assert pipeline.searched_keywords == ["climate policy"]

    with sqlite3.connect(database_path) as connection:
        assert count_rows_helper(connection, "queries") == 1
        assert count_rows_helper(connection, "sources") == 1
        assert count_rows_helper(connection, "documents") == 1
        assert count_rows_helper(connection, "document_urls") == 1
        query = connection.execute(
            "SELECT keyword, mode, variants_json FROM queries"
        ).fetchone()
        document = connection.execute(
            "SELECT title, doi, authors_json, year, language FROM documents"
        ).fetchone()
        document_url = connection.execute(
            "SELECT url, canonical_url, url_type, discovery_method FROM document_urls"
        ).fetchone()
        source = connection.execute("SELECT name FROM sources").fetchone()

    assert query[0] == "climate policy"
    assert query[1] == "collect"
    assert json.loads(query[2]) == ['"climate policy"']
    assert document == (
        "Open Climate Policy",
        "10.1234/climate",
        '["Ada Lovelace"]',
        2026,
        "en",
    )
    assert document_url == (
        "https://example.org/climate.pdf",
        "https://example.org/climate.pdf",
        "pdf",
        "sitemap",
    )
    assert source[0] == "sitemap"
    assert "new: 1" in result.stdout


@pytest.mark.revised
def test_collect_does_not_download_pdfs(
    tmp_path: Path,
    monkeypatch,
    recording_pipeline_factory,
    common_crawl_candidate_factory,
    count_rows_helper,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    pipeline = recording_pipeline_factory(
        [
            common_crawl_candidate_factory(
                title="No Download",
                url="https://example.org/file.pdf",
            )
        ]
    )
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    result = CliRunner().invoke(
        cli.app,
        ["collect", "no download"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert result.exit_code == 0
    with sqlite3.connect(database_path) as connection:
        assert count_rows_helper(connection, "downloads") == 0


@pytest.mark.revised
def test_collect_running_twice_does_not_duplicate_equivalent_documents(
    tmp_path: Path,
    monkeypatch,
    recording_pipeline_factory,
    common_crawl_candidate_factory,
    count_rows_helper,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    pipeline = recording_pipeline_factory(
        [
            common_crawl_candidate_factory(
                title="Duplicate Report",
                url="https://example.org/duplicate.pdf?utm_source=newsletter",
                metadata={"doi": "10.1234/duplicate"},
            )
        ]
    )
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    first_result = CliRunner().invoke(
        cli.app,
        ["collect", "duplicate"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )
    second_result = CliRunner().invoke(
        cli.app,
        ["collect", "duplicate"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert first_result.exit_code == 0
    assert second_result.exit_code == 0
    with sqlite3.connect(database_path) as connection:
        assert count_rows_helper(connection, "queries") == 2
        assert count_rows_helper(connection, "documents") == 1
        assert count_rows_helper(connection, "document_urls") == 1

    assert "new: 1" in first_result.stdout
    assert "duplicate: 1" in second_result.stdout


@pytest.mark.revised
def test_collect_records_collection_events(
    tmp_path: Path,
    monkeypatch,
    recording_pipeline_factory,
    common_crawl_candidate_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    pipeline = recording_pipeline_factory(
        [common_crawl_candidate_factory(title="Evented Report")]
    )
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    result = CliRunner().invoke(
        cli.app,
        ["collect", "events"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert result.exit_code == 0
    with sqlite3.connect(database_path) as connection:
        event = connection.execute(
            "SELECT event_type, message, metadata_json FROM events"
        ).fetchone()

    assert event[0] == "collect.completed"
    assert event[1] == "Collected 1 candidate for events."
    assert json.loads(event[2]) == {
        "keyword": "events",
        "new": 1,
        "updated": 0,
        "duplicate": 0,
        "errors": [],
    }

@pytest.mark.revised
def test_collect_service_saves_result_and_returns_summary(
    tmp_path: Path,
    common_crawl_candidate_factory,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    result = DiscoveryPipelineResult(
        query_plan=QueryPlan(
            keyword="service collect",
            variants=[
                PlannedQueryVariant(
                    label="quoted",
                    query='"service collect"',
                )
            ],
        ),
        candidates=[
            common_crawl_candidate_factory(
                title="Service Report",
                url="https://example.org/service.pdf",
                metadata={
                    "doi": "10.1234/service",
                    "authors": ["Ada Lovelace"],
                    "canonical_url": "https://example.org/service.pdf",
                },
            )
        ],
        errors=["sitemap: transient warning"],
    )

    try:
        summary = CollectService(repositories).save_result(result)
        event = repositories.connection.execute(
            "SELECT event_type, metadata_json FROM events"
        ).fetchone()
        document = repositories.connection.execute(
            "SELECT title, doi, authors_json FROM documents"
        ).fetchone()
    finally:
        repositories.close()

    assert summary.total == 1
    assert summary.new == 1
    assert summary.updated == 0
    assert summary.duplicate == 0
    assert event[0] == "collect.completed"
    assert json.loads(event[1])["errors"] == ["sitemap: transient warning"]
    assert document == ("Service Report", "10.1234/service", '["Ada Lovelace"]')


# Consolidated from test_collect_runtime_cli.py

import json
from pathlib import Path
import sqlite3

import pytest

from typer.testing import CliRunner

import bookhound.cli as cli


@pytest.mark.revised
def test_collect_uses_runtime_pipeline_and_persists_real_adapter_candidates(
    tmp_path: Path,
    monkeypatch,
    sitemap_http_client_factory,
    write_sitemap_runtime_config_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    config_path = write_sitemap_runtime_config_factory(
        tmp_path,
        database_path=database_path,
        pdf_directory=tmp_path / "pdfs",
    )
    monkeypatch.setattr(
        cli,
        "build_http_client",
        lambda settings: sitemap_http_client_factory(
            robots_content=b"Sitemap: https://example.org/sitemap.xml",
            sitemap_content=b"""
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url>
                <loc>https://example.org/reports/machine-learning.pdf</loc>
                <lastmod>2026-07-01</lastmod>
              </url>
            </urlset>
            """
        ),
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["--config", str(config_path), "collect", "machine learning"],
    )

    assert result.exit_code == 0
    assert "Collected 1 candidate" in result.stdout

    with sqlite3.connect(database_path) as connection:
        query = connection.execute("SELECT keyword, mode FROM queries").fetchone()
        source = connection.execute("SELECT name FROM sources").fetchone()
        document = connection.execute(
            "SELECT title, metadata_json FROM documents"
        ).fetchone()
        document_url = connection.execute(
            """
            SELECT url, canonical_url, url_type, discovery_method, metadata_json
            FROM document_urls
            """
        ).fetchone()

    document_metadata = json.loads(document[1])
    document_url_metadata = json.loads(document_url[4])

    assert query == ("machine learning", "collect")
    assert source[0] == "sitemap"
    assert document[0] == "machine-learning.pdf"
    assert document_metadata["sitemap_url"] == "https://example.org/sitemap.xml"
    assert document_metadata["lastmod"] == "2026-07-01"
    assert document_metadata["url_type"] == "pdf"
    assert document_url[:4] == (
        "https://example.org/reports/machine-learning.pdf",
        "https://example.org/reports/machine-learning.pdf",
        "pdf",
        "sitemap",
    )
    assert document_url_metadata["score"] == 0.7


@pytest.mark.revised
def test_collect_does_not_download_when_runtime_pipeline_finds_pdf(
    tmp_path: Path,
    monkeypatch,
    sitemap_http_client_factory,
    write_sitemap_runtime_config_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    pdf_directory = tmp_path / "pdfs"
    config_path = write_sitemap_runtime_config_factory(
        tmp_path,
        database_path=database_path,
        pdf_directory=pdf_directory,
    )
    monkeypatch.setattr(
        cli,
        "build_http_client",
        lambda settings: sitemap_http_client_factory(
            robots_content=b"Sitemap: https://example.org/sitemap.xml",
            sitemap_content=b"""
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url>
                <loc>https://example.org/reports/download-safety.pdf</loc>
              </url>
            </urlset>
            """
        ),
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["--config", str(config_path), "collect", "download safety"],
    )

    assert result.exit_code == 0
    with sqlite3.connect(database_path) as connection:
        downloads = connection.execute("SELECT COUNT(*) FROM downloads").fetchone()

    assert downloads[0] == 0
    assert pdf_directory.exists() is False


@pytest.mark.revised
def test_collect_deduplicates_runtime_candidates_by_canonical_url(
    tmp_path: Path,
    monkeypatch,
    sitemap_http_client_factory,
    write_sitemap_runtime_config_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    config_path = write_sitemap_runtime_config_factory(
        tmp_path,
        database_path=database_path,
        pdf_directory=tmp_path / "pdfs",
    )
    monkeypatch.setattr(
        cli,
        "build_http_client",
        lambda settings: sitemap_http_client_factory(
            robots_content=b"Sitemap: https://example.org/sitemap.xml",
            sitemap_content=b"""
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url>
                <loc>https://Example.org/reports/duplicate.pdf?utm_source=newsletter</loc>
              </url>
              <url>
                <loc>https://example.org/reports/duplicate.pdf</loc>
              </url>
            </urlset>
            """
        ),
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["--config", str(config_path), "collect", "duplicate"],
    )

    assert result.exit_code == 0
    assert "duplicate: 0" in result.stdout
    with sqlite3.connect(database_path) as connection:
        document_count = connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()
        document_url = connection.execute(
            "SELECT url, canonical_url FROM document_urls"
        ).fetchone()

    assert document_count[0] == 1
    assert document_url == (
        "https://Example.org/reports/duplicate.pdf?utm_source=newsletter",
        "https://example.org/reports/duplicate.pdf",
    )


# Consolidated from test_collect_persistence_boundary.py

import json
from pathlib import Path
import sqlite3

import pytest

from bookhound.collect_service import CollectService, CollectSummary
from bookhound.database import initialize_database
from bookhound.discovery_pipeline import DiscoveryPipelineResult
from bookhound.models import (
    DiscoveryMethod,
    Document,
    DocumentUrl,
    LicenseEvidence,
    LicenseStatus,
    SourceKind,
    UrlType,
)
from bookhound.query_planner import PlannedQueryVariant, QueryPlan
from bookhound.repositories import RepositorySet


class RecordingCollectionBoundary:
    def __init__(self, summary: CollectSummary) -> None:
        self.summary = summary
        self.results: list[DiscoveryPipelineResult] = []

    def save_discovery_result(self, result: DiscoveryPipelineResult) -> CollectSummary:
        self.results.append(result)
        return self.summary


@pytest.mark.revised
def test_collect_service_delegates_to_repository_collection_boundary(
    common_crawl_candidate_factory,
    discovery_result_factory,
) -> None:
    result = discovery_result_factory(
        keyword="delegated",
        candidates=[
            common_crawl_candidate_factory(
                title="Collection Report",
                url="https://example.org/reports/collection.pdf",
                query='"collection report"',
                score=0.8,
                metadata={
                    "canonical_url": "https://example.org/reports/collection.pdf"
                },
            )
        ],
    )
    boundary = RecordingCollectionBoundary(
        CollectSummary(total=1, new=1, updated=0, duplicate=0)
    )

    summary = CollectService(boundary).save_result(result)

    assert summary == CollectSummary(total=1, new=1, updated=0, duplicate=0)
    assert boundary.results == [result]


@pytest.mark.revised
def test_repository_collection_boundary_persists_evidence_event_and_url_metadata(
    tmp_path: Path,
    discovery_result_factory,
    raw_candidate_factory,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    evidence = _license_evidence()
    result = discovery_result_factory(
        keyword="evidence",
        candidates=[
            raw_candidate_factory(
                title="Evidence Report",
                url="https://example.org/reports/evidence.pdf",
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                query='"evidence report"',
                snippet="A report with source evidence.",
                score=0.75,
                metadata={
                    "doi": "10.1234/evidence",
                    "authors": ["Ada Lovelace"],
                    "canonical_url": "https://example.org/reports/evidence.pdf",
                    "license_evidence": [
                        {
                            "evidence": evidence,
                            "metadata": {"source_record": "fixture"},
                        }
                    ],
                },
            )
        ],
        errors=["common_crawl: transient warning"],
    )

    try:
        summary = repositories.save_discovery_result(result)
        event = repositories.connection.execute(
            "SELECT event_type, message, metadata_json FROM events"
        ).fetchone()
        document = repositories.connection.execute(
            "SELECT title, doi, authors_json, metadata_json FROM documents"
        ).fetchone()
        document_url = repositories.connection.execute(
            "SELECT url, canonical_url, metadata_json FROM document_urls"
        ).fetchone()
        evidence_row = repositories.connection.execute(
            """
            SELECT source, evidence_type, value, suggested_status, metadata_json
            FROM license_evidence
            """
        ).fetchone()
    finally:
        repositories.close()

    assert summary == CollectSummary(total=1, new=1, updated=0, duplicate=0)
    assert event[0] == "collect.completed"
    assert event[1] == "Collected 1 candidate for evidence."
    assert json.loads(event[2]) == {
        "keyword": "evidence",
        "new": 1,
        "updated": 0,
        "duplicate": 0,
        "errors": ["common_crawl: transient warning"],
    }
    assert document[:3] == ("Evidence Report", "10.1234/evidence", '["Ada Lovelace"]')
    assert "license_evidence" not in json.loads(document[3])
    assert document_url[:2] == (
        "https://example.org/reports/evidence.pdf",
        "https://example.org/reports/evidence.pdf",
    )
    assert json.loads(document_url[2]) == {
        "query": '"evidence"',
        "score": 0.75,
        "snippet": "A report with source evidence.",
    }
    assert evidence_row[:4] == (
        "common_crawl",
        "metadata",
        "unknown",
        "unknown",
    )
    assert json.loads(evidence_row[4]) == {"source_record": "fixture"}


@pytest.mark.revised
def test_repository_collection_boundary_preserves_duplicate_summary_behavior(
    tmp_path: Path,
    common_crawl_candidate_factory,
    discovery_result_factory,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    url = "https://example.org/reports/duplicate.pdf"
    _seed_collected_document(repositories, title="Original Title", url=url)
    result = discovery_result_factory(
        keyword="duplicate",
        candidates=[
            common_crawl_candidate_factory(
                title="Updated Title",
                url=url,
                query='"updated title"',
                score=0.9,
                metadata={
                    "doi": "10.1234/duplicate",
                    "canonical_url": url,
                },
            )
        ],
    )

    try:
        summary = repositories.save_discovery_result(result)
        document = repositories.connection.execute(
            "SELECT title, doi FROM documents"
        ).fetchone()
        event_metadata = repositories.connection.execute(
            "SELECT metadata_json FROM events"
        ).fetchone()[0]
    finally:
        repositories.close()

    assert summary == CollectSummary(total=1, new=0, updated=0, duplicate=1)
    assert document == ("Original Title", "10.1234/duplicate")
    assert json.loads(event_metadata) == {
        "keyword": "duplicate",
        "new": 0,
        "updated": 0,
        "duplicate": 1,
        "errors": [],
    }


@pytest.mark.revised
def test_repository_collection_boundary_rolls_back_all_collection_tables_on_late_failure(
    tmp_path: Path,
    common_crawl_candidate_factory,
    count_rows_helper,
    discovery_result_factory,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    invalid_evidence = LicenseEvidence.model_construct(
        source="common_crawl",
        evidence_type="metadata",
        value="invalid status",
        suggested_status="invalid-license-status",
        confidence=0.2,
    )
    result = discovery_result_factory(
        keyword="rollback",
        candidates=[
            common_crawl_candidate_factory(
                title="First Report",
                url="https://example.org/reports/first.pdf",
                query='"first report"',
                score=0.8,
                metadata={
                    "doi": "10.1234/first",
                    "canonical_url": "https://example.org/reports/first.pdf",
                },
            ),
            common_crawl_candidate_factory(
                title="Broken Evidence Report",
                url="https://example.org/reports/broken-evidence.pdf",
                query='"broken evidence report"',
                score=0.8,
                metadata={
                    "doi": "10.1234/broken-evidence",
                    "canonical_url": "https://example.org/reports/broken-evidence.pdf",
                    "license_evidence": [{"evidence": invalid_evidence}],
                },
            ),
        ],
    )

    try:
        with pytest.raises(sqlite3.IntegrityError):
            repositories.save_discovery_result(result)

        counts = {
            table_name: count_rows_helper(repositories.connection, table_name)
            for table_name in (
                "queries",
                "sources",
                "documents",
                "document_urls",
                "license_evidence",
                "events",
            )
        }
    finally:
        repositories.close()

    assert counts == {
        "queries": 0,
        "sources": 0,
        "documents": 0,
        "document_urls": 0,
        "license_evidence": 0,
        "events": 0,
    }


@pytest.mark.revised
def test_repository_collection_boundary_persists_operational_events(
    tmp_path: Path,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    result = DiscoveryPipelineResult(
        query_plan=QueryPlan(
            keyword="observability",
            variants=[PlannedQueryVariant(label="quoted", query='"observability"')],
        ),
        candidates=[],
        errors=[],
        events=[
            {
                "source": "sitemap",
                "event_type": "sitemap.frontier_rejected",
                "message": "Rejected sitemap URL outside the configured frontier.",
                "metadata": {
                    "url": "https://outside.example/sitemap.xml",
                },
            }
        ],
    )

    try:
        summary = repositories.save_discovery_result(result)
        rows = repositories.connection.execute(
            """
            SELECT event_type, entity_type, message, metadata_json
            FROM events
            ORDER BY event_type
            """
        ).fetchall()
    finally:
        repositories.close()

    events_by_type = {str(row[0]): row for row in rows}
    assert summary == CollectSummary(total=0, new=0, updated=0, duplicate=0)
    assert set(events_by_type) == {
        "collect.completed",
        "sitemap.frontier_rejected",
    }

    collect_completed = events_by_type["collect.completed"]
    assert collect_completed[1] == "query"
    assert json.loads(collect_completed[3]) == {
        "keyword": "observability",
        "new": 0,
        "updated": 0,
        "duplicate": 0,
        "errors": [],
    }

    operational_event = events_by_type["sitemap.frontier_rejected"]
    assert operational_event[1] == "query"
    assert operational_event[2] == "Rejected sitemap URL outside the configured frontier."
    assert json.loads(operational_event[3]) == {
        "keyword": "observability",
        "source": "sitemap",
        "url": "https://outside.example/sitemap.xml",
    }


def _license_evidence() -> LicenseEvidence:
    return LicenseEvidence(
        source="common_crawl",
        evidence_type="metadata",
        value="unknown",
        suggested_status=LicenseStatus.UNKNOWN,
        confidence=0.2,
    )


def _seed_collected_document(
    repositories: RepositorySet,
    *,
    title: str,
    url: str,
) -> None:
    source_id = repositories.sources.upsert(SourceKind.COMMON_CRAWL)
    document_id = repositories.documents.upsert(
        Document(title=title, doi="10.1234/duplicate")
    )
    repositories.document_urls.upsert(
        document_id=document_id,
        source_id=source_id,
        document_url=DocumentUrl(
            url=url,
            canonical_url=url,
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            url_type=UrlType.PDF,
            confidence=0.5,
        ),
        metadata={"query": "seed"},
    )
