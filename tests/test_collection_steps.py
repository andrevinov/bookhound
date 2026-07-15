import json
from pathlib import Path
import sqlite3

import pytest

from bookhound.database import get_schema_version, initialize_database
from bookhound.models import DiscoveryMethod, ExecutionMode, SearchQuery, SourceKind
from bookhound.repositories import RepositorySet


@pytest.mark.revised
def test_database_initializes_collection_steps_schema(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "bookhound.sqlite3")

    try:
        assert "collection_steps" in _table_names(connection)
        assert {
            "id",
            "query_id",
            "variant_label",
            "variant_query",
            "source",
            "discovery_method",
            "status",
            "started_at",
            "completed_at",
            "candidate_count",
            "error_count",
            "errors_json",
            "metadata_json",
        }.issubset(_column_names(connection, "collection_steps"))
        assert _has_foreign_key(
            connection,
            table_name="collection_steps",
            from_column="query_id",
            referenced_table="queries",
            referenced_column="id",
        )
    finally:
        connection.close()


@pytest.mark.revised
def test_collection_step_repository_records_running_completed_and_failed_steps(
    tmp_path: Path,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))

    try:
        query_id = repositories.queries.create(
            SearchQuery(
                keyword="machine learning",
                mode=ExecutionMode.COLLECT,
                variants=['"machine learning"'],
            ),
            parameters={},
        )

        running_step_id = repositories.collection_steps.create_running(
            query_id=query_id,
            variant_label="quoted",
            variant_query='"machine learning"',
            source=SourceKind.GOOGLE,
            discovery_method=DiscoveryMethod.API,
            metadata={"rate_limit_key": "source:google"},
        )
        completed_step_id = repositories.collection_steps.create_running(
            query_id=query_id,
            variant_label="quoted",
            variant_query='"machine learning"',
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            metadata={"rate_limit_key": "source:common_crawl"},
        )
        repositories.collection_steps.mark_completed(
            completed_step_id,
            candidate_count=2,
            errors=[],
            metadata={"duration_ms": 42},
        )
        failed_step_id = repositories.collection_steps.record_failed(
            query_id=query_id,
            variant_label="quoted",
            variant_query='"machine learning"',
            source=SourceKind.SITEMAP,
            discovery_method=DiscoveryMethod.SITEMAP,
            candidate_count=0,
            errors=["availability: Sitemap timed out."],
            metadata={"duration_ms": 13},
        )

        rows = _collection_step_rows(repositories.connection)

        assert [row["id"] for row in rows] == [
            running_step_id,
            completed_step_id,
            failed_step_id,
        ]
        assert [row["status"] for row in rows] == [
            "running",
            "completed",
            "failed",
        ]
        assert [row["source"] for row in rows] == [
            "google",
            "common_crawl",
            "sitemap",
        ]
        assert [row["discovery_method"] for row in rows] == [
            "api",
            "public_index",
            "sitemap",
        ]
        assert [row["candidate_count"] for row in rows] == [0, 2, 0]
        assert [row["error_count"] for row in rows] == [0, 0, 1]
        assert [row["errors"] for row in rows] == [
            [],
            [],
            ["availability: Sitemap timed out."],
        ]
        assert [row["metadata"] for row in rows] == [
            {"rate_limit_key": "source:google"},
            {
                "duration_ms": 42,
                "rate_limit_key": "source:common_crawl",
            },
            {"duration_ms": 13},
        ]
        assert all(row["started_at"] for row in rows)
        assert rows[0]["completed_at"] is None
        assert rows[1]["completed_at"] is not None
        assert rows[2]["completed_at"] is not None
    finally:
        repositories.close()


def test_collection_steps_are_linked_to_query_with_foreign_key(
    tmp_path: Path,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))

    try:
        with pytest.raises(sqlite3.IntegrityError):
            repositories.collection_steps.create_running(
                query_id=999_999,
                variant_label="quoted",
                variant_query='"missing query"',
                source=SourceKind.GOOGLE,
                discovery_method=DiscoveryMethod.API,
                metadata={},
            )
    finally:
        repositories.close()


def test_v2_database_is_migrated_to_collection_steps_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    _create_minimal_v2_database(database_path)

    connection = initialize_database(database_path)

    try:
        assert get_schema_version(connection) == 3
        assert "collection_steps" in _table_names(connection)
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(2,), (3,)]
    finally:
        connection.close()


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row[0] for row in rows}


def _column_names(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _has_foreign_key(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    from_column: str,
    referenced_table: str,
    referenced_column: str,
) -> bool:
    foreign_keys = connection.execute(
        f"PRAGMA foreign_key_list({table_name})"
    ).fetchall()
    for row in foreign_keys:
        if (
            row[2] == referenced_table
            and row[3] == from_column
            and row[4] == referenced_column
        ):
            return True
    return False


def _collection_step_rows(
    connection: sqlite3.Connection,
) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT
            id,
            query_id,
            variant_label,
            variant_query,
            source,
            discovery_method,
            status,
            started_at,
            completed_at,
            candidate_count,
            error_count,
            errors_json,
            metadata_json
        FROM collection_steps
        ORDER BY id
        """
    ).fetchall()
    return [
        {
            "id": int(row[0]),
            "query_id": int(row[1]),
            "variant_label": row[2],
            "variant_query": row[3],
            "source": row[4],
            "discovery_method": row[5],
            "status": row[6],
            "started_at": row[7],
            "completed_at": row[8],
            "candidate_count": int(row[9]),
            "error_count": int(row[10]),
            "errors": json.loads(row[11]),
            "metadata": json.loads(row[12]),
        }
        for row in rows
    ]


def _create_minimal_v2_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                )
            );

            INSERT INTO schema_migrations (version) VALUES (2);

            CREATE TABLE queries (
                id INTEGER PRIMARY KEY,
                keyword TEXT NOT NULL,
                mode TEXT NOT NULL,
                variants_json TEXT NOT NULL DEFAULT '[]',
                parameters_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                )
            );
            """
        )
        connection.commit()
    finally:
        connection.close()
