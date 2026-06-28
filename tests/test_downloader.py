from hashlib import sha256
from pathlib import Path
import sqlite3

import pytest

from bookhound.database import initialize_database
from bookhound.downloader import DownloadService, DownloadServiceConfig
from bookhound.http_client import HttpResponse
from bookhound.models import (
    DiscoveryMethod,
    Document,
    DocumentUrl,
    DownloadStatus,
    LicenseDecision,
    LicenseEvidence,
    LicenseStatus,
    SourceKind,
    UrlType,
)
from bookhound.repositories import RepositorySet


PDF_BYTES = b"%PDF-1.7\nallowed test pdf\n%%EOF\n"


class FakeHttpClient:
    def __init__(self, responses: list[HttpResponse | BaseException]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def get(
        self,
        url: str,
        *,
        rate_limit_key: str | None = None,
        cache: bool = False,
    ) -> HttpResponse:
        self.urls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakePrompt:
    def __init__(self, response: bool) -> None:
        self.response = response
        self.decisions: list[LicenseDecision] = []

    def confirm_unknown_license(self, decision: LicenseDecision) -> bool:
        self.decisions.append(decision)
        return self.response


@pytest.mark.revised
def test_allowed_downloads_and_records_file(tmp_path: Path) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    document_id, document_url_id = _document_with_url(repositories)
    service = DownloadService(
        repositories=repositories,
        http_client=FakeHttpClient([_pdf_response()]),
        config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
    )

    result = service.download(
        document_id=document_id,
        document_url_id=document_url_id,
        url="https://example.org/reports/allowed.pdf",
        license_decision=_decision(LicenseStatus.ALLOWED),
    )

    with sqlite3.connect(tmp_path / "bookhound.sqlite3") as connection:
        row = connection.execute(
            """
            SELECT local_path, sha256, size_bytes, status, license_evidence_id
            FROM downloads
            """
        ).fetchone()

    assert result.status is DownloadStatus.DOWNLOADED
    assert Path(row[0]).read_bytes() == PDF_BYTES
    assert row[1] == sha256(PDF_BYTES).hexdigest()
    assert row[2] == len(PDF_BYTES)
    assert row[3] == "downloaded"
    assert row[4] is None


@pytest.mark.revised
def test_manually_authorized_download_records_authorization_evidence_used(
    tmp_path: Path,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    document_id, document_url_id = _document_with_url(repositories)
    evidence = LicenseEvidence(
        source="user",
        evidence_type="manual_authorization",
        value="Repository owner granted explicit permission by email.",
        suggested_status=LicenseStatus.MANUALLY_AUTHORIZED,
        confidence=1.0,
    )
    evidence_id = repositories.license_evidence.add(
        document_id=document_id,
        document_url_id=document_url_id,
        evidence=evidence,
        metadata={"scope": "document"},
    )
    service = DownloadService(
        repositories=repositories,
        http_client=FakeHttpClient([_pdf_response()]),
        config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
    )

    result = service.download(
        document_id=document_id,
        document_url_id=document_url_id,
        url="https://example.org/reports/allowed.pdf",
        license_decision=_decision(
            LicenseStatus.MANUALLY_AUTHORIZED,
            evidence=[evidence],
        ),
        license_evidence_id=evidence_id,
    )

    row = repositories.connection.execute(
        "SELECT status, license_evidence_id FROM downloads"
    ).fetchone()
    assert result.status is DownloadStatus.DOWNLOADED
    assert row == ("downloaded", evidence_id)


@pytest.mark.revised
def test_denied_does_not_download(tmp_path: Path) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    document_id, document_url_id = _document_with_url(repositories)
    http_client = FakeHttpClient([_pdf_response()])
    service = DownloadService(
        repositories=repositories,
        http_client=http_client,
        config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
    )

    result = service.download(
        document_id=document_id,
        document_url_id=document_url_id,
        url="https://example.org/reports/blocked.pdf",
        license_decision=_decision(LicenseStatus.DENIED),
    )

    assert result.status is DownloadStatus.BLOCKED
    assert http_client.urls == []
    assert _table_count(repositories.connection, "downloads") == 0


@pytest.mark.revised
def test_interactive_unknown_calls_the_prompt(tmp_path: Path) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    document_id, document_url_id = _document_with_url(repositories)
    prompt = FakePrompt(response=True)
    decision = _decision(LicenseStatus.UNKNOWN)
    service = DownloadService(
        repositories=repositories,
        http_client=FakeHttpClient([_pdf_response()]),
        config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
        prompt=prompt,
    )

    result = service.download(
        document_id=document_id,
        document_url_id=document_url_id,
        url="https://example.org/reports/unknown.pdf",
        license_decision=decision,
        interactive=True,
    )

    assert prompt.decisions == [decision]
    assert result.status is DownloadStatus.DOWNLOADED


@pytest.mark.revised
def test_non_interactive_unknown_does_not_download(tmp_path: Path) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    document_id, document_url_id = _document_with_url(repositories)
    http_client = FakeHttpClient([_pdf_response()])
    prompt = FakePrompt(response=True)
    service = DownloadService(
        repositories=repositories,
        http_client=http_client,
        config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
        prompt=prompt,
    )

    result = service.download(
        document_id=document_id,
        document_url_id=document_url_id,
        url="https://example.org/reports/unknown.pdf",
        license_decision=_decision(LicenseStatus.UNKNOWN),
        interactive=False,
    )

    assert result.status is DownloadStatus.BLOCKED
    assert prompt.decisions == []
    assert http_client.urls == []
    assert _table_count(repositories.connection, "downloads") == 0


@pytest.mark.revised
def test_interrupted_download_is_not_recorded_as_success(tmp_path: Path) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    document_id, document_url_id = _document_with_url(repositories)
    service = DownloadService(
        repositories=repositories,
        http_client=FakeHttpClient([KeyboardInterrupt()]),
        config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
    )

    with pytest.raises(KeyboardInterrupt):
        service.download(
            document_id=document_id,
            document_url_id=document_url_id,
            url="https://example.org/reports/interrupted.pdf",
            license_decision=_decision(LicenseStatus.ALLOWED),
        )

    assert _table_count(repositories.connection, "downloads") == 0
    assert not list((tmp_path / "pdfs").glob("*.pdf"))


def _document_with_url(repositories: RepositorySet) -> tuple[int, int]:
    source_id = repositories.sources.upsert(SourceKind.SITEMAP)
    document_id = repositories.documents.upsert(Document(title="Allowed Report"))
    document_url_id = repositories.document_urls.upsert(
        document_id=document_id,
        source_id=source_id,
        document_url=DocumentUrl(
            url="https://example.org/reports/allowed.pdf",
            canonical_url="https://example.org/reports/allowed.pdf",
            source=SourceKind.SITEMAP,
            discovery_method=DiscoveryMethod.SITEMAP,
            url_type=UrlType.PDF,
            confidence=0.9,
        ),
        metadata={},
    )
    return document_id, document_url_id


def _decision(
    status: LicenseStatus,
    *,
    evidence: list[LicenseEvidence] | None = None,
) -> LicenseDecision:
    return LicenseDecision(
        status=status,
        reason=f"Test decision: {status.value}",
        evidence=evidence or [],
    )


def _pdf_response() -> HttpResponse:
    return HttpResponse(
        status_code=200,
        headers={"content-type": "application/pdf"},
        content=PDF_BYTES,
        url="https://example.org/reports/allowed.pdf",
    )


def _table_count(connection: sqlite3.Connection, table_name: str) -> int:
    row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    return int(row[0])
