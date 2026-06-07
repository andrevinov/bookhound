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
