from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import sqlite3

from bookhound.models import CrawlJobStatus


@dataclass(frozen=True)
class CrawlJob:
    id: int
    keyword: str
    status: CrawlJobStatus
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
                CrawlJobStatus.PENDING.value,
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
            WHERE status = ?
              AND (next_run_at IS NULL OR next_run_at <= ?)
            ORDER BY priority DESC, created_at ASC, id ASC
            LIMIT 1
            """,
            (CrawlJobStatus.PENDING.value, now_text),
        ).fetchone()
        if row is None:
            return None

        cursor = self.connection.execute(
            """
            UPDATE crawl_jobs
            SET status = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
              AND status = ?
              AND (next_run_at IS NULL OR next_run_at <= ?)
            """,
            (
                CrawlJobStatus.RUNNING.value,
                int(row[0]),
                CrawlJobStatus.PENDING.value,
                now_text,
            ),
        )
        self.connection.commit()
        if cursor.rowcount != 1:
            return None

        return CrawlJob(
            id=int(row[0]),
            keyword=str(row[1]),
            status=CrawlJobStatus.RUNNING,
            priority=int(row[3]),
            retries=int(row[4]),
            parameters=_json_object(str(row[5])),
            next_run_at=row[6],
        )

    def mark_retryable(self, job_id: int, *, next_run_at: datetime) -> int:
        self.connection.execute(
            """
            UPDATE crawl_jobs
            SET status = ?,
                retries = retries + 1,
                next_run_at = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
              AND status = ?
            """,
            (
                CrawlJobStatus.PENDING.value,
                _format_datetime(next_run_at),
                job_id,
                CrawlJobStatus.RUNNING.value,
            ),
        )
        self.connection.commit()
        row = self.connection.execute(
            """
            SELECT retries
            FROM crawl_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        return int(row[0]) if row is not None else 0


def _json_object(value: str) -> dict[str, object]:
    loaded = json.loads(value) if value else {}
    if isinstance(loaded, dict):
        return loaded
    return {}


def _format_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
