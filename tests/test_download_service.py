# Consolidated test module. See docs/audit-tests/03-centralize-test-files.md.
# Consolidated from test_downloader.py

from hashlib import sha256
from pathlib import Path
import sqlite3

import pytest

from bookhound.database import initialize_database
from bookhound.downloader import DownloadService, DownloadServiceConfig
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


_downloader_PDF_BYTES = b"%PDF-1.7\nallowed test pdf\n%%EOF\n"


@pytest.mark.revised
def test_allowed_downloads_and_records_file(
    tmp_path: Path,
    license_decision_factory,
    pdf_response_factory,
    recording_http_client_factory,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    document_id, document_url_id = _document_with_url(repositories)
    service = DownloadService(
        repositories=repositories,
        http_client=recording_http_client_factory.from_queue(
            [
                pdf_response_factory(
                    url="https://example.org/reports/allowed.pdf",
                    content=_downloader_PDF_BYTES,
                )
            ]
        ),
        config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
    )

    result = service.download(
        document_id=document_id,
        document_url_id=document_url_id,
        url="https://example.org/reports/allowed.pdf",
        license_decision=license_decision_factory(LicenseStatus.ALLOWED),
    )

    with sqlite3.connect(tmp_path / "bookhound.sqlite3") as connection:
        row = connection.execute(
            """
            SELECT local_path, sha256, size_bytes, status, license_evidence_id
            FROM downloads
            """
        ).fetchone()

    assert result.status is DownloadStatus.DOWNLOADED
    assert Path(row[0]).read_bytes() == _downloader_PDF_BYTES
    assert row[1] == sha256(_downloader_PDF_BYTES).hexdigest()
    assert row[2] == len(_downloader_PDF_BYTES)
    assert row[3] == "downloaded"
    assert row[4] is None


@pytest.mark.revised
def test_manually_authorized_download_records_authorization_evidence_used(
    tmp_path: Path,
    license_decision_factory,
    pdf_response_factory,
    recording_http_client_factory,
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
        http_client=recording_http_client_factory.from_queue(
            [
                pdf_response_factory(
                    url="https://example.org/reports/allowed.pdf",
                    content=_downloader_PDF_BYTES,
                )
            ]
        ),
        config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
    )

    result = service.download(
        document_id=document_id,
        document_url_id=document_url_id,
        url="https://example.org/reports/allowed.pdf",
        license_decision=license_decision_factory(
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
def test_denied_does_not_download(
    tmp_path: Path,
    count_rows_helper,
    license_decision_factory,
    pdf_response_factory,
    recording_http_client_factory,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    document_id, document_url_id = _document_with_url(repositories)
    http_client = recording_http_client_factory.from_queue(
        [
            pdf_response_factory(
                url="https://example.org/reports/allowed.pdf",
                content=_downloader_PDF_BYTES,
            )
        ]
    )
    service = DownloadService(
        repositories=repositories,
        http_client=http_client,
        config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
    )

    result = service.download(
        document_id=document_id,
        document_url_id=document_url_id,
        url="https://example.org/reports/blocked.pdf",
        license_decision=license_decision_factory(LicenseStatus.DENIED),
    )

    assert result.status is DownloadStatus.BLOCKED
    assert http_client.urls == []
    assert count_rows_helper(repositories.connection, "downloads") == 0


@pytest.mark.revised
def test_interactive_unknown_calls_the_prompt(
    tmp_path: Path,
    license_decision_factory,
    pdf_response_factory,
    prompt_stub_factory,
    recording_http_client_factory,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    document_id, document_url_id = _document_with_url(repositories)
    prompt = prompt_stub_factory(response=True)
    decision = license_decision_factory(LicenseStatus.UNKNOWN)
    service = DownloadService(
        repositories=repositories,
        http_client=recording_http_client_factory.from_queue(
            [
                pdf_response_factory(
                    url="https://example.org/reports/allowed.pdf",
                    content=_downloader_PDF_BYTES,
                )
            ]
        ),
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
def test_non_interactive_unknown_does_not_download(
    tmp_path: Path,
    count_rows_helper,
    license_decision_factory,
    pdf_response_factory,
    prompt_stub_factory,
    recording_http_client_factory,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    document_id, document_url_id = _document_with_url(repositories)
    http_client = recording_http_client_factory.from_queue(
        [
            pdf_response_factory(
                url="https://example.org/reports/allowed.pdf",
                content=_downloader_PDF_BYTES,
            )
        ]
    )
    prompt = prompt_stub_factory(response=True)
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
        license_decision=license_decision_factory(LicenseStatus.UNKNOWN),
        interactive=False,
    )

    assert result.status is DownloadStatus.BLOCKED
    assert prompt.decisions == []
    assert http_client.urls == []
    assert count_rows_helper(repositories.connection, "downloads") == 0


@pytest.mark.revised
def test_interrupted_download_is_not_recorded_as_success(
    tmp_path: Path,
    count_rows_helper,
    license_decision_factory,
    recording_http_client_factory,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    document_id, document_url_id = _document_with_url(repositories)
    service = DownloadService(
        repositories=repositories,
        http_client=recording_http_client_factory.from_queue([KeyboardInterrupt()]),
        config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
    )

    with pytest.raises(KeyboardInterrupt):
        service.download(
            document_id=document_id,
            document_url_id=document_url_id,
            url="https://example.org/reports/interrupted.pdf",
            license_decision=license_decision_factory(LicenseStatus.ALLOWED),
        )

    assert count_rows_helper(repositories.connection, "downloads") == 0
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


# Consolidated from test_downloader_response_validation.py

from hashlib import sha256
from pathlib import Path
import sqlite3

import pytest

from bookhound.database import initialize_database
from bookhound.downloader import DownloadService, DownloadServiceConfig
from bookhound.models import (
    DiscoveryMethod,
    Document,
    DocumentUrl,
    DownloadStatus,
    LicenseStatus,
    SourceKind,
    UrlType,
)
from bookhound.repositories import RepositorySet


_downloader_response_validation_PDF_BYTES = b"%PDF-1.7\nvalidated test pdf\n%%EOF\n"


@pytest.mark.revised
def test_non_successful_response_is_failed_without_writing_or_recording_downloaded(
    tmp_path: Path,
    http_response_factory,
    license_decision_factory,
    recording_http_client_factory,
) -> None:
    service, repositories, url = _download_service(
        tmp_path,
        http_response_factory(
            url="https://example.org/reports/missing.pdf",
            status_code=404,
            content_type="text/html",
            content=b"<html>not found</html>",
        ),
        recording_http_client_factory,
    )

    result = service.download(
        document_id=1,
        document_url_id=1,
        url=url,
        license_decision=license_decision_factory(LicenseStatus.ALLOWED),
    )

    assert result.status is DownloadStatus.FAILED
    assert _download_statuses(repositories.connection) == []
    assert not list((tmp_path / "pdfs").glob("*.pdf"))


@pytest.mark.revised
def test_html_response_is_failed_without_writing_or_recording_downloaded(
    tmp_path: Path,
    http_response_factory,
    license_decision_factory,
    recording_http_client_factory,
) -> None:
    service, repositories, url = _download_service(
        tmp_path,
        http_response_factory(
            url="https://example.org/reports/login.pdf",
            status_code=200,
            content_type="text/html; charset=utf-8",
            content=b"<html><title>Login required</title></html>",
        ),
        recording_http_client_factory,
    )

    result = service.download(
        document_id=1,
        document_url_id=1,
        url=url,
        license_decision=license_decision_factory(LicenseStatus.ALLOWED),
    )

    assert result.status is DownloadStatus.FAILED
    assert "downloaded" not in _download_statuses(repositories.connection)
    assert not list((tmp_path / "pdfs").glob("*.pdf"))


@pytest.mark.revised
def test_pdf_content_type_with_non_pdf_body_is_failed(
    tmp_path: Path,
    http_response_factory,
    license_decision_factory,
    recording_http_client_factory,
) -> None:
    service, repositories, url = _download_service(
        tmp_path,
        http_response_factory(
            url="https://example.org/reports/not-a-real-pdf.pdf",
            status_code=200,
            content_type="application/pdf",
            content=b"This is not a PDF body.",
        ),
        recording_http_client_factory,
    )

    result = service.download(
        document_id=1,
        document_url_id=1,
        url=url,
        license_decision=license_decision_factory(LicenseStatus.ALLOWED),
    )

    assert result.status is DownloadStatus.FAILED
    assert "downloaded" not in _download_statuses(repositories.connection)
    assert not list((tmp_path / "pdfs").glob("*.pdf"))


@pytest.mark.revised
def test_empty_pdf_response_is_failed(
    tmp_path: Path,
    http_response_factory,
    license_decision_factory,
    recording_http_client_factory,
) -> None:
    service, repositories, url = _download_service(
        tmp_path,
        http_response_factory(
            url="https://example.org/reports/empty.pdf",
            status_code=200,
            content_type="application/pdf",
            content=b"",
        ),
        recording_http_client_factory,
    )

    result = service.download(
        document_id=1,
        document_url_id=1,
        url=url,
        license_decision=license_decision_factory(LicenseStatus.ALLOWED),
    )

    assert result.status is DownloadStatus.FAILED
    assert "downloaded" not in _download_statuses(repositories.connection)
    assert not list((tmp_path / "pdfs").glob("*.pdf"))


@pytest.mark.revised
def test_valid_pdf_response_still_records_download(
    tmp_path: Path,
    http_response_factory,
    license_decision_factory,
    recording_http_client_factory,
) -> None:
    service, repositories, url = _download_service(
        tmp_path,
        http_response_factory(
            url="https://example.org/reports/valid.pdf",
            status_code=200,
            content_type="application/pdf",
            content=_downloader_response_validation_PDF_BYTES,
        ),
        recording_http_client_factory,
    )

    result = service.download(
        document_id=1,
        document_url_id=1,
        url=url,
        license_decision=license_decision_factory(LicenseStatus.ALLOWED),
    )

    row = repositories.connection.execute(
        "SELECT local_path, sha256, size_bytes, status FROM downloads"
    ).fetchone()

    assert result.status is DownloadStatus.DOWNLOADED
    assert row[1] == sha256(_downloader_response_validation_PDF_BYTES).hexdigest()
    assert row[2] == len(_downloader_response_validation_PDF_BYTES)
    assert row[3] == "downloaded"
    assert Path(row[0]).read_bytes() == _downloader_response_validation_PDF_BYTES


def _download_service(
    tmp_path: Path,
    response: object,
    recording_http_client_factory,
) -> tuple[DownloadService, RepositorySet, str]:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    url = response.url
    _downloader_response_validation_save_document_url(repositories, url)
    return (
        DownloadService(
            repositories=repositories,
            http_client=recording_http_client_factory.single(response),
            config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
        ),
        repositories,
        url,
    )


def _downloader_response_validation_save_document_url(repositories: RepositorySet, url: str) -> tuple[int, int]:
    source_id = repositories.sources.upsert(SourceKind.SITEMAP)
    document_id = repositories.documents.upsert(Document(title="Validated Report"))
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
    return document_id, document_url_id


def _download_statuses(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            "SELECT status FROM downloads ORDER BY id"
        ).fetchall()
    ]


# Consolidated from test_downloader_path_identity.py

from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from bookhound.database import initialize_database
from bookhound.downloader import DownloadService, DownloadServiceConfig
from bookhound.models import (
    DiscoveryMethod,
    Document,
    DocumentUrl,
    DownloadStatus,
    LicenseStatus,
    SourceKind,
    UrlType,
)
from bookhound.repositories import RepositorySet


PUBLISHER_A_PDF = b"%PDF-1.7\npublisher a report\n%%EOF\n"
PUBLISHER_B_PDF = b"%PDF-1.7\npublisher b report\n%%EOF\n"
REPORT_V1_PDF = b"%PDF-1.7\nfirst report version\n%%EOF\n"
REPORT_V2_PDF = b"%PDF-1.7\nsecond report version\n%%EOF\n"


@pytest.mark.revised
def test_same_basename_from_different_document_urls_gets_distinct_artifacts(
    tmp_path: Path,
    license_decision_factory,
    pdf_response_factory,
    recording_http_client_factory,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    first_url = "https://publisher-a.example/reports/report.pdf"
    second_url = "https://publisher-b.example/reports/report.pdf"
    first_document_id, first_document_url_id = _downloader_path_identity_save_document_url(
        repositories,
        title="Publisher A Report",
        url=first_url,
    )
    second_document_id, second_document_url_id = _downloader_path_identity_save_document_url(
        repositories,
        title="Publisher B Report",
        url=second_url,
    )
    service = DownloadService(
        repositories=repositories,
        http_client=recording_http_client_factory.from_queue(
            [
                pdf_response_factory(url=first_url, content=PUBLISHER_A_PDF),
                pdf_response_factory(url=second_url, content=PUBLISHER_B_PDF),
            ]
        ),
        config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
    )

    first_result = service.download(
        document_id=first_document_id,
        document_url_id=first_document_url_id,
        url=first_url,
        license_decision=license_decision_factory(LicenseStatus.ALLOWED),
    )
    second_result = service.download(
        document_id=second_document_id,
        document_url_id=second_document_url_id,
        url=second_url,
        license_decision=license_decision_factory(LicenseStatus.ALLOWED),
    )

    first_path = Path(first_result.local_path)
    second_path = Path(second_result.local_path)
    rows = _download_rows(repositories.connection)

    assert first_result.status is DownloadStatus.DOWNLOADED
    assert second_result.status is DownloadStatus.DOWNLOADED
    assert first_path != second_path
    assert _path_carries_identity(first_path, first_document_url_id)
    assert _path_carries_identity(second_path, second_document_url_id)
    assert _path_keeps_readable_pdf_name(first_path, "report")
    assert _path_keeps_readable_pdf_name(second_path, "report")
    assert len(rows) == 2
    _assert_download_row_matches_file(
        rows[0],
        document_url_id=first_document_url_id,
        expected_content=PUBLISHER_A_PDF,
    )
    _assert_download_row_matches_file(
        rows[1],
        document_url_id=second_document_url_id,
        expected_content=PUBLISHER_B_PDF,
    )


@pytest.mark.revised
def test_repeated_document_url_with_changed_bytes_preserves_each_artifact(
    tmp_path: Path,
    license_decision_factory,
    pdf_response_factory,
    recording_http_client_factory,
) -> None:
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    url = "https://publisher.example/reports/report.pdf"
    document_id, document_url_id = _downloader_path_identity_save_document_url(
        repositories,
        title="Versioned Report",
        url=url,
    )
    service = DownloadService(
        repositories=repositories,
        http_client=recording_http_client_factory.from_queue(
            [
                pdf_response_factory(url=url, content=REPORT_V1_PDF),
                pdf_response_factory(url=url, content=REPORT_V2_PDF),
            ]
        ),
        config=DownloadServiceConfig(download_directory=tmp_path / "pdfs"),
    )

    first_result = service.download(
        document_id=document_id,
        document_url_id=document_url_id,
        url=url,
        license_decision=license_decision_factory(LicenseStatus.ALLOWED),
    )
    second_result = service.download(
        document_id=document_id,
        document_url_id=document_url_id,
        url=url,
        license_decision=license_decision_factory(LicenseStatus.ALLOWED),
    )

    first_path = Path(first_result.local_path)
    second_path = Path(second_result.local_path)
    rows = _download_rows(repositories.connection)

    assert first_result.status is DownloadStatus.DOWNLOADED
    assert second_result.status is DownloadStatus.DOWNLOADED
    assert first_path != second_path
    assert _path_carries_identity(first_path, document_url_id)
    assert _path_carries_identity(second_path, document_url_id)
    assert _path_keeps_readable_pdf_name(first_path, "report")
    assert _path_keeps_readable_pdf_name(second_path, "report")
    assert len(rows) == 2
    _assert_download_row_matches_file(
        rows[0],
        document_url_id=document_url_id,
        expected_content=REPORT_V1_PDF,
    )
    _assert_download_row_matches_file(
        rows[1],
        document_url_id=document_url_id,
        expected_content=REPORT_V2_PDF,
    )


def _downloader_path_identity_save_document_url(
    repositories: RepositorySet,
    *,
    title: str,
    url: str,
) -> tuple[int, int]:
    source_id = repositories.sources.upsert(SourceKind.SITEMAP)
    document_id = repositories.documents.upsert(Document(title=title))
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
    return document_id, document_url_id


def _download_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT document_url_id, local_path, sha256, size_bytes, status
        FROM downloads
        ORDER BY id
        """
    ).fetchall()
    return [
        {
            "document_url_id": int(row[0]),
            "local_path": str(row[1]),
            "sha256": str(row[2]),
            "size_bytes": int(row[3]),
            "status": str(row[4]),
        }
        for row in rows
    ]


def _path_carries_identity(path: Path, document_url_id: int) -> bool:
    return str(document_url_id) in path.as_posix()


def _path_keeps_readable_pdf_name(path: Path, readable_name: str) -> bool:
    return path.suffix == ".pdf" and readable_name in path.stem


def _assert_download_row_matches_file(
    row: dict[str, Any],
    *,
    document_url_id: int,
    expected_content: bytes,
) -> None:
    local_path = Path(row["local_path"])
    assert row["document_url_id"] == document_url_id
    assert row["status"] == "downloaded"
    assert row["sha256"] == sha256(expected_content).hexdigest()
    assert row["size_bytes"] == len(expected_content)
    assert local_path.read_bytes() == expected_content
