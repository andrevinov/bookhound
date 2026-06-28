import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bookhound.database import initialize_database
from bookhound.jobs_daemon_export import (
    CrawlJobRepository,
    DaemonConfig,
    DaemonRunner,
    ExportService,
)
from bookhound.models import (
    DiscoveryMethod,
    Document,
    DocumentUrl,
    DownloadStatus,
    LicenseEvidence,
    LicenseStatus,
    SourceKind,
    UrlType,
)
from bookhound.repositories import RepositorySet


class FakeJobExecutor:
    def __init__(self) -> None:
        self.keywords: list[str] = []

    def execute_job(self, keyword: str) -> None:
        self.keywords.append(keyword)


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
def test_lock_prevents_two_concurrent_executions(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "bookhound.sqlite3")
    lock_path = tmp_path / "bookhound.lock"
    lock_path.write_text("already running", encoding="utf-8")
    executor = FakeJobExecutor()
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
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    document_id, document_url_id = _document_with_url(repositories)
    repositories.license_evidence.add(
        document_id=document_id,
        document_url_id=document_url_id,
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


def _document_with_url(repositories: RepositorySet) -> tuple[int, int]:
    source_id = repositories.sources.upsert(SourceKind.SITEMAP)
    document_id = repositories.documents.upsert(
        Document(
            title="Open Report",
            doi="10.1234/open-report",
            metadata={"subject": "climate"},
        )
    )
    document_url_id = repositories.document_urls.upsert(
        document_id=document_id,
        source_id=source_id,
        document_url=DocumentUrl(
            url="https://example.org/open-report.pdf",
            canonical_url="https://example.org/open-report.pdf",
            source=SourceKind.SITEMAP,
            discovery_method=DiscoveryMethod.SITEMAP,
            url_type=UrlType.PDF,
            confidence=0.95,
        ),
        metadata={},
    )
    return document_id, document_url_id
