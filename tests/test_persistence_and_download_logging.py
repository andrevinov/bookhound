from __future__ import annotations

from hashlib import sha256
import logging
from pathlib import Path
import sqlite3

import pytest

from bookhound.download_workflow import DownloadWorkflowService
from bookhound.downloader import DownloadService, DownloadServiceConfig
from bookhound.models import DownloadStatus, LicenseEvidence, LicenseStatus


PDF_BYTES = b"%PDF-1.7\nlogging download test pdf\n%%EOF\n"


@pytest.fixture(autouse=True)
def reset_bookhound_logging() -> None:
    logger = logging.getLogger("bookhound")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate

    logger.handlers = []
    logger.setLevel(logging.NOTSET)
    logger.propagate = True
    try:
        yield
    finally:
        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate


@pytest.mark.revised
def test_collection_persistence_logs_completed_summary(
    caplog: pytest.LogCaptureFixture,
    common_crawl_candidate_factory,
    discovery_result_factory,
    repositories,
) -> None:
    caplog.set_level(logging.INFO, logger="bookhound")
    result = discovery_result_factory(
        keyword="logging persistence",
        candidates=[
            common_crawl_candidate_factory(
                title="Persisted Log Report",
                url="https://example.org/reports/persisted.pdf",
            )
        ],
        errors=["sitemap: transient warning"],
    )

    summary = repositories.save_discovery_result(result)

    assert summary.total == 1
    assert summary.new == 1
    assert summary.updated == 0
    assert summary.duplicate == 0

    record = _single_log_record(caplog.records, "collect.persistence.completed")
    assert record.levelno == logging.INFO
    assert record.keyword == "logging persistence"
    assert record.total == 1
    assert record.new == 1
    assert record.updated == 0
    assert record.duplicate == 0
    assert record.error_count == 1
    assert record.duration_ms >= 0


@pytest.mark.revised
def test_collection_persistence_logs_rollback_on_failure(
    caplog: pytest.LogCaptureFixture,
    common_crawl_candidate_factory,
    count_rows_helper,
    discovery_result_factory,
    repositories,
) -> None:
    caplog.set_level(logging.INFO, logger="bookhound")
    invalid_evidence = LicenseEvidence.model_construct(
        source="fixture",
        evidence_type="fixture",
        value="Invalid evidence status for transaction rollback coverage.",
        suggested_status="invalid-license-status",
        confidence=1.0,
    )
    result = discovery_result_factory(
        keyword="rollback logging",
        candidates=[
            common_crawl_candidate_factory(
                title="Rollback Log Report",
                url="https://example.org/reports/rollback.pdf",
                metadata={
                    "license_evidence": [
                        {
                            "evidence": invalid_evidence,
                            "metadata": {"scope": "transaction-test"},
                        }
                    ]
                },
            )
        ],
    )

    with pytest.raises(sqlite3.IntegrityError):
        repositories.save_discovery_result(result)

    assert count_rows_helper(repositories.connection, "queries") == 0
    assert count_rows_helper(repositories.connection, "sources") == 0
    assert count_rows_helper(repositories.connection, "documents") == 0
    assert count_rows_helper(repositories.connection, "document_urls") == 0
    assert count_rows_helper(repositories.connection, "license_evidence") == 0
    assert count_rows_helper(repositories.connection, "events") == 0

    record = _single_log_record(caplog.records, "collect.persistence.failed")
    assert record.levelno == logging.ERROR
    assert record.keyword == "rollback logging"
    assert record.total == 1
    assert record.error_count == 0
    assert record.duration_ms >= 0
    assert isinstance(record.error, str)
    assert record.error
    assert record.exc_info is not None


@pytest.mark.revised
def test_download_workflow_logs_denied_license_as_warning(
    caplog: pytest.LogCaptureFixture,
    failing_prompt_factory,
    fixed_status_classifier_factory,
    persisted_download_candidate_factory,
    recording_download_service_factory,
) -> None:
    caplog.set_level(logging.INFO, logger="bookhound")
    candidate = persisted_download_candidate_factory(
        title="Denied Report",
        url="https://example.org/reports/denied.pdf?token=secret#section",
        document_id=11,
        document_url_id=12,
    )
    service = recording_download_service_factory()

    summary = DownloadWorkflowService(
        classifier=fixed_status_classifier_factory(LicenseStatus.DENIED),
        service=service,
        prompt=failing_prompt_factory(),
    ).run([candidate])

    assert service.calls == []
    assert summary.downloaded == 0
    assert summary.blocked == 1
    assert summary.pending == 0
    assert summary.failed == 0

    record = _single_log_record(caplog.records, "download.license.blocked")
    assert record.levelno == logging.WARNING
    assert record.document_id == 11
    assert record.document_url_id == 12
    assert record.url == "https://example.org/reports/denied.pdf"
    assert record.license_status == "denied"
    assert "secret" not in record.url


@pytest.mark.revised
def test_download_workflow_logs_unknown_license_pending(
    caplog: pytest.LogCaptureFixture,
    declining_prompt_factory,
    fixed_status_classifier_factory,
    persisted_download_candidate_factory,
    recording_download_service_factory,
) -> None:
    caplog.set_level(logging.INFO, logger="bookhound")
    candidate = persisted_download_candidate_factory(
        title="Unknown Report",
        url="https://example.org/reports/unknown.pdf?token=secret#license",
        document_id=21,
        document_url_id=22,
    )
    prompt = declining_prompt_factory()
    service = recording_download_service_factory()

    summary = DownloadWorkflowService(
        classifier=fixed_status_classifier_factory(LicenseStatus.UNKNOWN),
        service=service,
        prompt=prompt,
    ).run([candidate])

    assert service.calls == []
    assert len(prompt.decisions) == 1
    assert summary.downloaded == 0
    assert summary.blocked == 0
    assert summary.pending == 1
    assert summary.failed == 0

    record = _single_log_record(caplog.records, "download.license.pending")
    assert record.levelno == logging.WARNING
    assert record.document_id == 21
    assert record.document_url_id == 22
    assert record.url == "https://example.org/reports/unknown.pdf"
    assert record.license_status == "unknown"
    assert "secret" not in record.url


@pytest.mark.revised
def test_download_workflow_logs_candidate_exception(
    caplog: pytest.LogCaptureFixture,
    failing_prompt_factory,
    persisted_download_candidate_factory,
    recording_download_service_factory,
    status_map_classifier_factory,
) -> None:
    caplog.set_level(logging.INFO, logger="bookhound")
    failing = persisted_download_candidate_factory(
        title="Failing Report",
        url="https://example.org/reports/failing.pdf?token=secret#download",
        document_id=31,
        document_url_id=32,
    )
    successful = persisted_download_candidate_factory(
        title="Successful Report",
        url="https://example.org/reports/successful.pdf",
        document_id=41,
        document_url_id=42,
    )
    service = recording_download_service_factory(
        failures_by_url={
            failing.candidate.url: RuntimeError("temporary storage outage")
        },
        record_status=False,
    )

    summary = DownloadWorkflowService(
        classifier=status_map_classifier_factory(
            {
                failing.candidate.url: LicenseStatus.ALLOWED,
                successful.candidate.url: LicenseStatus.ALLOWED,
            }
        ),
        service=service,
        prompt=failing_prompt_factory(),
    ).run([failing, successful])

    assert [call["url"] for call in service.calls] == [
        failing.candidate.url,
        successful.candidate.url,
    ]
    assert summary.downloaded == 1
    assert summary.failed == 1

    record = _single_log_record(caplog.records, "download.candidate.failed")
    assert record.levelno == logging.ERROR
    assert record.document_id == 31
    assert record.document_url_id == 32
    assert record.url == "https://example.org/reports/failing.pdf"
    assert record.error == "temporary storage outage"
    assert "secret" not in record.url


@pytest.mark.revised
def test_download_service_logs_completed_download(
    caplog: pytest.LogCaptureFixture,
    license_decision_factory,
    pdf_response_factory,
    recording_http_client_factory,
    repositories,
    seed_document_url_factory,
    tmp_path: Path,
) -> None:
    caplog.set_level(logging.INFO, logger="bookhound")
    seeded = seed_document_url_factory(
        repositories,
        title="Downloaded Log Report",
        url="https://example.org/reports/downloaded.pdf?token=secret#page",
    )
    service = DownloadService(
        repositories=repositories,
        http_client=recording_http_client_factory.single(
            pdf_response_factory(url=seeded.url, content=PDF_BYTES)
        ),
        config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
    )

    result = service.download(
        document_id=seeded.document_id,
        document_url_id=seeded.document_url_id,
        url=seeded.url,
        license_decision=license_decision_factory(LicenseStatus.ALLOWED),
    )

    assert result.status is DownloadStatus.DOWNLOADED
    assert Path(result.local_path).read_bytes() == PDF_BYTES

    record = _single_log_record(caplog.records, "download.completed")
    assert record.levelno == logging.INFO
    assert record.document_id == seeded.document_id
    assert record.document_url_id == seeded.document_url_id
    assert record.url == "https://example.org/reports/downloaded.pdf"
    assert record.size_bytes == len(PDF_BYTES)
    assert record.sha256 == sha256(PDF_BYTES).hexdigest()
    assert "logging download test pdf" not in repr(record.__dict__)
    assert "secret" not in record.url


@pytest.mark.revised
def test_download_service_logs_response_validation_failure(
    caplog: pytest.LogCaptureFixture,
    license_decision_factory,
    recording_http_client_factory,
    repositories,
    seed_document_url_factory,
    text_response_factory,
    tmp_path: Path,
) -> None:
    caplog.set_level(logging.INFO, logger="bookhound")
    seeded = seed_document_url_factory(
        repositories,
        title="Invalid Response Report",
        url="https://example.org/reports/invalid.pdf?token=secret#page",
    )
    service = DownloadService(
        repositories=repositories,
        http_client=recording_http_client_factory.single(
            text_response_factory(url=seeded.url, content=b"This is not a PDF.")
        ),
        config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
    )

    result = service.download(
        document_id=seeded.document_id,
        document_url_id=seeded.document_url_id,
        url=seeded.url,
        license_decision=license_decision_factory(LicenseStatus.ALLOWED),
    )

    assert result.status is DownloadStatus.FAILED
    assert not Path(result.local_path).exists()
    assert not list((tmp_path / "pdfs").glob("*.pdf"))

    record = _single_log_record(caplog.records, "download.validation_failed")
    assert record.levelno == logging.WARNING
    assert record.document_id == seeded.document_id
    assert record.document_url_id == seeded.document_url_id
    assert record.url == "https://example.org/reports/invalid.pdf"
    assert record.error == "Response content type is not PDF: text/plain."
    assert "secret" not in record.url


def _single_log_record(
    records: list[logging.LogRecord],
    event: str,
) -> logging.LogRecord:
    matches = [record for record in records if getattr(record, "event", None) == event]
    assert len(matches) == 1, [
        getattr(record, "event", None) for record in records
    ]
    return matches[0]
