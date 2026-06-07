from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from bookhound.database import initialize_database
from bookhound.models import (
    DiscoveryMethod,
    Document,
    DocumentUrl,
    DownloadStatus,
    ExecutionMode,
    LicenseEvidence,
    LicenseStatus,
    SearchQuery,
    SourceKind,
    UrlType,
)
from bookhound.repositories import RepositorySet


@pytest.mark.revised
def test_document_upsert_does_not_duplicate_doi(tmp_path: Path) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))

    try:
        first_id = repositories.documents.upsert(
            Document(
                title="Machine Learning Notes",
                authors=["Ada Lovelace"],
                doi="10.1234/bookhound",
                isbn="9780000000001",
                year=2026,
                language="en",
                metadata={"source_count": 1},
            )
        )
        second_id = repositories.documents.upsert(
            Document(
                title="Updated Machine Learning Notes",
                authors=["Ada Lovelace", "Alan Turing"],
                doi="10.1234/bookhound",
                isbn="9780000000001",
                year=2026,
                language="en",
                metadata={"last_seen_source": "common_crawl"},
            )
        )

        row = repositories.connection.execute(
            """
            SELECT title, authors_json, metadata_json
            FROM documents
            WHERE doi = ?
            """,
            ("10.1234/bookhound",),
        ).fetchone()

        assert second_id == first_id
        assert _table_count(repositories.connection, "documents") == 1
        assert row[0] == "Updated Machine Learning Notes"
        assert json.loads(row[1]) == ["Ada Lovelace", "Alan Turing"]
        assert json.loads(row[2]) == {
            "source_count": 1,
            "last_seen_source": "common_crawl",
        }
    finally:
        repositories.close()


@pytest.mark.revised
def test_document_url_upsert_merges_metadata_without_losing_history(tmp_path: Path) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))

    try:
        source_id = repositories.sources.upsert(SourceKind.COMMON_CRAWL)
        document_id = repositories.documents.upsert(Document(title="A Cataloged PDF"))
        document_url = DocumentUrl(
            url="https://example.org/file.pdf",
            canonical_url="https://example.org/file.pdf",
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            url_type=UrlType.PDF,
            confidence=0.7,
            discovered_at=datetime(2026, 6, 7, 8, 0, tzinfo=timezone.utc),
        )

        first_id = repositories.document_urls.upsert(
            document_id=document_id,
            source_id=source_id,
            document_url=document_url,
            metadata={"first_seen_by": "common_crawl", "seen_count": 1},
        )
        second_id = repositories.document_urls.upsert(
            document_id=document_id,
            source_id=source_id,
            document_url=document_url.model_copy(update={"confidence": 0.95, "http_status": 200}),
            metadata={"last_seen_by": "sitemap", "seen_count": 2},
        )

        row = repositories.connection.execute(
            """
            SELECT confidence, http_status, metadata_json
            FROM document_urls
            WHERE canonical_url = ?
            """,
            ("https://example.org/file.pdf",),
        ).fetchone()

        assert second_id == first_id
        assert _table_count(repositories.connection, "document_urls") == 1
        assert row[0] == 0.95
        assert row[1] == 200
        assert json.loads(row[2]) == {
            "first_seen_by": "common_crawl",
            "last_seen_by": "sitemap",
            "seen_count": 2,
        }
    finally:
        repositories.close()


@pytest.mark.revised
def test_repositories_can_create_core_records(tmp_path: Path) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))

    try:
        query_id = repositories.queries.create(
            SearchQuery(
                keyword="open access statistics",
                mode=ExecutionMode.COLLECT,
                variants=['"open access statistics" filetype:pdf'],
            ),
            parameters={"limit": 10},
        )
        source_id = repositories.sources.upsert(SourceKind.UNPAYWALL)
        document_id = repositories.documents.upsert(
            Document(title="Open Access Statistics", doi="10.1234/statistics")
        )
        document_url_id = repositories.document_urls.upsert(
            document_id=document_id,
            source_id=source_id,
            document_url=DocumentUrl(
                url="https://example.org/statistics.pdf",
                canonical_url="https://example.org/statistics.pdf",
                source=SourceKind.UNPAYWALL,
                discovery_method=DiscoveryMethod.ENRICHMENT,
                url_type=UrlType.PDF,
                confidence=1.0,
            ),
            metadata={},
        )
        evidence_id = repositories.license_evidence.add(
            document_id=document_id,
            document_url_id=document_url_id,
            evidence=LicenseEvidence(
                source="unpaywall",
                evidence_type="api",
                value="cc-by",
                suggested_status=LicenseStatus.ALLOWED,
                confidence=0.9,
            ),
            metadata={"query_id": query_id},
        )
        download_id = repositories.downloads.add(
            document_id=document_id,
            document_url_id=document_url_id,
            local_path="/tmp/bookhound/statistics.pdf",
            status=DownloadStatus.DOWNLOADED,
            sha256="b" * 64,
            size_bytes=4096,
            license_evidence_id=evidence_id,
        )
        event_id = repositories.events.add(
            event_type="download.completed",
            entity_type="download",
            entity_id=download_id,
            message="Downloaded after license approval.",
            metadata={"source": "unpaywall"},
        )

        assert query_id > 0
        assert source_id > 0
        assert document_id > 0
        assert document_url_id > 0
        assert evidence_id > 0
        assert download_id > 0
        assert event_id > 0
    finally:
        repositories.close()

@pytest.mark.revised
def test_save_collection_result_commits_document_url_and_evidence_atomically(
    tmp_path: Path,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))

    try:
        result = repositories.save_collection_result(
            query=SearchQuery(keyword="education", mode=ExecutionMode.COLLECT),
            source=SourceKind.COMMON_CRAWL,
            document=Document(title="Education Report", doi="10.1234/education"),
            document_url=DocumentUrl(
                url="https://example.org/education.pdf",
                canonical_url="https://example.org/education.pdf",
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                url_type=UrlType.PDF,
                confidence=0.8,
            ),
            evidence=[
                LicenseEvidence(
                    source="common_crawl",
                    evidence_type="metadata",
                    value="unknown",
                    suggested_status=LicenseStatus.UNKNOWN,
                    confidence=0.2,
                )
            ],
        )

        assert result.query_id > 0
        assert result.source_id > 0
        assert result.document_id > 0
        assert result.document_url_id > 0
        assert len(result.license_evidence_ids) == 1
        assert _table_count(repositories.connection, "queries") == 1
        assert _table_count(repositories.connection, "sources") == 1
        assert _table_count(repositories.connection, "documents") == 1
        assert _table_count(repositories.connection, "document_urls") == 1
        assert _table_count(repositories.connection, "license_evidence") == 1
    finally:
        repositories.close()


@pytest.mark.revised
def test_save_collection_result_rolls_back_when_later_insert_fails(tmp_path: Path) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))

    try:
        invalid_evidence = LicenseEvidence.model_construct(
            source="common_crawl",
            evidence_type="metadata",
            value="invalid status",
            suggested_status="invalid-license-status",
            confidence=0.2,
            collected_at=datetime(2026, 6, 7, 9, 0, tzinfo=timezone.utc),
        )

        with pytest.raises(sqlite3.IntegrityError):
            repositories.save_collection_result(
                query=SearchQuery(keyword="rollback", mode=ExecutionMode.COLLECT),
                source=SourceKind.COMMON_CRAWL,
                document=Document(title="Rollback Report", doi="10.1234/rollback"),
                document_url=DocumentUrl(
                    url="https://example.org/rollback.pdf",
                    canonical_url="https://example.org/rollback.pdf",
                    source=SourceKind.COMMON_CRAWL,
                    discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                    url_type=UrlType.PDF,
                    confidence=0.8,
                ),
                evidence=[invalid_evidence],
            )

        assert _table_count(repositories.connection, "queries") == 0
        assert _table_count(repositories.connection, "sources") == 0
        assert _table_count(repositories.connection, "documents") == 0
        assert _table_count(repositories.connection, "document_urls") == 0
        assert _table_count(repositories.connection, "license_evidence") == 0
    finally:
        repositories.close()


def _table_count(connection: sqlite3.Connection, table_name: str) -> int:
    row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    return int(row[0])
