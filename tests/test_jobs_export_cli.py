# Consolidated test module. See docs/audit-tests/03-centralize-test-files.md.
# Consolidated from test_jobs_daemon_export.py

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bookhound.database import initialize_database
from bookhound.daemon import DaemonRunner as SplitDaemonRunner
from bookhound.export import ExportService as SplitExportService
from bookhound.jobs import CrawlJobRepository as SplitCrawlJobRepository
from bookhound.jobs_daemon_export import (
    CrawlJobRepository,
    DaemonConfig,
    DaemonRunner,
    ExportService,
)
from bookhound.models import (
    DownloadStatus,
    LicenseEvidence,
    LicenseStatus,
)
from bookhound.repositories import RepositorySet


class FakeDownloadWorkflow:
    def __init__(self, status: DownloadStatus) -> None:
        self.status = status
        self.calls: list[dict[str, object]] = []

    def run_pending_downloads(self, *, interactive: bool) -> DownloadStatus:
        self.calls.append({"interactive": interactive})
        return self.status


@pytest.mark.revised
def test_pending_job_is_selected_for_execution(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "bookhound.sqlite3")
    jobs = CrawlJobRepository(connection)
    now = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)

    jobs.create("low priority", priority=1)
    expected_id = jobs.create("high priority", priority=10)
    jobs.create(
        "future job",
        priority=99,
        next_run_at=now + timedelta(hours=1),
    )

    selected = jobs.claim_next_pending(now=now)

    assert selected is not None
    assert selected.id == expected_id
    assert selected.keyword == "high priority"
    assert selected.status == "running"
    assert connection.execute(
        "SELECT status FROM crawl_jobs WHERE id = ?",
        (expected_id,),
    ).fetchone()[0] == "running"


@pytest.mark.revised
def test_lock_prevents_two_concurrent_executions(
    tmp_path: Path,
    recording_job_executor_factory,
) -> None:
    connection = initialize_database(tmp_path / "bookhound.sqlite3")
    lock_path = tmp_path / "bookhound.lock"
    lock_path.write_text("already running", encoding="utf-8")
    executor = recording_job_executor_factory()
    runner = DaemonRunner(
        connection=connection,
        executor=executor,
        config=DaemonConfig(lock_path=lock_path),
    )

    result = runner.run_once()

    event = connection.execute(
        "SELECT event_type, message FROM events"
    ).fetchone()
    assert result.locked is True
    assert executor.keywords == []
    assert event == ("daemon.locked", "Skipped daemon run because the lock is held.")


@pytest.mark.revised
def test_daemon_does_not_download_unknown(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "bookhound.sqlite3")
    workflow = FakeDownloadWorkflow(status=DownloadStatus.BLOCKED)
    runner = DaemonRunner(
        connection=connection,
        download_workflow=workflow,
        config=DaemonConfig(lock_path=tmp_path / "bookhound.lock"),
    )

    result = runner.run_once()

    assert workflow.calls == [{"interactive": False}]
    assert result.download_status is DownloadStatus.BLOCKED


@pytest.mark.revised
def test_csv_and_jsonl_export_include_metadata_urls_and_license_status(
    tmp_path: Path,
    seed_document_url_factory,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    seeded = seed_document_url_factory(
        repositories,
        title="Open Report",
        url="https://example.org/open-report.pdf",
        doi="10.1234/open-report",
        confidence=0.95,
        document_metadata={"subject": "climate"},
    )
    repositories.license_evidence.add(
        document_id=seeded.document_id,
        document_url_id=seeded.document_url_id,
        evidence=LicenseEvidence(
            source="unpaywall",
            evidence_type="api_license",
            value="cc-by",
            suggested_status=LicenseStatus.ALLOWED,
            confidence=0.9,
        ),
        metadata={},
    )
    csv_path = tmp_path / "export.csv"
    jsonl_path = tmp_path / "export.jsonl"

    exporter = ExportService(repositories.connection)
    exporter.export_csv(csv_path)
    exporter.export_jsonl(jsonl_path)

    csv_rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8").splitlines()))
    jsonl_rows = [
        json.loads(line)
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
    ]
    expected = {
        "title": "Open Report",
        "doi": "10.1234/open-report",
        "url": "https://example.org/open-report.pdf",
        "canonical_url": "https://example.org/open-report.pdf",
        "source": "sitemap",
        "license_status": "allowed",
        "metadata": {"subject": "climate"},
    }

    assert csv_rows == [
        {
            **expected,
            "metadata": json.dumps(expected["metadata"], sort_keys=True),
        }
    ]
    assert jsonl_rows == [expected]


@pytest.mark.revised
def test_jsonl_export_streams_rows_without_fetchall(tmp_path: Path) -> None:
    export_path = tmp_path / "streamed-export.jsonl"
    exporter = ExportService(
        StreamingExportConnection(
            [
                _export_row(
                    title="Streamed JSONL Report",
                    url="https://example.org/streamed-jsonl.pdf",
                )
            ]
        )
    )

    exporter.export_jsonl(export_path)

    rows = [
        json.loads(line)
        for line in export_path.read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [
        {
            "title": "Streamed JSONL Report",
            "doi": "10.1234/streamed-export",
            "url": "https://example.org/streamed-jsonl.pdf",
            "canonical_url": "https://example.org/streamed-jsonl.pdf",
            "source": "sitemap",
            "license_status": "allowed",
            "metadata": {"subject": "climate"},
        }
    ]


def test_csv_export_streams_rows_without_fetchall(tmp_path: Path) -> None:
    export_path = tmp_path / "streamed-export.csv"
    exporter = ExportService(
        StreamingExportConnection(
            [
                _export_row(
                    title="Streamed CSV Report",
                    url="https://example.org/streamed-csv.pdf",
                )
            ]
        )
    )

    exporter.export_csv(export_path)

    rows = list(csv.DictReader(export_path.read_text(encoding="utf-8").splitlines()))
    assert rows == [
        {
            "title": "Streamed CSV Report",
            "doi": "10.1234/streamed-export",
            "url": "https://example.org/streamed-csv.pdf",
            "canonical_url": "https://example.org/streamed-csv.pdf",
            "source": "sitemap",
            "license_status": "allowed",
            "metadata": json.dumps({"subject": "climate"}, sort_keys=True),
        }
    ]


class StreamingExportConnection:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def execute(self, query: str) -> "NoFetchallCursor":
        return NoFetchallCursor(self.rows)


class NoFetchallCursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def fetchall(self):
        raise AssertionError("Export rows must be streamed instead of fetched eagerly.")


def _export_row(*, title: str, url: str) -> tuple[object, ...]:
    return (
        title,
        "10.1234/streamed-export",
        url,
        url,
        "sitemap",
        json.dumps({"subject": "climate"}),
        "allowed",
    )


@pytest.mark.revised
def test_split_job_daemon_and_export_modules_match_compatibility_exports() -> None:
    assert CrawlJobRepository is SplitCrawlJobRepository
    assert DaemonRunner is SplitDaemonRunner
    assert ExportService is SplitExportService


# Consolidated from test_jobs_daemon_export_cli.py

import json
from pathlib import Path

import pytest

from typer.testing import CliRunner

import bookhound.cli as cli
from bookhound.database import initialize_database
from bookhound.jobs_daemon_export import DaemonRunResult
from bookhound.models import (
    DiscoveryMethod,
    Document,
    DocumentUrl,
    SourceKind,
    UrlType,
)
from bookhound.repositories import RepositorySet


class FakeDaemonRunner:
    def __init__(self, result: DaemonRunResult) -> None:
        self.result = result
        self.calls = 0

    def run_once(self) -> DaemonRunResult:
        self.calls += 1
        return self.result


@pytest.mark.revised
def test_job_add_command_creates_pending_job(
    tmp_path: Path,
    write_minimal_paths_config_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"

    result = CliRunner().invoke(
        cli.app,
        [
            "--config",
            str(
                write_minimal_paths_config_factory(
                    tmp_path,
                    database_path=database_path,
                    pdf_directory=tmp_path / "pdfs",
                )
            ),
            "job",
            "add",
            "machine learning",
            "--priority",
            "7",
        ],
    )

    assert result.exit_code == 0
    assert "Created job 1" in result.stdout
    with initialize_database(database_path) as connection:
        job = connection.execute(
            """
            SELECT keyword, status, priority, parameters_json
            FROM crawl_jobs
            """
        ).fetchone()

    assert job == ("machine learning", "pending", 7, "{}")


@pytest.mark.revised
def test_daemon_run_once_command_invokes_daemon_runner(
    tmp_path: Path,
    monkeypatch,
    write_minimal_paths_config_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    runner = FakeDaemonRunner(DaemonRunResult(job_id=3))
    monkeypatch.setattr(cli, "build_daemon_runner", lambda repositories, settings: runner)

    result = CliRunner().invoke(
        cli.app,
        [
            "--config",
            str(
                write_minimal_paths_config_factory(
                    tmp_path,
                    database_path=database_path,
                    pdf_directory=tmp_path / "pdfs",
                )
            ),
            "daemon",
            "run-once",
        ],
    )

    assert result.exit_code == 0
    assert runner.calls == 1
    assert "Daemon run completed" in result.stdout
    assert "job: 3" in result.stdout


@pytest.mark.revised
def test_export_command_writes_jsonl_file(
    tmp_path: Path,
    write_minimal_paths_config_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    export_path = tmp_path / "export.jsonl"
    _save_export_document(database_path)

    result = CliRunner().invoke(
        cli.app,
        [
            "--config",
            str(
                write_minimal_paths_config_factory(
                    tmp_path,
                    database_path=database_path,
                    pdf_directory=tmp_path / "pdfs",
                )
            ),
            "export",
            "--format",
            "jsonl",
            "--output",
            str(export_path),
        ],
    )

    assert result.exit_code == 0
    assert "Exported 1 row" in result.stdout
    rows = [
        json.loads(line)
        for line in export_path.read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [
        {
            "title": "Exported Report",
            "doi": "",
            "url": "https://example.org/exported.pdf",
            "canonical_url": "https://example.org/exported.pdf",
            "source": "sitemap",
            "license_status": "unknown",
            "metadata": {"topic": "runtime"},
        }
    ]


@pytest.mark.revised
def test_export_command_writes_csv_file(
    tmp_path: Path,
    write_minimal_paths_config_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    export_path = tmp_path / "export.csv"
    _save_export_document(database_path)

    result = CliRunner().invoke(
        cli.app,
        [
            "--config",
            str(
                write_minimal_paths_config_factory(
                    tmp_path,
                    database_path=database_path,
                    pdf_directory=tmp_path / "pdfs",
                )
            ),
            "export",
            "--format",
            "csv",
            "--output",
            str(export_path),
        ],
    )

    assert result.exit_code == 0
    assert "Exported 1 row" in result.stdout
    assert "Exported Report" in export_path.read_text(encoding="utf-8")


def _save_export_document(database_path: Path) -> None:
    repositories = RepositorySet(initialize_database(database_path))
    try:
        source_id = repositories.sources.upsert(SourceKind.SITEMAP)
        document_id = repositories.documents.upsert(
            Document(title="Exported Report", metadata={"topic": "runtime"})
        )
        repositories.document_urls.upsert(
            document_id=document_id,
            source_id=source_id,
            document_url=DocumentUrl(
                url="https://example.org/exported.pdf",
                canonical_url="https://example.org/exported.pdf",
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                url_type=UrlType.PDF,
                confidence=0.9,
            ),
            metadata={},
        )
    finally:
        repositories.close()
