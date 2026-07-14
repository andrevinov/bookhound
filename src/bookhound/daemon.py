from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Protocol

from bookhound.jobs import CrawlJobRepository
from bookhound.models import CrawlJobStatus, DownloadStatus


class JobExecutor(Protocol):
    def execute_job(self, keyword: str) -> None:
        raise NotImplementedError


class DownloadWorkflow(Protocol):
    def run_pending_downloads(self, *, interactive: bool) -> DownloadStatus:
        raise NotImplementedError


Clock = Callable[[], datetime]


@dataclass(frozen=True)
class DaemonConfig:
    lock_path: Path
    retry_delay_seconds: int = 300
    stale_lock_after_seconds: int | None = None


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
        clock: Clock | None = None,
    ) -> None:
        self.connection = connection
        self.config = config
        self.executor = executor
        self.download_workflow = download_workflow
        self.clock = clock or _utc_now
        self.jobs = CrawlJobRepository(connection)

    def run_once(self) -> DaemonRunResult:
        now = self.clock()
        if self.config.lock_path.exists():
            if _is_stale_lock(
                self.config.lock_path,
                stale_after_seconds=self.config.stale_lock_after_seconds,
                now=now,
            ):
                self.config.lock_path.unlink(missing_ok=True)
                _add_event(
                    self.connection,
                    event_type="daemon.stale_lock_recovered",
                    message="Recovered stale daemon lock.",
                    metadata={},
                )
            else:
                _add_locked_event(self.connection)
                return DaemonRunResult(locked=True)

        self.config.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if not _acquire_lock(self.config.lock_path):
            _add_locked_event(self.connection)
            return DaemonRunResult(locked=True)

        try:
            job = None
            if self.executor is not None:
                job = self.jobs.claim_next_pending(now=now)
                if job is not None:
                    try:
                        self.executor.execute_job(job.keyword)
                    except Exception as error:
                        retry_at = now + timedelta(
                            seconds=self.config.retry_delay_seconds,
                        )
                        retries = self.jobs.mark_retryable(
                            job.id,
                            next_run_at=retry_at,
                        )
                        _add_event(
                            self.connection,
                            event_type="daemon.job_failed",
                            message="Daemon job failed and was scheduled for retry.",
                            metadata={
                                "error": str(error),
                                "job_id": job.id,
                                "keyword": job.keyword,
                                "retries": retries,
                            },
                        )
                        raise
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
            SET status = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (CrawlJobStatus.COMPLETED.value, job_id),
        )
        self.connection.commit()


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


def _add_locked_event(connection: sqlite3.Connection) -> None:
    _add_event(
        connection,
        event_type="daemon.locked",
        message="Skipped daemon run because the lock is held.",
        metadata={},
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_stale_lock(
    lock_path: Path,
    *,
    stale_after_seconds: int | None,
    now: datetime,
) -> bool:
    if stale_after_seconds is None:
        return False

    try:
        lock_stat = lock_path.stat()
    except FileNotFoundError:
        return False

    lock_mtime = datetime.fromtimestamp(lock_stat.st_mtime, tz=timezone.utc)
    lock_age = now - lock_mtime
    return lock_age.total_seconds() >= stale_after_seconds


def _acquire_lock(lock_path: Path) -> bool:
    try:
        if isinstance(lock_path, os.PathLike):
            file_descriptor = os.open(
                os.fspath(lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as lock_file:
                lock_file.write("running")
        else:
            lock_path.write_text("running", encoding="utf-8")
    except FileExistsError:
        return False
    return True
