# Consolidated test module. See docs/audit-tests/03-centralize-test-files.md.
# Consolidated from test_database_schema.py

from pathlib import Path
import sqlite3

import pytest

from bookhound.database import get_schema_version, initialize_database


EXPECTED_TABLES = {
    "schema_migrations",
    "queries",
    "sources",
    "documents",
    "document_urls",
    "license_evidence",
    "crawl_jobs",
    "downloads",
    "events",
}

@pytest.mark.revised
def test_initialize_database_creates_expected_tables_and_indexes(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "bookhound.sqlite3")

    try:
        assert EXPECTED_TABLES.issubset(_table_names(connection))
        assert _has_index_starting_with(connection, "documents", ("doi",))
        assert _has_index_starting_with(connection, "documents", ("isbn",))
        assert _has_index_starting_with(connection, "document_urls", ("canonical_url",))
        assert _has_index_starting_with(connection, "downloads", ("sha256",))
        assert _has_index_starting_with(connection, "queries", ("created_at",))
        assert _has_index_starting_with(connection, "events", ("created_at",))
    finally:
        connection.close()


@pytest.mark.revised
def test_license_evidence_document_url_id_is_indexed(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "bookhound.sqlite3")

    try:
        assert _has_index_starting_with(
            connection,
            "license_evidence",
            ("document_url_id",),
        )
    finally:
        connection.close()


@pytest.mark.revised
def test_initialize_database_is_idempotent_and_tracks_schema_version(tmp_path: Path) -> None:
    db_path = tmp_path / "bookhound.sqlite3"
    first_connection = initialize_database(db_path)
    first_version = get_schema_version(first_connection)
    first_connection.close()

    second_connection = initialize_database(db_path)

    try:
        assert get_schema_version(second_connection) == first_version
        migration_versions = second_connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert migration_versions == [(first_version,)]
    finally:
        second_connection.close()


@pytest.mark.revised
def test_database_constraints_prevent_obvious_duplicates(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "bookhound.sqlite3")

    try:
        source_id = _insert_source(connection, "common_crawl")

        with pytest.raises(sqlite3.IntegrityError):
            _insert_source(connection, "common_crawl")

        document_id = _insert_document(
            connection,
            title="Machine Learning Notes",
            doi="10.1234/bookhound",
            isbn="9780000000001",
        )

        with pytest.raises(sqlite3.IntegrityError):
            _insert_document(
                connection,
                title="Duplicate DOI",
                doi="10.1234/bookhound",
                isbn="9780000000002",
            )

        with pytest.raises(sqlite3.IntegrityError):
            _insert_document(
                connection,
                title="Duplicate ISBN",
                doi="10.1234/other",
                isbn="9780000000001",
            )

        _insert_document_url(
            connection,
            document_id=document_id,
            source_id=source_id,
            url="https://example.org/notes.pdf",
            canonical_url="https://example.org/notes.pdf",
        )

        with pytest.raises(sqlite3.IntegrityError):
            _insert_document_url(
                connection,
                document_id=document_id,
                source_id=source_id,
                url="https://example.org/notes.pdf?utm_source=test",
                canonical_url="https://example.org/notes.pdf",
            )
    finally:
        connection.close()


@pytest.mark.revised
def test_initialize_database_enables_wal_mode_for_file_database(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "bookhound.sqlite3")

    try:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode.lower() == "wal"
    finally:
        connection.close()


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row[0] for row in rows}


def _has_index_starting_with(
    connection: sqlite3.Connection,
    table_name: str,
    expected_columns: tuple[str, ...],
) -> bool:
    for index in connection.execute(f"PRAGMA index_list({table_name})").fetchall():
        index_name = index[1]
        indexed_columns = tuple(
            row[2] for row in connection.execute(f"PRAGMA index_info({index_name})").fetchall()
        )
        if indexed_columns[: len(expected_columns)] == expected_columns:
            return True
    return False


def _insert_source(connection: sqlite3.Connection, name: str) -> int:
    cursor = connection.execute(
        """
        INSERT INTO sources (name, enabled, config_json, quota_state_json)
        VALUES (?, ?, ?, ?)
        """,
        (name, 1, "{}", "{}"),
    )
    connection.commit()
    return int(cursor.lastrowid)


def _insert_document(
    connection: sqlite3.Connection,
    title: str,
    doi: str,
    isbn: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO documents (title, doi, isbn, metadata_json)
        VALUES (?, ?, ?, ?)
        """,
        (title, doi, isbn, "{}"),
    )
    connection.commit()
    return int(cursor.lastrowid)


def _insert_document_url(
    connection: sqlite3.Connection,
    document_id: int,
    source_id: int,
    url: str,
    canonical_url: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO document_urls (
            document_id,
            source_id,
            url,
            canonical_url,
            url_type,
            confidence
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (document_id, source_id, url, canonical_url, "pdf", 1.0),
    )
    connection.commit()
    return int(cursor.lastrowid)


# Consolidated from test_repositories.py

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
def test_document_upsert_does_not_duplicate_doi(
    tmp_path: Path,
    count_rows_helper,
) -> None:
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
        assert count_rows_helper(repositories.connection, "documents") == 1
        assert row[0] == "Updated Machine Learning Notes"
        assert json.loads(row[1]) == ["Ada Lovelace", "Alan Turing"]
        assert json.loads(row[2]) == {
            "source_count": 1,
            "last_seen_source": "common_crawl",
        }
    finally:
        repositories.close()


@pytest.mark.revised
def test_document_url_upsert_merges_metadata_without_losing_history(
    tmp_path: Path,
    count_rows_helper,
) -> None:
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
        assert count_rows_helper(repositories.connection, "document_urls") == 1
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
    count_rows_helper,
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
        assert count_rows_helper(repositories.connection, "queries") == 1
        assert count_rows_helper(repositories.connection, "sources") == 1
        assert count_rows_helper(repositories.connection, "documents") == 1
        assert count_rows_helper(repositories.connection, "document_urls") == 1
        assert count_rows_helper(repositories.connection, "license_evidence") == 1
    finally:
        repositories.close()


@pytest.mark.revised
def test_save_collection_result_rolls_back_when_later_insert_fails(
    tmp_path: Path,
    count_rows_helper,
) -> None:
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

        assert count_rows_helper(repositories.connection, "queries") == 0
        assert count_rows_helper(repositories.connection, "sources") == 0
        assert count_rows_helper(repositories.connection, "documents") == 0
        assert count_rows_helper(repositories.connection, "document_urls") == 0
        assert count_rows_helper(repositories.connection, "license_evidence") == 0
    finally:
        repositories.close()


@pytest.mark.revised
def test_repositories_read_collected_candidates_evidence_and_export_row_count(
    tmp_path: Path,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))

    try:
        source_id = repositories.sources.upsert(SourceKind.COMMON_CRAWL)
        document_id = repositories.documents.upsert(
            Document(title="Collected Reading Report")
        )
        document_url_id = repositories.document_urls.upsert(
            document_id=document_id,
            source_id=source_id,
            document_url=DocumentUrl(
                url="https://example.org/reading.pdf",
                canonical_url="https://example.org/reading.pdf",
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                url_type=UrlType.PDF,
                confidence=0.8,
                discovered_at=datetime(2026, 6, 8, 10, 0, tzinfo=timezone.utc),
            ),
            metadata={},
        )
        first_evidence_id = repositories.license_evidence.add(
            document_id=document_id,
            document_url_id=document_url_id,
            evidence=LicenseEvidence(
                source="html",
                evidence_type="license",
                value="unknown",
                suggested_status=LicenseStatus.UNKNOWN,
                confidence=0.2,
                collected_at=datetime(2026, 6, 8, 11, 0, tzinfo=timezone.utc),
            ),
            metadata={},
        )
        second_evidence_id = repositories.license_evidence.add(
            document_id=document_id,
            document_url_id=document_url_id,
            evidence=LicenseEvidence(
                source="unpaywall",
                evidence_type="api_license",
                value="cc-by",
                suggested_status=LicenseStatus.ALLOWED,
                confidence=0.9,
                collected_at=datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc),
            ),
            metadata={},
        )

        candidates = repositories.document_urls.list_collected_candidates(
            repositories.license_evidence
        )
        evidence_entries = repositories.license_evidence.list_for_document_url(
            document_id=document_id,
            document_url_id=document_url_id,
        )

        assert repositories.document_urls.count_export_rows() == 1
        assert len(candidates) == 1
        assert candidates[0].title == "Collected Reading Report"
        assert candidates[0].url == "https://example.org/reading.pdf"
        assert candidates[0].discovery_method is DiscoveryMethod.PUBLIC_INDEX
        assert candidates[0].query == "collected"
        assert candidates[0].metadata["document_id"] == document_id
        assert candidates[0].metadata["document_url_id"] == document_url_id
        assert [entry["id"] for entry in evidence_entries] == [
            second_evidence_id,
            first_evidence_id,
        ]
        assert [
            entry["evidence"].suggested_status
            for entry in candidates[0].metadata["license_evidence"]
        ] == [LicenseStatus.ALLOWED, LicenseStatus.UNKNOWN]
    finally:
        repositories.close()


# Consolidated from test_deduplication.py

import pytest

from bookhound.deduplication import compare_documents
from bookhound.models import Document

@pytest.mark.revised
def test_same_doi_merges_documents_with_high_confidence() -> None:
    result = compare_documents(
        Document(
            title="Machine Learning Notes",
            authors=["Ada Lovelace"],
            doi="10.1234/bookhound",
        ),
        Document(
            title="Updated Machine Learning Notes",
            authors=["Alan Turing"],
            doi="10.1234/bookhound",
        ),
    )

    assert result.should_merge is True
    assert result.confidence == 1.0
    assert result.reason == "same_doi"


@pytest.mark.revised
def test_same_isbn_merges_documents_with_high_confidence() -> None:
    result = compare_documents(
        Document(title="Statistics Textbook", isbn="9780000000001"),
        Document(title="Statistics Textbook Revised", isbn="9780000000001"),
    )

    assert result.should_merge is True
    assert result.confidence == 1.0
    assert result.reason == "same_isbn"


@pytest.mark.revised
def test_same_canonical_url_merges_candidates() -> None:
    result = compare_documents(
        Document(title="Open Access Report"),
        Document(title="Open Access Report"),
        left_canonical_url="https://example.org/report.pdf",
        right_canonical_url="https://example.org/report.pdf",
    )

    assert result.should_merge is True
    assert result.confidence == 0.95
    assert result.reason == "same_canonical_url"


@pytest.mark.revised
def test_similar_title_without_authors_does_not_merge_aggressively() -> None:
    result = compare_documents(
        Document(title="Introduction to Machine Learning"),
        Document(title="Introduction to Machine Learning Notes"),
    )

    assert result.should_merge is False
    assert result.confidence < 0.8
    assert result.reason == "insufficient_evidence"


@pytest.mark.revised
def test_same_title_authors_and_year_merges_as_fallback() -> None:
    result = compare_documents(
        Document(
            title="Introduction to Machine Learning",
            authors=["Ada Lovelace", "Alan Turing"],
            year=2026,
        ),
        Document(
            title="introduction to machine learning",
            authors=["Alan Turing", "Ada Lovelace"],
            year=2026,
        ),
    )

    assert result.should_merge is True
    assert result.confidence == 0.85
    assert result.reason == "same_title_authors_year"


@pytest.mark.revised
def test_same_hash_after_download_merges_documents_even_with_different_urls() -> None:
    result = compare_documents(
        Document(title="First discovered title"),
        Document(title="Second discovered title"),
        left_canonical_url="https://example.org/first.pdf",
        right_canonical_url="https://mirror.example.net/second.pdf",
        left_sha256="a" * 64,
        right_sha256="a" * 64,
    )

    assert result.should_merge is True
    assert result.confidence == 1.0
    assert result.reason == "same_sha256"


@pytest.mark.revised
def test_conflicting_strong_identifiers_do_not_merge() -> None:
    result = compare_documents(
        Document(title="Same Title", doi="10.1234/one", isbn="9780000000001"),
        Document(title="Same Title", doi="10.1234/two", isbn="9780000000002"),
    )

    assert result.should_merge is False
    assert result.confidence == 0.0
    assert result.reason == "conflicting_identifiers"
