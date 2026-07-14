# Consolidated test module. See docs/audit-tests/03-centralize-test-files.md.
# Consolidated from test_download_persistence_boundary.py

from pathlib import Path
import sqlite3

import pytest

from typer.testing import CliRunner

import bookhound.cli as cli
from bookhound.database import initialize_database
from bookhound.downloader import DownloadService, DownloadServiceConfig
from bookhound.models import (
    DiscoveryMethod,
    Document,
    DocumentUrl,
    LicenseStatus,
    SourceKind,
    UrlType,
)
from bookhound.repositories import RepositorySet


PDF_BYTES = b"%PDF-1.7\nfresh default download\n%%EOF\n"


@pytest.mark.revised
def test_default_download_persists_fresh_candidate_before_real_file_download(
    tmp_path: Path,
    monkeypatch,
    fixed_status_classifier_factory,
    pdf_response_factory,
    recording_http_client_factory,
    recording_pipeline_factory,
    sitemap_candidate_factory,
    write_isolated_download_config_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    pdf_directory = tmp_path / "pdfs"
    config_path = write_isolated_download_config_factory(
        tmp_path,
        database_path=database_path,
        pdf_directory=pdf_directory,
    )
    candidate = sitemap_candidate_factory(
        title="Fresh Download Report",
        url="https://example.org/reports/fresh-download.pdf",
        query='"keyword"',
        score=0.9,
        metadata={
            "doi": "10.1234/fresh-download",
            "authors": ["Ada Lovelace"],
            "year": 2026,
        },
    )
    pipeline = recording_pipeline_factory([candidate])
    http_client = recording_http_client_factory.single(
        pdf_response_factory(url=candidate.url, content=PDF_BYTES)
    )
    _patch_runtime_dependencies(
        monkeypatch,
        fixed_status_classifier_factory=fixed_status_classifier_factory,
        pipeline=pipeline,
        http_client=http_client,
    )

    result = CliRunner().invoke(
        cli.app,
        ["--config", str(config_path), "download", "fresh download"],
    )

    assert result.exit_code == 0
    assert "downloaded: 1" in result.stdout
    assert "failed: 0" in result.stdout
    assert pipeline.searched_keywords == ["fresh download"]
    assert http_client.urls == [candidate.url]

    with sqlite3.connect(database_path) as connection:
        document = connection.execute(
            "SELECT id, title, doi FROM documents"
        ).fetchone()
        document_url = connection.execute(
            "SELECT id, document_id, url, canonical_url FROM document_urls"
        ).fetchone()
        download = connection.execute(
            "SELECT document_id, document_url_id, status, local_path FROM downloads"
        ).fetchone()

    assert document is not None
    assert document_url is not None
    assert download is not None
    document_id = int(document[0])
    document_url_id = int(document_url[0])

    assert document == (document_id, "Fresh Download Report", "10.1234/fresh-download")
    assert document_url == (
        document_url_id,
        document_id,
        candidate.url,
        candidate.url,
    )
    assert download[:3] == (document_id, document_url_id, "downloaded")
    assert Path(download[3]).read_bytes() == PDF_BYTES


@pytest.mark.revised
def test_collected_only_download_uses_persisted_ids_with_real_download_service(
    tmp_path: Path,
    monkeypatch,
    failing_pipeline_factory,
    fixed_status_classifier_factory,
    pdf_response_factory,
    recording_http_client_factory,
    write_isolated_download_config_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    pdf_directory = tmp_path / "pdfs"
    config_path = write_isolated_download_config_factory(
        tmp_path,
        database_path=database_path,
        pdf_directory=pdf_directory,
    )
    document_id, document_url_id, url = _save_collected_document(database_path)
    http_client = recording_http_client_factory.single(
        pdf_response_factory(url=url, content=PDF_BYTES)
    )
    _patch_runtime_dependencies(
        monkeypatch,
        fixed_status_classifier_factory=fixed_status_classifier_factory,
        pipeline=failing_pipeline_factory(
            "Collected-only downloads must not run discovery."
        ),
        http_client=http_client,
    )

    result = CliRunner().invoke(
        cli.app,
        ["--config", str(config_path), "download", "ignored", "--collected-only"],
    )

    assert result.exit_code == 0
    assert "downloaded: 1" in result.stdout
    assert "failed: 0" in result.stdout
    assert http_client.urls == [url]

    with sqlite3.connect(database_path) as connection:
        download = connection.execute(
            "SELECT document_id, document_url_id, status, local_path FROM downloads"
        ).fetchone()

    assert download[:3] == (document_id, document_url_id, "downloaded")
    assert Path(download[3]).read_bytes() == PDF_BYTES


@pytest.mark.revised
def test_default_download_does_not_fetch_or_write_when_candidate_cannot_be_persisted(
    tmp_path: Path,
    monkeypatch,
    count_rows_helper,
    fixed_status_classifier_factory,
    pdf_response_factory,
    recording_http_client_factory,
    recording_pipeline_factory,
    sitemap_candidate_factory,
    write_isolated_download_config_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    pdf_directory = tmp_path / "pdfs"
    config_path = write_isolated_download_config_factory(
        tmp_path,
        database_path=database_path,
        pdf_directory=pdf_directory,
    )
    unsupported_candidate = sitemap_candidate_factory(
        title="Unsupported URL",
        url="ftp://example.org/reports/unsupported.pdf",
        query='"keyword"',
        score=0.9,
    )
    http_client = recording_http_client_factory.single(
        pdf_response_factory(url=unsupported_candidate.url, content=PDF_BYTES)
    )
    _patch_runtime_dependencies(
        monkeypatch,
        fixed_status_classifier_factory=fixed_status_classifier_factory,
        pipeline=recording_pipeline_factory([unsupported_candidate]),
        http_client=http_client,
    )

    result = CliRunner().invoke(
        cli.app,
        ["--config", str(config_path), "download", "unsupported"],
    )

    assert result.exit_code == 0
    assert "downloaded: 0" in result.stdout
    assert "failed: 1" in result.stdout
    assert http_client.urls == []
    assert not pdf_directory.exists()

    with sqlite3.connect(database_path) as connection:
        assert count_rows_helper(connection, "documents") == 0
        assert count_rows_helper(connection, "document_urls") == 0
        assert count_rows_helper(connection, "downloads") == 0


def _patch_runtime_dependencies(
    monkeypatch,
    *,
    fixed_status_classifier_factory,
    pipeline: object,
    http_client: object,
) -> None:
    monkeypatch.setattr(
        cli,
        "build_search_pipeline",
        lambda *args, **kwargs: pipeline,
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "build_license_classifier",
        lambda: fixed_status_classifier_factory(status=LicenseStatus.ALLOWED),
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "build_download_service",
        lambda repositories, settings, prompt: DownloadService(
            repositories=repositories,
            http_client=http_client,
            config=DownloadServiceConfig(download_directory=settings.pdf_directory),
            prompt=prompt,
        ),
        raising=False,
    )


def _save_collected_document(database_path: Path) -> tuple[int, int, str]:
    repositories = RepositorySet(initialize_database(database_path))
    url = "https://example.org/reports/collected-download.pdf"
    try:
        source_id = repositories.sources.upsert(SourceKind.SITEMAP)
        document_id = repositories.documents.upsert(
            Document(title="Collected Download Report")
        )
        document_url_id = repositories.document_urls.upsert(
            document_id=document_id,
            source_id=source_id,
            document_url=DocumentUrl(
                url=url,
                canonical_url=url,
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                url_type=UrlType.PDF,
                confidence=0.9,
            ),
            metadata={},
        )
        return document_id, document_url_id, url
    finally:
        repositories.close()


# Consolidated from test_persisted_download_candidates.py

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

import bookhound.cli as cli
import bookhound.models as models
from bookhound.database import initialize_database
from bookhound.download_workflow import DownloadSummary, DownloadWorkflowService
from bookhound.models import (
    DiscoveryMethod,
    Document,
    DocumentUrl,
    LicenseDecision,
    LicenseEvidence,
    LicenseStatus,
    SourceKind,
    UrlType,
)
from bookhound.repositories import RepositorySet


class EvidenceAwareClassifier:
    def __init__(self, status: LicenseStatus = LicenseStatus.ALLOWED) -> None:
        self.status = status
        self.calls: list[dict[str, object]] = []

    def classify(
        self,
        *,
        document_url: str,
        evidence: list[LicenseEvidence],
    ) -> LicenseDecision:
        self.calls.append({"document_url": document_url, "evidence": evidence})
        return LicenseDecision(
            status=self.status,
            reason=f"Test decision for {document_url}.",
            evidence=evidence[:1],
        )


@pytest.mark.revised
def test_persisted_download_candidate_requires_positive_database_identity(
    common_crawl_candidate_factory,
) -> None:
    candidate = common_crawl_candidate_factory(
        title="Explicit Identity Report",
        url="https://example.org/reports/explicit-identity.pdf",
        query='"explicit identity"',
        score=0.91,
        metadata={},
    )

    with pytest.raises(ValidationError):
        models.PersistedDownloadCandidate(
            candidate=candidate,
            canonical_url=candidate.url,
            document_id=0,
            document_url_id=12,
        )

    with pytest.raises(ValidationError):
        models.PersistedDownloadCandidate(
            candidate=candidate,
            canonical_url=candidate.url,
            document_id=11,
            document_url_id=0,
        )


@pytest.mark.revised
def test_persisted_license_evidence_is_a_named_entry_with_optional_id() -> None:
    evidence = _license_evidence()

    entry = models.PersistedLicenseEvidence(id=7, evidence=evidence)

    assert entry.id == 7
    assert entry.evidence == evidence


@pytest.mark.revised
def test_collected_download_candidates_preserve_source_identity_and_named_evidence(
    tmp_path: Path,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    evidence = _license_evidence()

    try:
        source_id = repositories.sources.upsert(SourceKind.COMMON_CRAWL)
        document_id = repositories.documents.upsert(
            Document(title="Collected Common Crawl Report")
        )
        document_url_id = repositories.document_urls.upsert(
            document_id=document_id,
            source_id=source_id,
            document_url=DocumentUrl(
                url="https://example.org/reports/common-crawl.pdf",
                canonical_url="https://example.org/reports/common-crawl.pdf",
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                url_type=UrlType.PDF,
                confidence=0.84,
                discovered_at=datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc),
            ),
            metadata={"collection_query": "common crawl"},
        )
        evidence_id = repositories.license_evidence.add(
            document_id=document_id,
            document_url_id=document_url_id,
            evidence=evidence,
            metadata={"source_record": "fixture"},
        )

        candidates = repositories.document_urls.list_persisted_download_candidates(
            repositories.license_evidence
        )

        assert len(candidates) == 1
        persisted = candidates[0]
        assert isinstance(persisted, models.PersistedDownloadCandidate)
        assert persisted.document_id == document_id
        assert persisted.document_url_id == document_url_id
        assert persisted.canonical_url == "https://example.org/reports/common-crawl.pdf"
        assert persisted.candidate.title == "Collected Common Crawl Report"
        assert persisted.candidate.url == "https://example.org/reports/common-crawl.pdf"
        assert persisted.candidate.source is SourceKind.COMMON_CRAWL
        assert persisted.candidate.discovery_method is DiscoveryMethod.PUBLIC_INDEX
        assert persisted.candidate.query == "collected"
        assert "document_id" not in persisted.candidate.metadata
        assert "document_url_id" not in persisted.candidate.metadata
        assert "license_evidence" not in persisted.candidate.metadata
        assert len(persisted.license_evidence) == 1
        assert persisted.license_evidence[0].id == evidence_id
        assert persisted.license_evidence[0].evidence.value == evidence.value
        assert (
            persisted.license_evidence[0].evidence.suggested_status
            is LicenseStatus.ALLOWED
        )
    finally:
        repositories.close()


@pytest.mark.revised
def test_download_workflow_uses_explicit_identity_without_metadata_contract(
    common_crawl_candidate_factory,
    failing_prompt_factory,
    recording_download_service_factory,
) -> None:
    evidence = _license_evidence()
    candidate = common_crawl_candidate_factory(
        title="Explicit Identity Report",
        url="https://example.org/reports/explicit-identity.pdf",
        query='"explicit identity"',
        score=0.91,
        metadata={"doi": "10.1234/no-metadata-contract"},
    )
    persisted = models.PersistedDownloadCandidate(
        candidate=candidate,
        canonical_url=candidate.url,
        document_id=11,
        document_url_id=12,
        license_evidence=[
            models.PersistedLicenseEvidence(id=7, evidence=evidence),
        ],
    )
    classifier = EvidenceAwareClassifier()
    service = recording_download_service_factory(record_status=False)

    summary = DownloadWorkflowService(
        classifier=classifier,
        service=service,
        prompt=failing_prompt_factory(
            message="Allowed candidates should not prompt for unknown license."
        ),
    ).run([persisted])

    assert summary == DownloadSummary(downloaded=1)
    assert classifier.calls == [
        {"document_url": candidate.url, "evidence": [evidence]},
    ]
    assert service.calls == [
        {
            "document_id": 11,
            "document_url_id": 12,
            "url": candidate.url,
            "license_evidence_id": 7,
            "interactive": False,
        }
    ]
    assert candidate.metadata == {"doi": "10.1234/no-metadata-contract"}


@pytest.mark.revised
def test_fresh_download_preparation_returns_persisted_candidates_without_identity_metadata(
    tmp_path: Path,
    common_crawl_candidate_factory,
    discovery_result_factory,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    candidate = common_crawl_candidate_factory(
        title="Fresh Explicit Identity Report",
        url="https://example.org/reports/fresh-explicit-identity.pdf",
        query='"explicit identity"',
        score=0.91,
        metadata={"doi": "10.1234/fresh-explicit-identity"},
    )

    try:
        prepared_candidates, failed = cli._persist_discovered_download_candidates(
            repositories,
            discovery_result_factory(
                keyword="explicit identity",
                candidates=[candidate],
            ),
        )

        assert failed == 0
        assert len(prepared_candidates) == 1
        persisted = prepared_candidates[0]
        assert isinstance(persisted, models.PersistedDownloadCandidate)
        assert persisted.document_id > 0
        assert persisted.document_url_id > 0
        assert persisted.canonical_url == candidate.url
        assert persisted.candidate.title == candidate.title
        assert persisted.candidate.url == candidate.url
        assert persisted.candidate.metadata == {
            "doi": "10.1234/fresh-explicit-identity"
        }
    finally:
        repositories.close()


@pytest.mark.revised
def test_collected_download_candidates_batch_license_evidence_lookup(
    tmp_path: Path,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))

    try:
        seeded = [
            _seed_collected_candidate(
                repositories,
                title="First Batched Evidence Report",
                url="https://example.org/reports/batched-1.pdf",
                collected_at=datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc),
            ),
            _seed_collected_candidate(
                repositories,
                title="Second Batched Evidence Report",
                url="https://example.org/reports/batched-2.pdf",
                collected_at=datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
            ),
            _seed_collected_candidate(
                repositories,
                title="Third Batched Evidence Report",
                url="https://example.org/reports/batched-3.pdf",
                collected_at=datetime(2026, 7, 10, 11, 0, tzinfo=timezone.utc),
            ),
        ]
        statements: list[str] = []
        repositories.connection.set_trace_callback(statements.append)

        candidates = repositories.document_urls.list_persisted_download_candidates(
            repositories.license_evidence
        )
    finally:
        repositories.connection.set_trace_callback(None)
        repositories.close()

    evidence_selects = [
        statement
        for statement in statements
        if "from license_evidence" in statement.lower()
    ]
    assert len(evidence_selects) <= 1
    assert [candidate.candidate.url for candidate in candidates] == [
        item["url"] for item in seeded
    ]
    assert [
        [entry.evidence.value for entry in candidate.license_evidence]
        for candidate in candidates
    ] == [[item["evidence_value"]] for item in seeded]

def _license_evidence() -> LicenseEvidence:
    return LicenseEvidence(
        source="html",
        evidence_type="license",
        value="https://creativecommons.org/licenses/by/4.0/",
        suggested_status=LicenseStatus.ALLOWED,
        confidence=0.95,
        collected_at=datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc),
    )


def _seed_collected_candidate(
    repositories: RepositorySet,
    *,
    title: str,
    url: str,
    collected_at: datetime,
) -> dict[str, str]:
    source_id = repositories.sources.upsert(SourceKind.COMMON_CRAWL)
    document_id = repositories.documents.upsert(Document(title=title))
    document_url_id = repositories.document_urls.upsert(
        document_id=document_id,
        source_id=source_id,
        document_url=DocumentUrl(
            url=url,
            canonical_url=url,
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
            url_type=UrlType.PDF,
            confidence=0.8,
            discovered_at=collected_at,
        ),
        metadata={},
    )
    evidence_value = f"license evidence for {title}"
    repositories.license_evidence.add(
        document_id=document_id,
        document_url_id=document_url_id,
        evidence=LicenseEvidence(
            source="html",
            evidence_type="license",
            value=evidence_value,
            suggested_status=LicenseStatus.ALLOWED,
            confidence=0.9,
            collected_at=collected_at,
        ),
        metadata={},
    )
    return {"url": url, "evidence_value": evidence_value}
