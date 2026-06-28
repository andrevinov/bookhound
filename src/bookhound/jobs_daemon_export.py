from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Protocol

from bookhound.models import DownloadStatus


@dataclass(frozen=True)
class CrawlJob:
    id: int
    keyword: str
    status: str
    priority: int
    retries: int
    parameters: dict[str, object]
    next_run_at: str | None


class CrawlJobRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create(
        self,
        keyword: str,
        *,
        priority: int = 0,
        parameters: dict[str, object] | None = None,
        next_run_at: datetime | None = None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO crawl_jobs (
                keyword,
                status,
                priority,
                parameters_json,
                next_run_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                keyword,
                "pending",
                priority,
                json.dumps(parameters or {}, sort_keys=True, separators=(",", ":")),
                _format_datetime(next_run_at) if next_run_at else None,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def claim_next_pending(self, *, now: datetime) -> CrawlJob | None:
        now_text = _format_datetime(now)
        row = self.connection.execute(
            """
            SELECT id, keyword, status, priority, retries, parameters_json, next_run_at
            FROM crawl_jobs
            WHERE status = 'pending'
              AND (next_run_at IS NULL OR next_run_at <= ?)
            ORDER BY priority DESC, created_at ASC, id ASC
            LIMIT 1
            """,
            (now_text,),
        ).fetchone()
        if row is None:
            return None

        self.connection.execute(
            """
            UPDATE crawl_jobs
            SET status = 'running',
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (int(row[0]),),
        )
        self.connection.commit()
        return CrawlJob(
            id=int(row[0]),
            keyword=str(row[1]),
            status="running",
            priority=int(row[3]),
            retries=int(row[4]),
            parameters=_json_object(str(row[5])),
            next_run_at=row[6],
        )


class JobExecutor(Protocol):
    def execute_job(self, keyword: str) -> None:
        raise NotImplementedError


class DownloadWorkflow(Protocol):
    def run_pending_downloads(self, *, interactive: bool) -> DownloadStatus:
        raise NotImplementedError


@dataclass(frozen=True)
class DaemonConfig:
    lock_path: Path


@dataclass(frozen=True)
class DaemonRunResult:
    locked: bool = False
    job_id: int | None = None
    download_status: DownloadStatus | None = None


class DaemonRunner:
    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        config: DaemonConfig,
        executor: JobExecutor | None = None,
        download_workflow: DownloadWorkflow | None = None,
    ) -> None:
        self.connection = connection
        self.config = config
        self.executor = executor
        self.download_workflow = download_workflow
        self.jobs = CrawlJobRepository(connection)

    def run_once(self) -> DaemonRunResult:
        if self.config.lock_path.exists():
            _add_event(
                self.connection,
                event_type="daemon.locked",
                message="Skipped daemon run because the lock is held.",
                metadata={},
            )
            return DaemonRunResult(locked=True)

        self.config.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.lock_path.write_text("running", encoding="utf-8")
        try:
            job = None
            if self.executor is not None:
                job = self.jobs.claim_next_pending(now=datetime.now(timezone.utc))
                if job is not None:
                    self.executor.execute_job(job.keyword)
                    self._mark_job_completed(job.id)

            download_status = None
            if self.download_workflow is not None:
                download_status = self.download_workflow.run_pending_downloads(
                    interactive=False,
                )

            _add_event(
                self.connection,
                event_type="daemon.completed",
                message="Daemon run completed.",
                metadata={
                    "job_id": job.id if job else None,
                    "download_status": download_status.value if download_status else None,
                },
            )
            return DaemonRunResult(
                locked=False,
                job_id=job.id if job else None,
                download_status=download_status,
            )
        finally:
            self.config.lock_path.unlink(missing_ok=True)

    def _mark_job_completed(self, job_id: int) -> None:
        self.connection.execute(
            """
            UPDATE crawl_jobs
            SET status = 'completed',
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (job_id,),
        )
        self.connection.commit()


class ExportService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def export_csv(self, path: Path) -> None:
        rows = self._rows()
        with path.open("w", encoding="utf-8", newline="") as export_file:
            writer = csv.DictWriter(
                export_file,
                fieldnames=[
                    "title",
                    "doi",
                    "url",
                    "canonical_url",
                    "source",
                    "license_status",
                    "metadata",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        **row,
                        "metadata": json.dumps(row["metadata"], sort_keys=True),
                    }
                )

    def export_jsonl(self, path: Path) -> None:
        rows = self._rows()
        with path.open("w", encoding="utf-8") as export_file:
            for row in rows:
                export_file.write(json.dumps(row, sort_keys=True) + "\n")

    def _rows(self) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT
                documents.title,
                documents.doi,
                document_urls.url,
                document_urls.canonical_url,
                sources.name,
                documents.metadata_json,
                (
                    SELECT license_evidence.suggested_status
                    FROM license_evidence
                    WHERE license_evidence.document_id = documents.id
                    ORDER BY license_evidence.collected_at DESC, license_evidence.id DESC
                    LIMIT 1
                ) AS license_status
            FROM documents
            JOIN document_urls ON document_urls.document_id = documents.id
            JOIN sources ON sources.id = document_urls.source_id
            ORDER BY documents.id, document_urls.id
            """
        ).fetchall()
        return [
            {
                "title": str(row[0]),
                "doi": str(row[1]) if row[1] is not None else "",
                "url": str(row[2]),
                "canonical_url": str(row[3]),
                "source": str(row[4]),
                "license_status": str(row[6]) if row[6] is not None else "unknown",
                "metadata": _json_object(str(row[5])),
            }
            for row in rows
        ]


def _add_event(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    message: str,
    metadata: dict[str, object],
) -> None:
    connection.execute(
        """
        INSERT INTO events (event_type, message, metadata_json)
        VALUES (?, ?, ?)
        """,
        (
            event_type,
            message,
            json.dumps(metadata, sort_keys=True, separators=(",", ":")),
        ),
    )
    connection.commit()


def _json_object(value: str) -> dict[str, object]:
    loaded = json.loads(value) if value else {}
    if isinstance(loaded, dict):
        return loaded
    return {}


def _format_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
