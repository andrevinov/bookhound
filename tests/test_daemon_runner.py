# Consolidated test module. See docs/audit-tests/03-centralize-test-files.md.
from __future__ import annotations

# Consolidated from test_crawl_job_status_domain.py

from datetime import datetime, timedelta, timezone
import sqlite3
from pathlib import Path

import pytest

import bookhound.models as models
from bookhound.daemon import DaemonConfig, DaemonRunner
from bookhound.database import initialize_database
from bookhound.jobs import CrawlJobRepository


@pytest.mark.revised
def test_crawl_job_status_values_are_domain_enum() -> None:
    assert {
        status.value
        for status in models.CrawlJobStatus
    } == {
        "pending",
        "running",
        "completed",
    }
    assert models.CrawlJobStatus.PENDING.value == "pending"
    assert models.CrawlJobStatus.RUNNING.value == "running"
    assert models.CrawlJobStatus.COMPLETED.value == "completed"


@pytest.mark.revised
def test_claim_next_pending_returns_constrained_status(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "bookhound.sqlite3")
    jobs = CrawlJobRepository(connection)
    now = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)
    expected_id = jobs.create("typed job status")

    selected = jobs.claim_next_pending(now=now)
    stored_status = connection.execute(
        "SELECT status FROM crawl_jobs WHERE id = ?",
        (expected_id,),
    ).fetchone()[0]

    assert selected is not None
    assert selected.id == expected_id
    assert selected.status is models.CrawlJobStatus.RUNNING
    assert stored_status == models.CrawlJobStatus.RUNNING.value


@pytest.mark.revised
def test_retryable_transition_uses_constrained_status(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "bookhound.sqlite3")
    jobs = CrawlJobRepository(connection)
    now = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)
    job_id = jobs.create("retryable typed status")
    selected = jobs.claim_next_pending(now=now)

    retries = jobs.mark_retryable(
        job_id,
        next_run_at=now + timedelta(minutes=5),
    )
    row = connection.execute(
        "SELECT status, retries FROM crawl_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()

    assert selected is not None
    assert selected.status is models.CrawlJobStatus.RUNNING
    assert retries == 1
    assert row == (models.CrawlJobStatus.PENDING.value, 1)


@pytest.mark.revised
def test_daemon_completion_uses_constrained_status(
    tmp_path: Path,
    recording_job_executor_factory,
) -> None:
    connection = initialize_database(tmp_path / "bookhound.sqlite3")
    CrawlJobRepository(connection).create("complete typed status")
    executor = recording_job_executor_factory()
    runner = DaemonRunner(
        connection=connection,
        executor=executor,
        config=DaemonConfig(lock_path=tmp_path / "bookhound.lock"),
    )

    result = runner.run_once()
    stored_status = connection.execute(
        "SELECT status FROM crawl_jobs"
    ).fetchone()[0]

    assert result.locked is False
    assert executor.keywords == ["complete typed status"]
    assert stored_status == models.CrawlJobStatus.COMPLETED.value


@pytest.mark.revised
def test_database_rejects_unsupported_crawl_job_status(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "bookhound.sqlite3")

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO crawl_jobs (keyword, status)
            VALUES (?, ?)
            """,
            ("invalid status", "misspelled"),
        )


# Consolidated from test_daemon_deterministic_time.py

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from bookhound.daemon import DaemonConfig, DaemonRunner
from bookhound.database import initialize_database
from bookhound.jobs import CrawlJobRepository


@pytest.mark.revised
def test_retry_schedule_uses_injected_clock_exactly(
    tmp_path: Path,
    failing_job_executor_factory,
    fixed_clock_factory,
    format_utc_datetime_helper,
) -> None:
    fixed_now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
    retry_delay_seconds = 90
    connection = initialize_database(tmp_path / "bookhound.sqlite3")
    job_id = CrawlJobRepository(connection).create(
        "deterministic retry",
        next_run_at=fixed_now,
    )
    executor = failing_job_executor_factory(RuntimeError("deterministic failure"))
    runner = DaemonRunner(
        connection=connection,
        executor=executor,
        config=DaemonConfig(
            lock_path=tmp_path / "bookhound.lock",
            retry_delay_seconds=retry_delay_seconds,
        ),
        clock=fixed_clock_factory(fixed_now),
    )

    with pytest.raises(RuntimeError, match="deterministic failure"):
        runner.run_once()

    job = connection.execute(
        "SELECT status, retries, next_run_at FROM crawl_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    event = connection.execute(
        "SELECT event_type, metadata_json FROM events"
    ).fetchone()

    assert executor.keywords == ["deterministic retry"]
    assert job == (
        "pending",
        1,
        format_utc_datetime_helper(
            fixed_now + timedelta(seconds=retry_delay_seconds)
        ),
    )
    assert event[0] == "daemon.job_failed"
    assert json.loads(event[1]) == {
        "error": "deterministic failure",
        "job_id": job_id,
        "keyword": "deterministic retry",
        "retries": 1,
    }


@pytest.mark.revised
def test_stale_lock_at_threshold_uses_injected_clock(
    tmp_path: Path,
    fixed_clock_factory,
    recording_job_executor_factory,
    write_lock_with_mtime_helper,
) -> None:
    fixed_now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
    stale_after_seconds = 60
    connection = initialize_database(tmp_path / "bookhound.sqlite3")
    CrawlJobRepository(connection).create("threshold stale lock")
    lock_path = tmp_path / "bookhound.lock"
    write_lock_with_mtime_helper(
        lock_path,
        fixed_now - timedelta(seconds=stale_after_seconds),
    )
    executor = recording_job_executor_factory()
    runner = DaemonRunner(
        connection=connection,
        executor=executor,
        config=DaemonConfig(
            lock_path=lock_path,
            stale_lock_after_seconds=stale_after_seconds,
        ),
        clock=fixed_clock_factory(fixed_now),
    )

    result = runner.run_once()

    events = connection.execute(
        "SELECT event_type FROM events ORDER BY id"
    ).fetchall()
    job_status = connection.execute("SELECT status FROM crawl_jobs").fetchone()[0]

    assert result.locked is False
    assert executor.keywords == ["threshold stale lock"]
    assert job_status == "completed"
    assert [row[0] for row in events] == [
        "daemon.stale_lock_recovered",
        "daemon.completed",
    ]
    assert lock_path.exists() is False


@pytest.mark.revised
def test_stale_lock_below_threshold_uses_injected_clock(
    tmp_path: Path,
    fixed_clock_factory,
    recording_job_executor_factory,
    write_lock_with_mtime_helper,
) -> None:
    fixed_now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
    stale_after_seconds = 60
    connection = initialize_database(tmp_path / "bookhound.sqlite3")
    CrawlJobRepository(connection).create("fresh lock")
    lock_path = tmp_path / "bookhound.lock"
    write_lock_with_mtime_helper(
        lock_path,
        fixed_now - timedelta(seconds=stale_after_seconds - 1),
    )
    executor = recording_job_executor_factory()
    runner = DaemonRunner(
        connection=connection,
        executor=executor,
        config=DaemonConfig(
            lock_path=lock_path,
            stale_lock_after_seconds=stale_after_seconds,
        ),
        clock=fixed_clock_factory(fixed_now),
    )

    result = runner.run_once()

    event = connection.execute(
        "SELECT event_type, message FROM events"
    ).fetchone()
    job_status = connection.execute("SELECT status FROM crawl_jobs").fetchone()[0]

    assert result.locked is True
    assert executor.keywords == []
    assert job_status == "pending"
    assert event == ("daemon.locked", "Skipped daemon run because the lock is held.")
    assert lock_path.exists() is True


# Consolidated from test_daemon_failure_recovery.py

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from bookhound.daemon import DaemonConfig, DaemonRunner
from bookhound.database import initialize_database
from bookhound.jobs import CrawlJobRepository


class NoopParent:
    def mkdir(self, *, parents: bool, exist_ok: bool) -> None:
        return None


class RacingLockPath:
    parent = NoopParent()

    def __init__(self) -> None:
        self.write_attempts = 0
        self.unlink_attempts = 0

    def exists(self) -> bool:
        return False

    def write_text(self, value: str, *, encoding: str) -> int:
        self.write_attempts += 1
        raise FileExistsError("Lock was created by another daemon.")

    def unlink(self, *, missing_ok: bool) -> None:
        self.unlink_attempts += 1


@dataclass
class SingleRowCursor:
    row: tuple[object, ...] | None

    def fetchone(self) -> tuple[object, ...] | None:
        return self.row


class InterleavingConnection:
    def __init__(self, database_path: Path, job_id: int) -> None:
        self.connection = sqlite3.connect(database_path)
        self.database_path = database_path
        self.job_id = job_id
        self.changed_after_select = False

    def execute(self, sql: str, parameters: tuple[object, ...] = ()):
        cursor = self.connection.execute(sql, parameters)
        if (
            not self.changed_after_select
            and "SELECT id, keyword, status, priority, retries" in sql
        ):
            row = cursor.fetchone()
            if row is None:
                return SingleRowCursor(None)

            with sqlite3.connect(self.database_path) as other_connection:
                other_connection.execute(
                    "UPDATE crawl_jobs SET status = 'running' WHERE id = ?",
                    (self.job_id,),
                )
                other_connection.commit()

            self.changed_after_select = True
            return SingleRowCursor(row)
        return cursor

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


@pytest.mark.revised
def test_executor_failure_schedules_retry_and_removes_lock(
    tmp_path: Path,
    failing_job_executor_factory,
    parse_utc_datetime_helper,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    connection = initialize_database(database_path)
    job_id = CrawlJobRepository(connection).create("resilient daemon")
    lock_path = tmp_path / "bookhound.lock"
    executor = failing_job_executor_factory(RuntimeError("source timeout"))
    started_at = datetime.now(timezone.utc)
    runner = DaemonRunner(
        connection=connection,
        executor=executor,
        config=DaemonConfig(lock_path=lock_path, retry_delay_seconds=300),
    )

    with pytest.raises(RuntimeError, match="source timeout"):
        runner.run_once()

    job = connection.execute(
        "SELECT status, retries, next_run_at FROM crawl_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    event = connection.execute(
        "SELECT event_type, message, metadata_json FROM events"
    ).fetchone()
    retry_at = parse_utc_datetime_helper(str(job[2]))

    assert executor.keywords == ["resilient daemon"]
    assert job[0] == "pending"
    assert job[1] == 1
    assert retry_at > started_at
    assert CrawlJobRepository(connection).claim_next_pending(now=started_at) is None
    assert lock_path.exists() is False
    assert event[0] == "daemon.job_failed"
    assert event[1] == "Daemon job failed and was scheduled for retry."
    assert json.loads(event[2]) == {
        "error": "source timeout",
        "job_id": job_id,
        "keyword": "resilient daemon",
        "retries": 1,
    }


@pytest.mark.revised
def test_stale_lock_is_recovered_before_running_job(
    tmp_path: Path,
    recording_job_executor_factory,
    write_lock_with_mtime_helper,
) -> None:
    connection = initialize_database(tmp_path / "bookhound.sqlite3")
    CrawlJobRepository(connection).create("stale lock recovery")
    lock_path = tmp_path / "bookhound.lock"
    write_lock_with_mtime_helper(
        lock_path,
        datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    executor = recording_job_executor_factory()
    runner = DaemonRunner(
        connection=connection,
        executor=executor,
        config=DaemonConfig(lock_path=lock_path, stale_lock_after_seconds=60),
    )

    result = runner.run_once()

    events = connection.execute(
        "SELECT event_type FROM events ORDER BY id"
    ).fetchall()
    job_status = connection.execute("SELECT status FROM crawl_jobs").fetchone()[0]

    assert result.locked is False
    assert executor.keywords == ["stale lock recovery"]
    assert job_status == "completed"
    assert [row[0] for row in events] == [
        "daemon.stale_lock_recovered",
        "daemon.completed",
    ]
    assert lock_path.exists() is False


@pytest.mark.revised
def test_lock_creation_race_is_treated_as_locked(
    tmp_path: Path,
    recording_job_executor_factory,
) -> None:
    connection = initialize_database(tmp_path / "bookhound.sqlite3")
    CrawlJobRepository(connection).create("racing daemon")
    executor = recording_job_executor_factory()
    lock_path = RacingLockPath()
    runner = DaemonRunner(
        connection=connection,
        executor=executor,
        config=DaemonConfig(lock_path=lock_path),
    )

    result = runner.run_once()

    event = connection.execute(
        "SELECT event_type, message FROM events"
    ).fetchone()
    job_status = connection.execute("SELECT status FROM crawl_jobs").fetchone()[0]

    assert result.locked is True
    assert executor.keywords == []
    assert job_status == "pending"
    assert lock_path.write_attempts == 1
    assert lock_path.unlink_attempts == 0
    assert event == ("daemon.locked", "Skipped daemon run because the lock is held.")


@pytest.mark.revised
def test_claim_next_pending_ignores_job_claimed_after_selection(tmp_path: Path) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    connection = initialize_database(database_path)
    job_id = CrawlJobRepository(connection).create("single owner")
    connection.close()
    interleaving_connection = InterleavingConnection(database_path, job_id)
    try:
        jobs = CrawlJobRepository(interleaving_connection)

        selected = jobs.claim_next_pending(now=datetime.now(timezone.utc))
    finally:
        interleaving_connection.close()

    with sqlite3.connect(database_path) as verification_connection:
        row = verification_connection.execute(
            "SELECT status, retries FROM crawl_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()

    assert selected is None
    assert row == ("running", 0)


# Consolidated from test_daemon_runtime_flow.py

from pathlib import Path
import sqlite3

from typer.testing import CliRunner

import bookhound.cli as cli
from bookhound.database import initialize_database
from bookhound.jobs_daemon_export import CrawlJobRepository


def test_daemon_run_once_claims_pending_job_collects_candidates_and_completes_job(
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
    job_id = _create_job(database_path, "machine learning")
    monkeypatch.setattr(
        cli,
        "build_http_client",
        lambda settings: sitemap_http_client_factory(
            robots_content=b"Sitemap: https://example.org/sitemap.xml",
            sitemap_content=b"""
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url>
                <loc>https://example.org/reports/machine-learning.pdf</loc>
                <lastmod>2026-07-04</lastmod>
              </url>
            </urlset>
            """
        ),
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["--config", str(config_path), "daemon", "run-once"],
    )

    assert result.exit_code == 0
    assert "Daemon run completed" in result.stdout
    assert f"job: {job_id}" in result.stdout

    with sqlite3.connect(database_path) as connection:
        job = connection.execute(
            "SELECT keyword, status FROM crawl_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        document = connection.execute(
            "SELECT title, metadata_json FROM documents"
        ).fetchone()
        document_url = connection.execute(
            "SELECT url, canonical_url, discovery_method FROM document_urls"
        ).fetchone()
        events = connection.execute(
            "SELECT event_type FROM events ORDER BY id"
        ).fetchall()

    assert job == ("machine learning", "completed")
    assert document[0] == "machine-learning.pdf"
    assert "2026-07-04" in document[1]
    assert document_url == (
        "https://example.org/reports/machine-learning.pdf",
        "https://example.org/reports/machine-learning.pdf",
        "sitemap",
    )
    assert [row[0] for row in events] == [
        "collect.completed",
        "daemon.completed",
    ]


def test_daemon_run_once_collect_flow_does_not_download_pdfs(
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
    _create_job(database_path, "download safety")
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
        ["--config", str(config_path), "daemon", "run-once"],
    )

    assert result.exit_code == 0
    with sqlite3.connect(database_path) as connection:
        downloads = connection.execute("SELECT COUNT(*) FROM downloads").fetchone()

    assert downloads[0] == 0
    assert pdf_directory.exists() is False


def _create_job(database_path: Path, keyword: str) -> int:
    connection = initialize_database(database_path)
    try:
        return CrawlJobRepository(connection).create(keyword)
    finally:
        connection.close()
