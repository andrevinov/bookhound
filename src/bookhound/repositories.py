from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
import sqlite3
import time
from typing import Any, Iterable

from bookhound.discovery_pipeline import (
    DiscoveryPipelineResult,
    DiscoveryStepResult,
)
from bookhound.models import (
    DiscoveryMethod,
    Document,
    DocumentUrl,
    DownloadStatus,
    ExecutionMode,
    LicenseEvidence,
    LicenseStatus,
    PersistedDownloadCandidate,
    PersistedLicenseEvidence,
    RawCandidate,
    SearchQuery,
    SourceKind,
    UrlType,
)
from bookhound.query_planner import PlannedQueryVariant, QueryPlan
from bookhound.url_normalization import canonicalize_url, is_direct_pdf_url


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollectSummary:
    total: int
    new: int
    updated: int
    duplicate: int


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
        self.collection_steps = CollectionStepRepository(connection)

    def close(self) -> None:
        self.connection.close()

    def begin_collection(self, query_plan: QueryPlan) -> int:
        variants = [variant.query for variant in query_plan.variants]
        try:
            self.connection.execute("BEGIN")
            query_id = self.queries.create(
                SearchQuery(
                    keyword=query_plan.keyword,
                    mode=ExecutionMode.COLLECT,
                    variants=variants,
                ),
                parameters={},
                commit=False,
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

        return query_id

    def save_collection_step(
        self,
        query_id: int,
        step: DiscoveryStepResult,
    ) -> CollectSummary:
        started_at = time.perf_counter()
        candidates = _unique_candidates_by_canonical_url(step.candidates)
        summary = CollectSummary(
            total=len(candidates),
            new=0,
            updated=0,
            duplicate=0,
        )
        step_id: int | None = None

        try:
            self.connection.execute("BEGIN")
            if step.status == "failed":
                step_id = self.collection_steps.record_failed(
                    query_id=query_id,
                    variant_label=step.variant.label,
                    variant_query=step.variant.query,
                    source=step.source,
                    discovery_method=step.discovery_method,
                    candidate_count=len(candidates),
                    errors=list(step.errors),
                    metadata={},
                    commit=False,
                )
            elif step.status == "completed":
                step_id = self.collection_steps.create_running(
                    query_id=query_id,
                    variant_label=step.variant.label,
                    variant_query=step.variant.query,
                    source=step.source,
                    discovery_method=step.discovery_method,
                    metadata={},
                    commit=False,
                )
                for candidate in candidates:
                    summary = self._save_discovery_candidate(
                        candidate,
                        summary,
                        collection_query=step.variant.query,
                    )
                self.collection_steps.mark_completed(
                    step_id,
                    candidate_count=len(candidates),
                    errors=list(step.errors),
                    metadata={},
                    commit=False,
                )
            else:
                raise ValueError(
                    f"Unsupported collection step status: {step.status}"
                )
            self.connection.commit()
        except Exception as error:
            self.connection.rollback()
            logger.error(
                "Collection step persistence failed.",
                exc_info=True,
                extra={
                    "event": "collect.step.persistence.failed",
                    "keyword": step.query_plan.keyword,
                    "query_id": query_id,
                    "step_id": step_id,
                    "query_variant_label": step.variant.label,
                    "query": step.variant.query,
                    "source": step.source.value,
                    "discovery_method": step.discovery_method.value,
                    "status": step.status,
                    "candidate_count": len(candidates),
                    "new": summary.new,
                    "updated": summary.updated,
                    "duplicate": summary.duplicate,
                    "error_count": len(step.errors),
                    "duration_ms": _duration_ms(started_at),
                    "error": str(error),
                },
            )
            raise

        logger.info(
            "Collection step persistence completed.",
            extra={
                "event": "collect.step.persistence.completed",
                "keyword": step.query_plan.keyword,
                "query_id": query_id,
                "step_id": step_id,
                "query_variant_label": step.variant.label,
                "query": step.variant.query,
                "source": step.source.value,
                "discovery_method": step.discovery_method.value,
                "status": step.status,
                "candidate_count": len(candidates),
                "new": summary.new,
                "updated": summary.updated,
                "duplicate": summary.duplicate,
                "error_count": len(step.errors),
                "duration_ms": _duration_ms(started_at),
            },
        )
        return summary

    def finish_collection(
        self,
        *,
        query_id: int,
        keyword: str,
        summary: CollectSummary,
        errors: list[str],
        events: list[dict[str, object]],
    ) -> None:
        try:
            self.connection.execute("BEGIN")
            self.events.add(
                event_type="collect.completed",
                entity_type="query",
                entity_id=query_id,
                message=(
                    f"Collected {summary.total} "
                    f"{_candidate_count_label(summary.total)} "
                    f"for {keyword}."
                ),
                metadata={
                    "keyword": keyword,
                    "new": summary.new,
                    "updated": summary.updated,
                    "duplicate": summary.duplicate,
                    "errors": list(errors),
                },
                commit=False,
            )
            for event in events:
                self.events.add(
                    event_type=_event_type(event),
                    entity_type="query",
                    entity_id=query_id,
                    message=_event_message(event),
                    metadata=_event_metadata(
                        event,
                        keyword=keyword,
                    ),
                    commit=False,
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def save_discovery_result(
        self,
        result: DiscoveryPipelineResult,
    ) -> CollectSummary:
        started_at = time.perf_counter()
        variants = [variant.query for variant in result.query_plan.variants]
        summary = CollectSummary(
            total=len(result.candidates),
            new=0,
            updated=0,
            duplicate=0,
        )

        try:
            self.connection.execute("BEGIN")
            query_id = self.queries.create(
                SearchQuery(
                    keyword=result.query_plan.keyword,
                    mode=ExecutionMode.COLLECT,
                    variants=variants,
                ),
                parameters={},
                commit=False,
            )

            collection_query = variants[0] if variants else result.query_plan.keyword
            for candidate in result.candidates:
                summary = self._save_discovery_candidate(
                    candidate,
                    summary,
                    collection_query=collection_query,
                )

            self.events.add(
                event_type="collect.completed",
                entity_type="query",
                entity_id=query_id,
                message=(
                    f"Collected {summary.total} "
                    f"{_candidate_count_label(summary.total)} "
                    f"for {result.query_plan.keyword}."
                ),
                metadata={
                    "keyword": result.query_plan.keyword,
                    "new": summary.new,
                    "updated": summary.updated,
                    "duplicate": summary.duplicate,
                    "errors": list(result.errors),
                },
                commit=False,
            )
            for event in result.events:
                self.events.add(
                    event_type=_event_type(event),
                    entity_type="query",
                    entity_id=query_id,
                    message=_event_message(event),
                    metadata=_event_metadata(
                        event,
                        keyword=result.query_plan.keyword,
                    ),
                    commit=False,
                )
            self.connection.commit()
        except Exception as error:
            self.connection.rollback()
            logger.error(
                "Collection persistence failed.",
                exc_info=True,
                extra={
                    "event": "collect.persistence.failed",
                    "keyword": result.query_plan.keyword,
                    "total": summary.total,
                    "new": summary.new,
                    "updated": summary.updated,
                    "duplicate": summary.duplicate,
                    "error_count": len(result.errors),
                    "duration_ms": _duration_ms(started_at),
                    "error": str(error),
                },
            )
            raise

        logger.info(
            "Collection persistence completed.",
            extra={
                "event": "collect.persistence.completed",
                "keyword": result.query_plan.keyword,
                "total": summary.total,
                "new": summary.new,
                "updated": summary.updated,
                "duplicate": summary.duplicate,
                "error_count": len(result.errors),
                "duration_ms": _duration_ms(started_at),
            },
        )
        return summary

    def _save_discovery_candidate(
        self,
        candidate: RawCandidate,
        summary: CollectSummary,
        *,
        collection_query: str,
    ) -> CollectSummary:
        document = _candidate_document(candidate)
        document_url = _candidate_document_url(candidate)
        existing_document_id = self.documents.find_existing_id(document)
        existing_document_url_id = self.document_urls.find_existing_id(
            document_url.canonical_url,
        )

        source_id = self.sources.upsert(candidate.source, commit=False)

        if existing_document_url_id is not None:
            self.document_urls.merge_source_occurrence(
                existing_document_url_id,
                _source_occurrence(candidate, collection_query=collection_query),
                commit=False,
            )
            return _increment_summary(summary, duplicate=1)

        document_id = self.documents.upsert(document, commit=False)
        document_url_id = self.document_urls.upsert(
            document_id=document_id,
            source_id=source_id,
            document_url=document_url,
            metadata=_candidate_document_url_metadata(
                candidate,
                collection_query=collection_query,
            ),
            commit=False,
        )
        for evidence, metadata in _candidate_license_evidence(candidate):
            self.license_evidence.add(
                document_id=document_id,
                document_url_id=document_url_id,
                evidence=evidence,
                metadata=metadata,
                commit=False,
            )

        if existing_document_id is None:
            return _increment_summary(summary, new=1)

        return _increment_summary(summary, updated=1)

    def save_collection_result(
        self,
        query: SearchQuery,
        source: SourceKind,
        document: Document,
        document_url: DocumentUrl,
        evidence: list[LicenseEvidence],
    ) -> CollectionSaveResult:
        result = _single_item_discovery_result(
            query=query,
            source=source,
            document=document,
            document_url=document_url,
            evidence=evidence,
        )
        self.save_discovery_result(result)
        row = self.connection.execute(
            """
            SELECT
                queries.id,
                sources.id,
                documents.id,
                document_urls.id
            FROM document_urls
            JOIN documents ON documents.id = document_urls.document_id
            JOIN sources ON sources.id = document_urls.source_id
            JOIN queries ON queries.keyword = ?
            WHERE document_urls.canonical_url = ?
            ORDER BY queries.id DESC
            LIMIT 1
            """,
            (query.keyword, document_url.canonical_url),
        ).fetchone()
        if row is None:
            raise RuntimeError("Expected saved collection result was not found.")

        evidence_rows = self.connection.execute(
            """
            SELECT id
            FROM license_evidence
            WHERE document_url_id = ? OR document_id = ?
            ORDER BY id
            """,
            (int(row[3]), int(row[2])),
        ).fetchall()
        return CollectionSaveResult(
            query_id=int(row[0]),
            source_id=int(row[1]),
            document_id=int(row[2]),
            document_url_id=int(row[3]),
            license_evidence_ids=[
                int(evidence_row[0]) for evidence_row in evidence_rows
            ],
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


class CollectionStepRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create_running(
        self,
        *,
        query_id: int,
        variant_label: str,
        variant_query: str,
        source: SourceKind,
        discovery_method: DiscoveryMethod,
        metadata: dict[str, Any],
        commit: bool = True,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO collection_steps (
                query_id,
                variant_label,
                variant_query,
                source,
                discovery_method,
                status,
                candidate_count,
                error_count,
                errors_json,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                query_id,
                variant_label,
                variant_query,
                source.value,
                discovery_method.value,
                "running",
                0,
                0,
                _to_json([]),
                _to_json(metadata),
            ),
        )
        _commit_if_requested(self.connection, commit)
        return int(cursor.lastrowid)

    def mark_completed(
        self,
        step_id: int,
        *,
        candidate_count: int,
        errors: list[str],
        metadata: dict[str, Any],
        commit: bool = True,
    ) -> None:
        self._mark_finished(
            step_id,
            status="completed",
            candidate_count=candidate_count,
            errors=errors,
            metadata=metadata,
            commit=commit,
        )

    def record_failed(
        self,
        *,
        query_id: int,
        variant_label: str,
        variant_query: str,
        source: SourceKind,
        discovery_method: DiscoveryMethod,
        candidate_count: int,
        errors: list[str],
        metadata: dict[str, Any],
        commit: bool = True,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO collection_steps (
                query_id,
                variant_label,
                variant_query,
                source,
                discovery_method,
                status,
                completed_at,
                candidate_count,
                error_count,
                errors_json,
                metadata_json
            )
            VALUES (
                ?, ?, ?, ?, ?, ?,
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                ?, ?, ?, ?
            )
            """,
            (
                query_id,
                variant_label,
                variant_query,
                source.value,
                discovery_method.value,
                "failed",
                candidate_count,
                len(errors),
                _to_json(errors),
                _to_json(metadata),
            ),
        )
        _commit_if_requested(self.connection, commit)
        return int(cursor.lastrowid)

    def _mark_finished(
        self,
        step_id: int,
        *,
        status: str,
        candidate_count: int,
        errors: list[str],
        metadata: dict[str, Any],
        commit: bool,
    ) -> None:
        row = self.connection.execute(
            """
            SELECT metadata_json
            FROM collection_steps
            WHERE id = ?
            """,
            (step_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Expected collection step was not found.")

        merged_metadata = _merge_json_object(row[0], metadata)
        self.connection.execute(
            """
            UPDATE collection_steps
            SET status = ?,
                completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                candidate_count = ?,
                error_count = ?,
                errors_json = ?,
                metadata_json = ?
            WHERE id = ?
            """,
            (
                status,
                candidate_count,
                len(errors),
                _to_json(errors),
                _to_json(merged_metadata),
                step_id,
            ),
        )
        _commit_if_requested(self.connection, commit)


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

    def find_existing_id(self, document: Document) -> int | None:
        existing = self._find_existing(document)
        return int(existing[0]) if existing is not None else None

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

    def find_existing_id(self, canonical_url: str) -> int | None:
        row = self.connection.execute(
            """
            SELECT id
            FROM document_urls
            WHERE canonical_url = ?
            """,
            (canonical_url,),
        ).fetchone()
        return int(row[0]) if row is not None else None

    def merge_source_occurrence(
        self,
        document_url_id: int,
        occurrence: dict[str, str],
        *,
        commit: bool = True,
    ) -> None:
        row = self.connection.execute(
            """
            SELECT
                document_urls.metadata_json,
                sources.name,
                document_urls.discovery_method
            FROM document_urls
            JOIN sources ON sources.id = document_urls.source_id
            WHERE document_urls.id = ?
            """,
            (document_url_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Expected document URL was not found.")

        merged_metadata = _merge_document_url_source_occurrence(
            existing_json=str(row[0]),
            existing_source=str(row[1]),
            existing_discovery_method=str(row[2]),
            new_occurrence=occurrence,
        )
        self.connection.execute(
            """
            UPDATE document_urls
            SET metadata_json = ?
            WHERE id = ?
            """,
            (_to_json(merged_metadata), document_url_id),
        )
        _commit_if_requested(self.connection, commit)

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

    def list_collected_candidates(
        self,
        license_evidence: LicenseEvidenceRepository,
    ) -> list[RawCandidate]:
        return [
            persisted.candidate.model_copy(
                update={
                    "metadata": {
                        **persisted.candidate.metadata,
                        "document_id": persisted.document_id,
                        "document_url_id": persisted.document_url_id,
                        "license_evidence": [
                            {"id": entry.id, "evidence": entry.evidence}
                            for entry in persisted.license_evidence
                        ],
                    }
                }
            )
            for persisted in self.list_persisted_download_candidates(
                license_evidence,
            )
        ]

    def list_persisted_download_candidates(
        self,
        license_evidence: LicenseEvidenceRepository,
    ) -> list[PersistedDownloadCandidate]:
        rows = self.connection.execute(
            """
            SELECT
                documents.id,
                document_urls.id,
                documents.title,
                document_urls.url,
                document_urls.canonical_url,
                sources.name,
                document_urls.discovery_method,
                document_urls.confidence,
                document_urls.discovered_at
            FROM document_urls
            JOIN documents ON documents.id = document_urls.document_id
            JOIN sources ON sources.id = document_urls.source_id
            ORDER BY document_urls.discovered_at, document_urls.id
            """
        ).fetchall()
        evidence_by_candidate = license_evidence.list_persisted_for_document_urls(
            (int(row[0]), int(row[1])) for row in rows
        )
        return [
            self._persisted_download_candidate_from_row(
                row,
                license_evidence=license_evidence,
                license_evidence_entries=evidence_by_candidate.get(
                    (int(row[0]), int(row[1])),
                    [],
                ),
            )
            for row in rows
        ]

    def find_persisted_download_candidate(
        self,
        *,
        canonical_url: str,
        candidate: RawCandidate,
        license_evidence: LicenseEvidenceRepository,
    ) -> PersistedDownloadCandidate | None:
        row = self.connection.execute(
            """
            SELECT
                documents.id,
                document_urls.id,
                documents.title,
                document_urls.url,
                document_urls.canonical_url,
                sources.name,
                document_urls.discovery_method,
                document_urls.confidence,
                document_urls.discovered_at
            FROM document_urls
            JOIN documents ON documents.id = document_urls.document_id
            JOIN sources ON sources.id = document_urls.source_id
            WHERE document_urls.canonical_url = ?
            """,
            (canonical_url,),
        ).fetchone()
        if row is None:
            return None

        return self._persisted_download_candidate_from_row(
            row,
            license_evidence=license_evidence,
            candidate=candidate,
        )

    def _persisted_download_candidate_from_row(
        self,
        row,
        *,
        license_evidence: LicenseEvidenceRepository,
        candidate: RawCandidate | None = None,
        license_evidence_entries: list[PersistedLicenseEvidence] | None = None,
    ) -> PersistedDownloadCandidate:
        document_id = int(row[0])
        document_url_id = int(row[1])
        persisted_candidate = RawCandidate(
            title=candidate.title if candidate else str(row[2]),
            url=str(row[3]),
            source=SourceKind(str(row[5])),
            discovery_method=_discovery_method_or_default(str(row[6])),
            query=candidate.query if candidate else "collected",
            score=candidate.score if candidate else row[7],
            discovered_at=candidate.discovered_at if candidate else str(row[8]),
            snippet=candidate.snippet if candidate else None,
            adapter_score=candidate.adapter_score if candidate else None,
            metadata=(
                _candidate_download_metadata(candidate)
                if candidate
                else {}
            ),
        )
        return PersistedDownloadCandidate(
            candidate=persisted_candidate,
            canonical_url=str(row[4]),
            document_id=document_id,
            document_url_id=document_url_id,
            license_evidence=(
                license_evidence_entries
                if license_evidence_entries is not None
                else license_evidence.list_persisted_for_document_url(
                    document_id=document_id,
                    document_url_id=document_url_id,
                )
            ),
        )

    def count_export_rows(self) -> int:
        row = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM documents
            JOIN document_urls ON document_urls.document_id = documents.id
            JOIN sources ON sources.id = document_urls.source_id
            """
        ).fetchone()
        return int(row[0])


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

    def list_for_document_url(
        self,
        *,
        document_id: int,
        document_url_id: int,
    ) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT
                id,
                source,
                evidence_type,
                value,
                suggested_status,
                confidence,
                collected_at
            FROM license_evidence
            WHERE document_url_id = ? OR document_id = ?
            ORDER BY collected_at DESC, id DESC
            """,
            (document_url_id, document_id),
        ).fetchall()
        return [
            {
                "id": int(row[0]),
                "evidence": LicenseEvidence(
                    source=str(row[1]),
                    evidence_type=str(row[2]),
                    value=str(row[3]),
                    suggested_status=LicenseStatus(str(row[4])),
                    confidence=row[5],
                    collected_at=str(row[6]),
                ),
            }
            for row in rows
        ]

    def list_persisted_for_document_url(
        self,
        *,
        document_id: int,
        document_url_id: int,
    ) -> list[PersistedLicenseEvidence]:
        return [
            PersistedLicenseEvidence(
                id=entry["id"] if isinstance(entry["id"], int) else None,
                evidence=entry["evidence"],
            )
            for entry in self.list_for_document_url(
                document_id=document_id,
                document_url_id=document_url_id,
            )
            if isinstance(entry.get("evidence"), LicenseEvidence)
        ]

    def list_persisted_for_document_urls(
        self,
        candidates: Iterable[tuple[int, int]],
    ) -> dict[tuple[int, int], list[PersistedLicenseEvidence]]:
        candidate_keys = list(dict.fromkeys(candidates))
        evidence_by_candidate = {key: [] for key in candidate_keys}
        if not candidate_keys:
            return evidence_by_candidate

        keys_by_document_id: dict[int, list[tuple[int, int]]] = {}
        keys_by_document_url_id: dict[int, list[tuple[int, int]]] = {}
        for document_id, document_url_id in candidate_keys:
            keys_by_document_id.setdefault(document_id, []).append(
                (document_id, document_url_id)
            )
            keys_by_document_url_id.setdefault(document_url_id, []).append(
                (document_id, document_url_id)
            )

        document_ids = sorted(keys_by_document_id)
        document_url_ids = sorted(keys_by_document_url_id)
        document_url_placeholders = ", ".join("?" for _ in document_url_ids)
        document_placeholders = ", ".join("?" for _ in document_ids)
        rows = self.connection.execute(
            f"""
            SELECT
                id,
                document_id,
                document_url_id,
                source,
                evidence_type,
                value,
                suggested_status,
                confidence,
                collected_at
            FROM license_evidence
            WHERE document_url_id IN ({document_url_placeholders})
               OR document_id IN ({document_placeholders})
            ORDER BY collected_at DESC, id DESC
            """,
            (*document_url_ids, *document_ids),
        ).fetchall()

        seen_evidence_ids = {key: set() for key in candidate_keys}
        for row in rows:
            entry = PersistedLicenseEvidence(
                id=int(row[0]),
                evidence=LicenseEvidence(
                    source=str(row[3]),
                    evidence_type=str(row[4]),
                    value=str(row[5]),
                    suggested_status=LicenseStatus(str(row[6])),
                    confidence=row[7],
                    collected_at=str(row[8]),
                ),
            )
            matching_keys: list[tuple[int, int]] = []
            if row[2] is not None:
                matching_keys.extend(keys_by_document_url_id.get(int(row[2]), []))
            if row[1] is not None:
                matching_keys.extend(keys_by_document_id.get(int(row[1]), []))
            for key in matching_keys:
                if entry.id in seen_evidence_ids[key]:
                    continue
                evidence_by_candidate[key].append(entry)
                seen_evidence_ids[key].add(entry.id)

        return evidence_by_candidate


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


def _event_type(event: dict[str, object]) -> str:
    event_type = event.get("event_type")
    if isinstance(event_type, str) and event_type.strip():
        return event_type
    return "discovery.operational_event"


def _event_message(event: dict[str, object]) -> str | None:
    message = event.get("message")
    if isinstance(message, str):
        return message
    return None


def _event_metadata(
    event: dict[str, object],
    *,
    keyword: str,
) -> dict[str, object]:
    metadata = event.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    event_metadata = {**metadata, "keyword": keyword}
    source = event.get("source")
    if isinstance(source, str) and source.strip():
        event_metadata["source"] = source

    return event_metadata


def _single_item_discovery_result(
    *,
    query: SearchQuery,
    source: SourceKind,
    document: Document,
    document_url: DocumentUrl,
    evidence: list[LicenseEvidence],
) -> DiscoveryPipelineResult:
    variants = [
        PlannedQueryVariant(label=f"variant_{index}", query=variant)
        for index, variant in enumerate(query.variants, start=1)
    ]
    if not variants:
        variants = [PlannedQueryVariant(label="keyword", query=query.keyword)]

    metadata: dict[str, object] = {
        **document.metadata,
        "canonical_url": document_url.canonical_url,
    }
    if document.authors:
        metadata["authors"] = document.authors
    if document.doi is not None:
        metadata["doi"] = document.doi
    if document.isbn is not None:
        metadata["isbn"] = document.isbn
    if document.year is not None:
        metadata["year"] = document.year
    if document.language is not None:
        metadata["language"] = document.language
    if evidence:
        metadata["license_evidence"] = [{"evidence": item} for item in evidence]

    return DiscoveryPipelineResult(
        query_plan=QueryPlan(keyword=query.keyword, variants=variants),
        candidates=[
            RawCandidate(
                title=document.title,
                url=document_url.url,
                source=source,
                discovery_method=document_url.discovery_method,
                query=variants[0].query,
                score=document_url.confidence,
                discovered_at=document_url.discovered_at,
                metadata=metadata,
            )
        ],
        errors=[],
    )


def _candidate_document(candidate: RawCandidate) -> Document:
    metadata = _candidate_document_metadata(candidate)
    return Document(
        title=candidate.title,
        authors=_metadata_string_list(metadata.get("authors")),
        doi=_metadata_optional_string(metadata.get("doi")),
        isbn=_metadata_optional_string(metadata.get("isbn")),
        year=_metadata_optional_int(metadata.get("year")),
        language=_metadata_optional_string(metadata.get("language")),
        metadata=metadata,
    )


def _candidate_document_metadata(candidate: RawCandidate) -> dict[str, object]:
    return {
        key: value
        for key, value in candidate.metadata.items()
        if key != "license_evidence"
    }


def _unique_candidates_by_canonical_url(
    candidates: Iterable[RawCandidate],
) -> list[RawCandidate]:
    candidates_by_canonical_url: dict[str, RawCandidate] = {}
    for candidate in candidates:
        canonical_url = _candidate_canonical_url(candidate)
        if canonical_url not in candidates_by_canonical_url:
            candidates_by_canonical_url[canonical_url] = candidate
    return list(candidates_by_canonical_url.values())


def _candidate_canonical_url(candidate: RawCandidate) -> str:
    canonical_url = candidate.metadata.get("canonical_url")
    if isinstance(canonical_url, str) and canonical_url.strip():
        return canonical_url
    return canonicalize_url(candidate.url)


def _candidate_document_url(candidate: RawCandidate) -> DocumentUrl:
    return DocumentUrl(
        url=candidate.url,
        canonical_url=_candidate_canonical_url(candidate),
        source=candidate.source,
        discovery_method=candidate.discovery_method,
        url_type=(
            UrlType.PDF if is_direct_pdf_url(candidate.url) else UrlType.LANDING_PAGE
        ),
        confidence=candidate.score,
        discovered_at=candidate.discovered_at,
    )


def _candidate_document_url_metadata(
    candidate: RawCandidate,
    *,
    collection_query: str,
) -> dict[str, object]:
    return {
        "query": collection_query,
        "score": candidate.score,
        "snippet": candidate.snippet,
    }


def _source_occurrence(
    candidate: RawCandidate,
    *,
    collection_query: str,
) -> dict[str, str]:
    query_variant_label = candidate.metadata.get("query_variant_label")
    if not isinstance(query_variant_label, str) or not query_variant_label.strip():
        query_variant_label = "quoted"

    return {
        "source": candidate.source.value,
        "discovery_method": candidate.discovery_method.value,
        "query_variant_label": query_variant_label,
        "query": collection_query,
    }


def _candidate_license_evidence(
    candidate: RawCandidate,
) -> list[tuple[LicenseEvidence, dict[str, Any]]]:
    entries = candidate.metadata.get("license_evidence", [])
    if not isinstance(entries, list):
        return []

    evidence_entries: list[tuple[LicenseEvidence, dict[str, Any]]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        evidence = entry.get("evidence")
        if not isinstance(evidence, LicenseEvidence):
            continue
        metadata = entry.get("metadata")
        evidence_entries.append(
            (evidence, metadata if isinstance(metadata, dict) else {})
        )
    return evidence_entries


def _candidate_download_metadata(candidate: RawCandidate) -> dict[str, Any]:
    persisted_keys = {
        "canonical_url",
        "document_id",
        "document_url_id",
        "license_evidence",
    }
    return {
        key: value
        for key, value in candidate.metadata.items()
        if key not in persisted_keys
    }


def _metadata_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    return [item for item in value if isinstance(item, str) and item.strip()]


def _metadata_optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _metadata_optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _increment_summary(
    summary: CollectSummary,
    *,
    new: int = 0,
    updated: int = 0,
    duplicate: int = 0,
) -> CollectSummary:
    return CollectSummary(
        total=summary.total,
        new=summary.new + new,
        updated=summary.updated + updated,
        duplicate=summary.duplicate + duplicate,
    )


def _candidate_count_label(count: int) -> str:
    if count == 1:
        return "candidate"
    return "candidates"


def _merge_json_object(existing_json: str, new_values: dict[str, Any]) -> dict[str, Any]:
    existing = json.loads(existing_json) if existing_json else {}
    if not isinstance(existing, dict):
        existing = {}
    return {**existing, **new_values}


def _merge_document_url_source_occurrence(
    *,
    existing_json: str,
    existing_source: str,
    existing_discovery_method: str,
    new_occurrence: dict[str, str],
) -> dict[str, Any]:
    existing = json.loads(existing_json) if existing_json else {}
    if not isinstance(existing, dict):
        existing = {}

    existing_occurrences = existing.get("source_occurrences")
    if not _valid_source_occurrences(existing_occurrences):
        existing_occurrences = [
            _stored_document_url_source_occurrence(
                existing,
                source=existing_source,
                discovery_method=existing_discovery_method,
            )
        ]

    return {
        **existing,
        "source_occurrences": _merge_source_occurrences(
            existing_occurrences,
            [new_occurrence],
        ),
    }


def _stored_document_url_source_occurrence(
    metadata: dict[str, Any],
    *,
    source: str,
    discovery_method: str,
) -> dict[str, str]:
    query = metadata.get("query")
    if not isinstance(query, str) or not query.strip():
        query = ""

    query_variant_label = metadata.get("query_variant_label")
    if not isinstance(query_variant_label, str) or not query_variant_label.strip():
        query_variant_label = "quoted"

    return {
        "source": source,
        "discovery_method": discovery_method,
        "query_variant_label": query_variant_label,
        "query": query,
    }


def _merge_source_occurrences(
    existing_occurrences: object,
    new_occurrences: object,
) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for occurrence in _valid_source_occurrences(existing_occurrences):
        key = _source_occurrence_key(occurrence)
        if key in seen:
            continue
        merged.append(occurrence)
        seen.add(key)
    for occurrence in _valid_source_occurrences(new_occurrences):
        key = _source_occurrence_key(occurrence)
        if key in seen:
            continue
        merged.append(occurrence)
        seen.add(key)
    return merged


def _valid_source_occurrences(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    occurrences: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        discovery_method = item.get("discovery_method")
        query_variant_label = item.get("query_variant_label")
        query = item.get("query")
        if not all(
            isinstance(field, str) and field.strip()
            for field in (source, discovery_method, query_variant_label, query)
        ):
            continue
        occurrences.append(
            {
                "source": source,
                "discovery_method": discovery_method,
                "query_variant_label": query_variant_label,
                "query": query,
            }
        )
    return occurrences


def _source_occurrence_key(
    occurrence: dict[str, str],
) -> tuple[str, str, str, str]:
    return (
        occurrence["source"],
        occurrence["discovery_method"],
        occurrence["query_variant_label"],
        occurrence["query"],
    )


def _format_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _duration_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


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


def _discovery_method_or_default(value: str) -> DiscoveryMethod:
    try:
        return DiscoveryMethod(value)
    except ValueError:
        return DiscoveryMethod.SITEMAP
