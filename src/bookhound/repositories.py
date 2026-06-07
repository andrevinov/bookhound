from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import sqlite3
from typing import Any

from bookhound.models import (
    Document,
    DocumentUrl,
    DownloadStatus,
    LicenseEvidence,
    SearchQuery,
    SourceKind,
)


@dataclass(frozen=True)
class CollectionSaveResult:
    query_id: int
    source_id: int
    document_id: int
    document_url_id: int
    license_evidence_ids: list[int]


class RepositorySet:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.queries = QueryRepository(connection)
        self.sources = SourceRepository(connection)
        self.documents = DocumentRepository(connection)
        self.document_urls = DocumentUrlRepository(connection)
        self.license_evidence = LicenseEvidenceRepository(connection)
        self.downloads = DownloadRepository(connection)
        self.events = EventRepository(connection)

    def close(self) -> None:
        self.connection.close()

    def save_collection_result(
        self,
        query: SearchQuery,
        source: SourceKind,
        document: Document,
        document_url: DocumentUrl,
        evidence: list[LicenseEvidence],
    ) -> CollectionSaveResult:
        try:
            self.connection.execute("BEGIN")
            query_id = self.queries.create(query, parameters={}, commit=False)
            source_id = self.sources.upsert(source, commit=False)
            document_id = self.documents.upsert(document, commit=False)
            document_url_id = self.document_urls.upsert(
                document_id=document_id,
                source_id=source_id,
                document_url=document_url,
                metadata={},
                commit=False,
            )
            evidence_ids = [
                self.license_evidence.add(
                    document_id=document_id,
                    document_url_id=document_url_id,
                    evidence=item,
                    metadata={},
                    commit=False,
                )
                for item in evidence
            ]
        except Exception:
            self.connection.rollback()
            raise

        self.connection.commit()
        return CollectionSaveResult(
            query_id=query_id,
            source_id=source_id,
            document_id=document_id,
            document_url_id=document_url_id,
            license_evidence_ids=evidence_ids,
        )


class QueryRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create(
        self,
        query: SearchQuery,
        parameters: dict[str, Any],
        *,
        commit: bool = True,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO queries (
                keyword,
                mode,
                variants_json,
                parameters_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                query.keyword,
                query.mode.value,
                _to_json(query.variants),
                _to_json(parameters),
                _format_datetime(query.created_at),
            ),
        )
        _commit_if_requested(self.connection, commit)
        return int(cursor.lastrowid)


class SourceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def upsert(
        self,
        source: SourceKind,
        *,
        config: dict[str, Any] | None = None,
        quota_state: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> int:
        self.connection.execute(
            """
            INSERT INTO sources (name, enabled, config_json, quota_state_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                enabled = excluded.enabled,
                config_json = excluded.config_json,
                quota_state_json = excluded.quota_state_json,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                source.value,
                1,
                _to_json(config or {}),
                _to_json(quota_state or {}),
            ),
        )
        _commit_if_requested(self.connection, commit)
        return _required_id(
            self.connection,
            "SELECT id FROM sources WHERE name = ?",
            (source.value,),
        )


class DocumentRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def upsert(self, document: Document, *, commit: bool = True) -> int:
        existing = self._find_existing(document)
        if existing is None:
            cursor = self.connection.execute(
                """
                INSERT INTO documents (
                    title,
                    authors_json,
                    doi,
                    isbn,
                    year,
                    language,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document.title,
                    _to_json(document.authors),
                    document.doi,
                    document.isbn,
                    document.year,
                    document.language,
                    _to_json(document.metadata),
                ),
            )
            _commit_if_requested(self.connection, commit)
            return int(cursor.lastrowid)

        document_id, metadata_json = existing
        merged_metadata = _merge_json_object(metadata_json, document.metadata)
        self.connection.execute(
            """
            UPDATE documents
            SET title = ?,
                authors_json = ?,
                doi = COALESCE(?, doi),
                isbn = COALESCE(?, isbn),
                year = COALESCE(?, year),
                language = COALESCE(?, language),
                metadata_json = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (
                document.title,
                _to_json(document.authors),
                document.doi,
                document.isbn,
                document.year,
                document.language,
                _to_json(merged_metadata),
                document_id,
            ),
        )
        _commit_if_requested(self.connection, commit)
        return int(document_id)

    def _find_existing(self, document: Document) -> tuple[int, str] | None:
        if document.doi:
            row = self.connection.execute(
                "SELECT id, metadata_json FROM documents WHERE doi = ?",
                (document.doi,),
            ).fetchone()
            if row is not None:
                return int(row[0]), str(row[1])

        if document.isbn:
            row = self.connection.execute(
                "SELECT id, metadata_json FROM documents WHERE isbn = ?",
                (document.isbn,),
            ).fetchone()
            if row is not None:
                return int(row[0]), str(row[1])

        return None


class DocumentUrlRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def upsert(
        self,
        document_id: int,
        source_id: int,
        document_url: DocumentUrl,
        metadata: dict[str, Any],
        *,
        commit: bool = True,
    ) -> int:
        existing = self.connection.execute(
            """
            SELECT id, metadata_json
            FROM document_urls
            WHERE canonical_url = ?
            """,
            (document_url.canonical_url,),
        ).fetchone()

        if existing is None:
            cursor = self.connection.execute(
                """
                INSERT INTO document_urls (
                    document_id,
                    source_id,
                    url,
                    canonical_url,
                    url_type,
                    discovery_method,
                    confidence,
                    http_status,
                    discovered_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    source_id,
                    document_url.url,
                    document_url.canonical_url,
                    document_url.url_type.value,
                    document_url.discovery_method.value,
                    document_url.confidence,
                    document_url.http_status,
                    _format_datetime(document_url.discovered_at),
                    _to_json(metadata),
                ),
            )
            _commit_if_requested(self.connection, commit)
            return int(cursor.lastrowid)

        document_url_id, metadata_json = existing
        merged_metadata = _merge_json_object(str(metadata_json), metadata)
        self.connection.execute(
            """
            UPDATE document_urls
            SET document_id = ?,
                source_id = ?,
                url = ?,
                url_type = ?,
                discovery_method = ?,
                confidence = ?,
                http_status = COALESCE(?, http_status),
                metadata_json = ?
            WHERE id = ?
            """,
            (
                document_id,
                source_id,
                document_url.url,
                document_url.url_type.value,
                document_url.discovery_method.value,
                document_url.confidence,
                document_url.http_status,
                _to_json(merged_metadata),
                document_url_id,
            ),
        )
        _commit_if_requested(self.connection, commit)
        return int(document_url_id)


class LicenseEvidenceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(
        self,
        document_id: int,
        document_url_id: int,
        evidence: LicenseEvidence,
        metadata: dict[str, Any],
        *,
        commit: bool = True,
    ) -> int:
        suggested_status = _enum_or_raw_value(evidence.suggested_status)
        cursor = self.connection.execute(
            """
            INSERT INTO license_evidence (
                document_id,
                document_url_id,
                source,
                evidence_type,
                value,
                suggested_status,
                confidence,
                collected_at,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                document_url_id,
                evidence.source,
                evidence.evidence_type,
                evidence.value,
                suggested_status,
                evidence.confidence,
                _format_datetime(evidence.collected_at),
                _to_json(metadata),
            ),
        )
        _commit_if_requested(self.connection, commit)
        return int(cursor.lastrowid)


class DownloadRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(
        self,
        document_id: int,
        document_url_id: int,
        local_path: str,
        status: DownloadStatus,
        *,
        sha256: str | None = None,
        size_bytes: int | None = None,
        license_evidence_id: int | None = None,
        error: str | None = None,
        downloaded_at: datetime | None = None,
        commit: bool = True,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO downloads (
                document_id,
                document_url_id,
                license_evidence_id,
                local_path,
                sha256,
                size_bytes,
                status,
                error,
                downloaded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                document_url_id,
                license_evidence_id,
                local_path,
                sha256,
                size_bytes,
                status.value,
                error,
                _format_datetime(downloaded_at) if downloaded_at else None,
            ),
        )
        _commit_if_requested(self.connection, commit)
        return int(cursor.lastrowid)


class EventRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(
        self,
        event_type: str,
        entity_type: str | None,
        entity_id: int | None,
        message: str | None,
        metadata: dict[str, Any],
        *,
        commit: bool = True,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO events (
                event_type,
                entity_type,
                entity_id,
                message,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_type,
                entity_type,
                entity_id,
                message,
                _to_json(metadata),
            ),
        )
        _commit_if_requested(self.connection, commit)
        return int(cursor.lastrowid)


def _to_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _merge_json_object(existing_json: str, new_values: dict[str, Any]) -> dict[str, Any]:
    existing = json.loads(existing_json) if existing_json else {}
    if not isinstance(existing, dict):
        existing = {}
    return {**existing, **new_values}


def _format_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _commit_if_requested(connection: sqlite3.Connection, commit: bool) -> None:
    if commit:
        connection.commit()


def _required_id(
    connection: sqlite3.Connection,
    statement: str,
    parameters: tuple[Any, ...],
) -> int:
    row = connection.execute(statement, parameters).fetchone()
    if row is None:
        raise RuntimeError("Expected repository row was not found.")
    return int(row[0])


def _enum_or_raw_value(value: Any) -> Any:
    return getattr(value, "value", value)
