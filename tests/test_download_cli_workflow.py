# Consolidated test module. See docs/audit-tests/03-centralize-test-files.md.
# Consolidated from test_download_cli.py

from pathlib import Path

import pytest

from typer.testing import CliRunner

import bookhound.cli as cli
from bookhound.download_workflow import (
    DownloadFailure,
    DownloadSummary,
    DownloadWorkflowService,
)
from bookhound.models import (
    DiscoveryMethod,
    LicenseEvidence,
    LicenseStatus,
    RawCandidate,
    SourceKind,
)


@pytest.mark.revised
def test_download_command_downloads_only_allowed_candidates(
    tmp_path: Path,
    monkeypatch,
    recording_download_service_factory,
    recording_pipeline_factory,
    sitemap_candidate_factory,
    status_map_classifier_factory,
) -> None:
    allowed_candidate = sitemap_candidate_factory(
        title="Allowed",
        url="https://example.org/allowed.pdf",
        query='"keyword"',
        score=0.9,
    )
    denied_candidate = sitemap_candidate_factory(
        title="Denied",
        url="https://example.org/denied.pdf",
        query='"keyword"',
        score=0.9,
    )
    pipeline = recording_pipeline_factory([allowed_candidate, denied_candidate])
    download_service = recording_download_service_factory(simulate_license_gate=True)
    _patch_download_dependencies(
        monkeypatch,
        pipeline=pipeline,
        download_service=download_service,
        statuses={
            allowed_candidate.url: LicenseStatus.ALLOWED,
            denied_candidate.url: LicenseStatus.DENIED,
        },
        status_map_classifier_factory=status_map_classifier_factory,
    )

    result = CliRunner().invoke(
        cli.app,
        ["download", "climate"],
        env={"BOOKHOUND_DATABASE_PATH": str(tmp_path / "bookhound.sqlite3")},
    )

    assert result.exit_code == 0
    assert pipeline.searched_keywords == ["climate"]
    assert [call["url"] for call in download_service.calls] == [
        "https://example.org/allowed.pdf"
    ]
    assert "downloaded: 1" in result.stdout
    assert "blocked: 1" in result.stdout


@pytest.mark.revised
def test_download_command_downloads_manually_authorized_candidates(
    tmp_path: Path,
    monkeypatch,
    recording_download_service_factory,
    recording_pipeline_factory,
    sitemap_candidate_factory,
    status_map_classifier_factory,
) -> None:
    candidate = sitemap_candidate_factory(
        title="Manual",
        url="https://example.org/manual.pdf",
        query='"keyword"',
        score=0.9,
    )
    download_service = recording_download_service_factory(simulate_license_gate=True)
    _patch_download_dependencies(
        monkeypatch,
        pipeline=recording_pipeline_factory([candidate]),
        download_service=download_service,
        statuses={candidate.url: LicenseStatus.MANUALLY_AUTHORIZED},
        status_map_classifier_factory=status_map_classifier_factory,
    )

    result = CliRunner().invoke(
        cli.app,
        ["download", "manual"],
        env={"BOOKHOUND_DATABASE_PATH": str(tmp_path / "bookhound.sqlite3")},
    )

    assert result.exit_code == 0
    assert [call["url"] for call in download_service.calls] == [
        "https://example.org/manual.pdf"
    ]
    assert "downloaded: 1" in result.stdout


@pytest.mark.revised
def test_previously_collected_only_option_does_not_call_external_discovery(
    tmp_path: Path,
    monkeypatch,
    recording_download_service_factory,
    recording_pipeline_factory,
    sitemap_candidate_factory,
    status_map_classifier_factory,
) -> None:
    pipeline = recording_pipeline_factory(
        [
            sitemap_candidate_factory(
                title="Collected",
                url="https://example.org/collected.pdf",
                query='"keyword"',
                score=0.9,
            )
        ]
    )
    download_service = recording_download_service_factory(simulate_license_gate=True)
    database_path = tmp_path / "bookhound.sqlite3"
    _patch_download_dependencies(
        monkeypatch,
        pipeline=pipeline,
        download_service=download_service,
        statuses={"https://example.org/collected.pdf": LicenseStatus.ALLOWED},
        status_map_classifier_factory=status_map_classifier_factory,
    )
    collect_result = CliRunner().invoke(
        cli.app,
        ["collect", "collected"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )
    assert collect_result.exit_code == 0
    pipeline.searched_keywords.clear()

    result = CliRunner().invoke(
        cli.app,
        ["download", "collected", "--collected-only"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert result.exit_code == 0
    assert pipeline.searched_keywords == []


@pytest.mark.revised
def test_unknown_prompt_respects_the_user_response(
    tmp_path: Path,
    monkeypatch,
    recording_download_service_factory,
    recording_pipeline_factory,
    sitemap_candidate_factory,
    status_map_classifier_factory,
) -> None:
    candidate = sitemap_candidate_factory(
        title="Unknown",
        url="https://example.org/unknown.pdf",
        query='"keyword"',
        score=0.9,
    )
    download_service = recording_download_service_factory(simulate_license_gate=True)
    _patch_download_dependencies(
        monkeypatch,
        pipeline=recording_pipeline_factory([candidate]),
        download_service=download_service,
        statuses={candidate.url: LicenseStatus.UNKNOWN},
        status_map_classifier_factory=status_map_classifier_factory,
    )

    declined = CliRunner().invoke(
        cli.app,
        ["download", "unknown"],
        input="n\n",
        env={"BOOKHOUND_DATABASE_PATH": str(tmp_path / "declined.sqlite3")},
    )
    accepted = CliRunner().invoke(
        cli.app,
        ["download", "unknown"],
        input="y\n",
        env={"BOOKHOUND_DATABASE_PATH": str(tmp_path / "accepted.sqlite3")},
    )

    assert declined.exit_code == 0
    assert accepted.exit_code == 0
    assert "pending: 1" in declined.stdout
    assert "downloaded: 1" in accepted.stdout


@pytest.mark.revised
def test_download_final_summary_shows_correct_counts(
    tmp_path: Path,
    monkeypatch,
    recording_download_service_factory,
    recording_pipeline_factory,
    sitemap_candidate_factory,
    status_map_classifier_factory,
) -> None:
    allowed = sitemap_candidate_factory(
        title="Allowed",
        url="https://example.org/allowed.pdf",
        query='"keyword"',
        score=0.9,
    )
    denied = sitemap_candidate_factory(
        title="Denied",
        url="https://example.org/denied.pdf",
        query='"keyword"',
        score=0.9,
    )
    unknown = sitemap_candidate_factory(
        title="Unknown",
        url="https://example.org/unknown.pdf",
        query='"keyword"',
        score=0.9,
    )
    _patch_download_dependencies(
        monkeypatch,
        pipeline=recording_pipeline_factory([allowed, denied, unknown]),
        download_service=recording_download_service_factory(
            simulate_license_gate=True
        ),
        statuses={
            allowed.url: LicenseStatus.ALLOWED,
            denied.url: LicenseStatus.DENIED,
            unknown.url: LicenseStatus.UNKNOWN,
        },
        status_map_classifier_factory=status_map_classifier_factory,
    )

    result = CliRunner().invoke(
        cli.app,
        ["download", "summary"],
        input="n\n",
        env={"BOOKHOUND_DATABASE_PATH": str(tmp_path / "bookhound.sqlite3")},
    )

    assert result.exit_code == 0
    assert "downloaded: 1" in result.stdout
    assert "blocked: 1" in result.stdout
    assert "pending: 1" in result.stdout
    assert "failed: 0" in result.stdout


@pytest.mark.revised
def test_download_workflow_counts_license_gate_outcomes_and_failures(
    declining_prompt_factory,
    recording_download_service_factory,
    status_map_classifier_factory,
) -> None:
    evidence = LicenseEvidence(
        source="html",
        evidence_type="license",
        value="cc-by",
        suggested_status=LicenseStatus.ALLOWED,
        confidence=0.9,
    )
    allowed = _workflow_candidate(
        "Allowed",
        "https://example.org/allowed.pdf",
        metadata={
            "document_id": 11,
            "document_url_id": 12,
            "license_evidence": [{"id": 7, "evidence": evidence}],
        },
    )
    denied = _workflow_candidate("Denied", "https://example.org/denied.pdf")
    unknown = _workflow_candidate("Unknown", "https://example.org/unknown.pdf")
    failing = _workflow_candidate("Failing", "https://example.org/failing.pdf")
    classifier = status_map_classifier_factory(
        {
            allowed.url: LicenseStatus.ALLOWED,
            denied.url: LicenseStatus.DENIED,
            unknown.url: LicenseStatus.UNKNOWN,
            failing.url: LicenseStatus.ALLOWED,
        }
    )
    download_service = recording_download_service_factory(
        failures_by_url={failing.url: RuntimeError("download failed")},
        record_status=False,
    )

    summary = DownloadWorkflowService(
        classifier=classifier,
        service=download_service,
        prompt=declining_prompt_factory(),
    ).run([allowed, denied, unknown, failing])

    assert summary == DownloadSummary(
        downloaded=1,
        blocked=1,
        pending=1,
        failed=1,
    )
    assert [call["url"] for call in download_service.calls] == [
        allowed.url,
        failing.url,
    ]
    assert download_service.calls[0]["document_id"] == 11
    assert download_service.calls[0]["document_url_id"] == 12
    assert download_service.calls[0]["license_evidence_id"] == 7


@pytest.mark.revised
def test_download_command_surfaces_failure_details(tmp_path: Path, monkeypatch) -> None:
    failure = DownloadFailure(
        url="https://example.org/reports/failure.pdf",
        title="Failure Report",
        document_id=41,
        document_url_id=42,
        error="temporary storage outage",
    )

    def fake_download_candidates(keyword, collected_only, repositories):
        return [], 0

    def fake_download_candidates_with_license_gate(
        candidates,
        *,
        classifier,
        service,
        prompt,
    ) -> DownloadSummary:
        return DownloadSummary(failed=1, failures=[failure])

    monkeypatch.setattr(
        cli,
        "_download_candidates",
        fake_download_candidates,
        raising=False,
    )
    monkeypatch.setattr(cli, "build_license_classifier", lambda: object(), raising=False)
    monkeypatch.setattr(
        cli,
        "build_download_service",
        lambda repositories, settings, prompt: object(),
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "_download_candidates_with_license_gate",
        fake_download_candidates_with_license_gate,
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["download", "failures"],
        env={"BOOKHOUND_DATABASE_PATH": str(tmp_path / "bookhound.sqlite3")},
    )

    assert result.exit_code == 0
    assert "failed: 1" in result.stdout
    assert "Download failures:" in result.stdout
    assert "Failure Report" in result.stdout
    assert "https://example.org/reports/failure.pdf" in result.stdout
    assert "temporary storage outage" in result.stdout
    assert "Traceback" not in result.stdout


def _patch_download_dependencies(
    monkeypatch,
    *,
    pipeline: object,
    download_service: object,
    statuses: dict[str, LicenseStatus],
    status_map_classifier_factory,
) -> None:
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)
    monkeypatch.setattr(
        cli,
        "build_license_classifier",
        lambda: status_map_classifier_factory(statuses),
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "build_download_service",
        lambda repositories, settings, prompt: download_service,
        raising=False,
    )

def _workflow_candidate(
    title: str,
    url: str,
    *,
    metadata: dict[str, object] | None = None,
) -> RawCandidate:
    return RawCandidate(
        title=title,
        url=url,
        source=SourceKind.SITEMAP,
        discovery_method=DiscoveryMethod.SITEMAP,
        query='"keyword"',
        score=0.9,
        metadata=metadata or {},
    )


# Consolidated from test_download_failure_context.py

import pytest

from bookhound.download_workflow import DownloadWorkflowService
from bookhound.models import LicenseStatus


@pytest.mark.revised
def test_download_exception_records_candidate_failure_context(
    failing_prompt_factory,
    persisted_download_candidate_factory,
    recording_download_service_factory,
    status_map_classifier_factory,
) -> None:
    candidate = persisted_download_candidate_factory(
        title="Disk Failure Report",
        url="https://example.org/reports/disk-failure.pdf",
        document_id=41,
        document_url_id=42,
        query='"download failure context"',
        score=0.9,
    )
    service = recording_download_service_factory(
        failures_by_url={
            candidate.candidate.url: OSError("disk full while writing PDF")
        },
        record_status=False,
    )

    summary = DownloadWorkflowService(
        classifier=status_map_classifier_factory(
            {candidate.candidate.url: LicenseStatus.ALLOWED}
        ),
        service=service,
        prompt=failing_prompt_factory(
            message="Allowed and denied candidates should not prompt."
        ),
    ).run([candidate])

    assert summary.downloaded == 0
    assert summary.blocked == 0
    assert summary.pending == 0
    assert summary.failed == 1
    assert len(summary.failures) == 1

    failure = summary.failures[0]
    assert failure.url == candidate.candidate.url
    assert failure.title == "Disk Failure Report"
    assert failure.document_id == 41
    assert failure.document_url_id == 42
    assert failure.error == "disk full while writing PDF"


@pytest.mark.revised
def test_download_failure_context_keeps_later_candidates_running(
    failing_prompt_factory,
    persisted_download_candidate_factory,
    recording_download_service_factory,
    status_map_classifier_factory,
) -> None:
    failing = persisted_download_candidate_factory(
        title="Failing Report",
        url="https://example.org/reports/failing.pdf",
        document_id=51,
        document_url_id=52,
        query='"download failure context"',
        score=0.9,
    )
    successful = persisted_download_candidate_factory(
        title="Successful Report",
        url="https://example.org/reports/successful.pdf",
        document_id=61,
        document_url_id=62,
        query='"download failure context"',
        score=0.9,
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
        prompt=failing_prompt_factory(
            message="Allowed and denied candidates should not prompt."
        ),
    ).run([failing, successful])

    assert [call["url"] for call in service.calls] == [
        failing.candidate.url,
        successful.candidate.url,
    ]
    assert summary.downloaded == 1
    assert summary.blocked == 0
    assert summary.pending == 0
    assert summary.failed == 1
    assert [failure.url for failure in summary.failures] == [failing.candidate.url]
    assert summary.failures[0].error == "temporary storage outage"


@pytest.mark.revised
def test_license_gate_outcomes_are_not_reported_as_download_failures(
    declining_prompt_factory,
    persisted_download_candidate_factory,
    recording_download_service_factory,
    status_map_classifier_factory,
) -> None:
    denied = persisted_download_candidate_factory(
        title="Denied Report",
        url="https://example.org/reports/denied.pdf",
        document_id=71,
        document_url_id=72,
        query='"download failure context"',
        score=0.9,
    )
    unknown = persisted_download_candidate_factory(
        title="Unknown Report",
        url="https://example.org/reports/unknown.pdf",
        document_id=81,
        document_url_id=82,
        query='"download failure context"',
        score=0.9,
    )
    service = recording_download_service_factory(record_status=False)

    summary = DownloadWorkflowService(
        classifier=status_map_classifier_factory(
            {
                denied.candidate.url: LicenseStatus.DENIED,
                unknown.candidate.url: LicenseStatus.UNKNOWN,
            }
        ),
        service=service,
        prompt=declining_prompt_factory(),
    ).run([denied, unknown])

    assert service.calls == []
    assert summary.downloaded == 0
    assert summary.blocked == 1
    assert summary.pending == 1
    assert summary.failed == 0
    assert summary.failures == []


# Consolidated from test_download_license_gate_runtime.py

from pathlib import Path

from typer.testing import CliRunner

import pytest

import bookhound.cli as cli
from bookhound.database import initialize_database
from bookhound.models import (
    DiscoveryMethod,
    Document,
    DocumentUrl,
    LicenseEvidence,
    LicenseStatus,
    SourceKind,
    UrlType,
)
from bookhound.repositories import RepositorySet


@pytest.mark.revised
def test_collected_only_download_classifies_with_persisted_license_evidence(
    tmp_path: Path,
    monkeypatch,
    fixed_status_classifier_factory,
    recording_download_service_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    evidence_id = _save_collected_document_with_evidence(
        database_path,
        evidence=LicenseEvidence(
            source="html",
            evidence_type="license",
            value="https://creativecommons.org/licenses/by/4.0/",
            suggested_status=LicenseStatus.ALLOWED,
            confidence=0.95,
        ),
    )
    classifier = fixed_status_classifier_factory(status=LicenseStatus.ALLOWED)
    download_service = recording_download_service_factory()
    monkeypatch.setattr(cli, "build_license_classifier", lambda: classifier)
    monkeypatch.setattr(
        cli,
        "build_download_service",
        lambda repositories, settings, prompt: download_service,
    )

    result = CliRunner().invoke(
        cli.app,
        ["download", "ignored", "--collected-only"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert result.exit_code == 0
    assert len(classifier.calls) == 1
    evidence = classifier.calls[0]["evidence"]
    assert [item.value for item in evidence] == [
        "https://creativecommons.org/licenses/by/4.0/"
    ]
    assert evidence[0].suggested_status is LicenseStatus.ALLOWED
    assert download_service.calls[0]["license_evidence_id"] == evidence_id


@pytest.mark.revised
def test_collected_only_download_blocks_when_persisted_evidence_denies_access(
    tmp_path: Path,
    monkeypatch,
    recording_download_service_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    _save_collected_document_with_evidence(
        database_path,
        evidence=LicenseEvidence(
            source="html",
            evidence_type="access",
            value="Subscription required. Do not distribute.",
            suggested_status=LicenseStatus.DENIED,
            confidence=0.9,
        ),
    )
    download_service = recording_download_service_factory()
    monkeypatch.setattr(cli, "build_license_classifier", cli.LicenseClassifier)
    monkeypatch.setattr(
        cli,
        "build_download_service",
        lambda repositories, settings, prompt: download_service,
    )

    result = CliRunner().invoke(
        cli.app,
        ["download", "ignored", "--collected-only"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert result.exit_code == 0
    assert download_service.calls == []
    assert "blocked: 1" in result.stdout


def _save_collected_document_with_evidence(
    database_path: Path,
    *,
    evidence: LicenseEvidence,
) -> int:
    repositories = RepositorySet(initialize_database(database_path))
    try:
        source_id = repositories.sources.upsert(SourceKind.SITEMAP)
        document_id = repositories.documents.upsert(
            Document(title="Collected License Report")
        )
        document_url_id = repositories.document_urls.upsert(
            document_id=document_id,
            source_id=source_id,
            document_url=DocumentUrl(
                url="https://example.org/reports/license-report.pdf",
                canonical_url="https://example.org/reports/license-report.pdf",
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                url_type=UrlType.PDF,
                confidence=0.9,
            ),
            metadata={},
        )
        return repositories.license_evidence.add(
            document_id=document_id,
            document_url_id=document_url_id,
            evidence=evidence,
            metadata={},
        )
    finally:
        repositories.close()


# Consolidated from test_unknown_license_prompt_ownership.py

from pathlib import Path

import pytest

from bookhound.database import initialize_database
from bookhound.download_workflow import DownloadSummary, DownloadWorkflowService
from bookhound.downloader import DownloadService, DownloadServiceConfig
from bookhound.models import LicenseStatus
from bookhound.repositories import RepositorySet


PDF_BYTES = b"%PDF-1.7\nunknown license prompt ownership\n%%EOF\n"


@pytest.mark.revised
def test_real_workflow_and_downloader_prompt_once_for_accepted_unknown(
    tmp_path: Path,
    count_rows_helper,
    fixed_status_classifier_factory,
    pdf_response_factory,
    prompt_stub_factory,
    recording_http_client_factory,
    seed_document_url_factory,
    sitemap_candidate_factory,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    seeded = seed_document_url_factory(
        repositories,
        title="Unknown License Report",
        url="https://example.org/reports/unknown-license.pdf",
    )
    candidate = sitemap_candidate_factory(
        title="Unknown License Report",
        url="https://example.org/reports/unknown-license.pdf",
        query='"unknown license"',
        score=0.9,
        metadata={
            "document_id": seeded.document_id,
            "document_url_id": seeded.document_url_id,
        },
    )
    prompt = prompt_stub_factory(response=True)
    http_client = recording_http_client_factory.single(
        pdf_response_factory(url=candidate.url, content=PDF_BYTES)
    )
    service = DownloadService(
        repositories=repositories,
        http_client=http_client,
        config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
        prompt=prompt,
    )

    summary = DownloadWorkflowService(
        classifier=fixed_status_classifier_factory(status=LicenseStatus.UNKNOWN),
        service=service,
        prompt=prompt,
    ).run([candidate])

    assert summary == DownloadSummary(downloaded=1)
    assert len(prompt.decisions) == 1
    assert prompt.decisions[0].status is LicenseStatus.UNKNOWN
    assert http_client.urls == [candidate.url]
    assert count_rows_helper(repositories.connection, "downloads") == 1


@pytest.mark.revised
def test_real_workflow_declined_unknown_license_never_reaches_downloader(
    tmp_path: Path,
    count_rows_helper,
    fixed_status_classifier_factory,
    pdf_response_factory,
    prompt_stub_factory,
    recording_http_client_factory,
    seed_document_url_factory,
    sitemap_candidate_factory,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    seeded = seed_document_url_factory(
        repositories,
        title="Unknown License Report",
        url="https://example.org/reports/unknown-license.pdf",
    )
    candidate = sitemap_candidate_factory(
        title="Unknown License Report",
        url="https://example.org/reports/unknown-license.pdf",
        query='"unknown license"',
        score=0.9,
        metadata={
            "document_id": seeded.document_id,
            "document_url_id": seeded.document_url_id,
        },
    )
    prompt = prompt_stub_factory(response=False)
    http_client = recording_http_client_factory.single(
        pdf_response_factory(url=candidate.url, content=PDF_BYTES)
    )
    service = DownloadService(
        repositories=repositories,
        http_client=http_client,
        config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
        prompt=prompt,
    )

    summary = DownloadWorkflowService(
        classifier=fixed_status_classifier_factory(status=LicenseStatus.UNKNOWN),
        service=service,
        prompt=prompt,
    ).run([candidate])

    assert summary == DownloadSummary(pending=1)
    assert len(prompt.decisions) == 1
    assert http_client.urls == []
    assert count_rows_helper(repositories.connection, "downloads") == 0
