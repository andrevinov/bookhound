from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from bookhound.http_client import (
    BookhoundHttpClient,
    HttpClientConfig,
    HttpClientProtocol,
    HttpResponse,
)
from bookhound.models import DownloadRecord, DownloadStatus, LicenseDecision, LicenseStatus
from bookhound.repositories import RepositorySet


class DownloadPrompt(Protocol):
    def confirm_unknown_license(self, decision: LicenseDecision) -> bool:
        raise NotImplementedError


@dataclass(frozen=True)
class DownloadServiceConfig:
    download_directory: Path
    request_timeout_seconds: float = 30.0
    user_agent: str = "Bookhound/0.1.0"


class DownloadService:
    def __init__(
        self,
        *,
        repositories: RepositorySet,
        http_client: HttpClientProtocol | None = None,
        config: DownloadServiceConfig,
        prompt: DownloadPrompt | None = None,
    ) -> None:
        self.repositories = repositories
        self.config = config
        self.prompt = prompt
        self.http_client = http_client or BookhoundHttpClient(
            HttpClientConfig(
                user_agent=self.config.user_agent,
                timeout_seconds=self.config.request_timeout_seconds,
            )
        )

    def download(
        self,
        *,
        document_id: int,
        document_url_id: int,
        url: str,
        license_decision: LicenseDecision,
        license_evidence_id: int | None = None,
        interactive: bool = False,
    ) -> DownloadRecord:
        if not self._license_allows_download(license_decision, interactive=interactive):
            return DownloadRecord(
                url=url,
                local_path=str(self._download_path(url)),
                status=DownloadStatus.BLOCKED,
                license_decision=license_decision,
            )

        response = self.http_client.get(url)
        validation_error = _download_response_error(response)
        if validation_error is not None:
            return DownloadRecord(
                url=url,
                local_path=str(self._download_path(url)),
                status=DownloadStatus.FAILED,
                license_decision=license_decision,
                error=validation_error,
            )

        file_hash = sha256(response.content).hexdigest()
        local_path = self._write_pdf_atomically(
            self._download_path(
                url,
                document_url_id=document_url_id,
                file_hash=file_hash,
            ),
            response.content,
        )
        size_bytes = len(response.content)
        downloaded_at = datetime.now(timezone.utc)

        self.repositories.downloads.add(
            document_id=document_id,
            document_url_id=document_url_id,
            local_path=str(local_path),
            status=DownloadStatus.DOWNLOADED,
            sha256=file_hash,
            size_bytes=size_bytes,
            license_evidence_id=license_evidence_id,
            downloaded_at=downloaded_at,
        )

        return DownloadRecord(
            url=url,
            local_path=str(local_path),
            status=DownloadStatus.DOWNLOADED,
            sha256=file_hash,
            size_bytes=size_bytes,
            license_decision=license_decision,
            downloaded_at=downloaded_at,
        )

    def _license_allows_download(
        self,
        decision: LicenseDecision,
        *,
        interactive: bool,
    ) -> bool:
        if decision.status in {
            LicenseStatus.ALLOWED,
            LicenseStatus.MANUALLY_AUTHORIZED,
        }:
            return True
        if decision.status is not LicenseStatus.UNKNOWN:
            return False
        if decision.unknown_license_confirmed:
            return True
        if not interactive or self.prompt is None:
            return False
        return self.prompt.confirm_unknown_license(decision)

    def _write_pdf_atomically(self, final_path: Path, content: bytes) -> Path:
        self.config.download_directory.mkdir(parents=True, exist_ok=True)
        temporary_path = final_path.with_suffix(f"{final_path.suffix}.tmp")

        try:
            temporary_path.write_bytes(content)
            temporary_path.replace(final_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise

        return final_path

    def _download_path(
        self,
        url: str,
        *,
        document_url_id: int | None = None,
        file_hash: str | None = None,
    ) -> Path:
        parsed = urlsplit(url)
        filename = Path(parsed.path).name or "download.pdf"
        if not filename.lower().endswith(".pdf"):
            filename = f"{filename}.pdf"
        if document_url_id is not None and file_hash is not None:
            readable_name = _safe_filename_part(Path(filename).stem)
            filename = f"document-url-{document_url_id}-{readable_name}-{file_hash}.pdf"
        return self.config.download_directory / filename


def _download_response_error(response: HttpResponse) -> str | None:
    if not 200 <= response.status_code < 300:
        return f"HTTP {response.status_code} response cannot be downloaded as a PDF."

    if not response.content:
        return "Downloaded response is empty."

    content_type = _normalized_content_type(response)
    if content_type and content_type not in {"application/pdf", "application/x-pdf"}:
        return f"Response content type is not PDF: {content_type}."

    if not response.content.startswith(b"%PDF-"):
        return "Downloaded response does not start with a PDF header."

    return None


def _normalized_content_type(response: HttpResponse) -> str:
    for header_name, header_value in response.headers.items():
        if header_name.lower() == "content-type":
            return str(header_value).split(";", 1)[0].strip().lower()
    return ""


def _safe_filename_part(value: str) -> str:
    safe_value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return safe_value or "download"
