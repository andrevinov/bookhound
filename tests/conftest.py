from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from bookhound.database import initialize_database
from bookhound.discovery_pipeline import DiscoveryPipelineResult, DiscoveryStepResult
from bookhound.http_client import HttpResponse
from bookhound.jobs_daemon_export import DaemonConfig
from bookhound.models import (
    DiscoveryMethod,
    Document,
    DocumentUrl,
    DownloadRecord,
    DownloadStatus,
    LicenseDecision,
    LicenseEvidence,
    LicenseStatus,
    PersistedDownloadCandidate,
    PersistedLicenseEvidence,
    RawCandidate,
    SourceKind,
    UrlType,
)
from bookhound.query_planner import PlannedQueryVariant, QueryPlan
from bookhound.repositories import RepositorySet


class RecordingHttpClient:
    def __init__(
        self,
        *,
        responses: list[HttpResponse | BaseException] | None = None,
        responses_by_url: Mapping[str, HttpResponse | BaseException] | None = None,
        single_response: HttpResponse | BaseException | None = None,
        default_response: HttpResponse | BaseException | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.responses_by_url = dict(responses_by_url or {})
        self.single_response = single_response
        self.default_response = default_response
        self.urls: list[str] = []
        self.rate_limit_keys: list[str | None] = []
        self.cache_flags: list[bool] = []

    @classmethod
    def from_queue(
        cls,
        responses: Sequence[HttpResponse | BaseException],
    ) -> "RecordingHttpClient":
        return cls(responses=list(responses))

    @classmethod
    def from_mapping(
        cls,
        responses_by_url: Mapping[str, HttpResponse | BaseException],
        *,
        default_response: HttpResponse | BaseException | None = None,
    ) -> "RecordingHttpClient":
        return cls(
            responses_by_url=responses_by_url,
            default_response=default_response,
        )

    @classmethod
    def single(
        cls,
        response: HttpResponse | BaseException,
    ) -> "RecordingHttpClient":
        return cls(single_response=response)

    @classmethod
    def raising(cls, error: BaseException) -> "RecordingHttpClient":
        return cls(single_response=error)

    def get(
        self,
        url: str,
        *,
        rate_limit_key: str | None = None,
        cache: bool = False,
    ) -> HttpResponse:
        self.urls.append(url)
        self.rate_limit_keys.append(rate_limit_key)
        self.cache_flags.append(cache)

        if self.responses:
            return _http_response_or_raise(self.responses.pop(0))
        if url in self.responses_by_url:
            return _http_response_or_raise(self.responses_by_url[url])
        if self.single_response is not None:
            return _http_response_or_raise(self.single_response)
        if self.default_response is not None:
            return _http_response_or_raise(self.default_response)
        raise KeyError(url)


def _http_response_or_raise(response: HttpResponse | BaseException) -> HttpResponse:
    if isinstance(response, BaseException):
        raise response
    return response


def http_response(
    *,
    url: str,
    content: bytes,
    content_type: str,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
) -> HttpResponse:
    response_headers = {"content-type": content_type}
    if headers is not None:
        response_headers.update(headers)
    return HttpResponse(
        status_code=status_code,
        headers=response_headers,
        content=content,
        url=url,
    )


def json_response(
    *,
    url: str,
    payload: Mapping[str, object],
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
) -> HttpResponse:
    return http_response(
        url=url,
        content=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
        status_code=status_code,
        headers=headers,
    )


def xml_response(
    *,
    url: str,
    content: bytes,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
) -> HttpResponse:
    return http_response(
        url=url,
        content=content,
        content_type="application/xml",
        status_code=status_code,
        headers=headers,
    )


def html_response(
    *,
    url: str,
    content: bytes,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
) -> HttpResponse:
    return http_response(
        url=url,
        content=content,
        content_type="text/html",
        status_code=status_code,
        headers=headers,
    )


def text_response(
    *,
    url: str,
    content: bytes,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
) -> HttpResponse:
    return http_response(
        url=url,
        content=content,
        content_type="text/plain",
        status_code=status_code,
        headers=headers,
    )


def pdf_response(
    *,
    url: str,
    content: bytes,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
) -> HttpResponse:
    return http_response(
        url=url,
        content=content,
        content_type="application/pdf",
        status_code=status_code,
        headers=headers,
    )


def write_bookhound_config(
    tmp_path: Path,
    *,
    database_path: Path | str | None = None,
    pdf_directory: Path | str | None = None,
    raw_sections: str | Sequence[str] | None = None,
    filename: str = "bookhound.toml",
) -> Path:
    config_path = tmp_path / filename
    sections: list[str] = []
    path_lines: list[str] = []
    if database_path is not None:
        path_lines.append(f'database_path = "{database_path}"')
    if pdf_directory is not None:
        path_lines.append(f'pdf_directory = "{pdf_directory}"')
    if path_lines:
        sections.append("[paths]\n" + "\n".join(path_lines))

    if raw_sections is not None:
        if isinstance(raw_sections, str):
            sections.append(raw_sections.strip())
        else:
            sections.extend(section.strip() for section in raw_sections)

    config_path.write_text("\n\n".join(sections).strip(), encoding="utf-8")
    return config_path


def write_sitemap_runtime_config(
    tmp_path: Path,
    *,
    database_path: Path | str,
    pdf_directory: Path | str | None = None,
    domain_roots: Sequence[str] = ("https://example.org/",),
    filename: str = "bookhound.toml",
) -> Path:
    roots = ", ".join(f'"{root}"' for root in domain_roots)
    return write_bookhound_config(
        tmp_path,
        database_path=database_path,
        pdf_directory=pdf_directory,
        raw_sections=f"""
[sources.arxiv]
enabled = false

[sources.common_crawl]
enabled = false

[sources.sitemap]
enabled = true
domain_roots = [{roots}]

[sources.link_expansion]
enabled = false
""",
        filename=filename,
    )


def write_isolated_download_config(
    tmp_path: Path,
    *,
    database_path: Path | str,
    pdf_directory: Path | str,
    filename: str = "bookhound.toml",
) -> Path:
    return write_bookhound_config(
        tmp_path,
        database_path=database_path,
        pdf_directory=pdf_directory,
        raw_sections="""
[sources.arxiv]
enabled = false

[sources.common_crawl]
enabled = false

[sources.seed_crawler]
enabled = false

[sources.sitemap]
enabled = false

[sources.link_expansion]
enabled = false
""",
        filename=filename,
    )


def write_minimal_paths_config(
    tmp_path: Path,
    *,
    database_path: Path | str,
    pdf_directory: Path | str | None = None,
    filename: str = "bookhound.toml",
) -> Path:
    return write_bookhound_config(
        tmp_path,
        database_path=database_path,
        pdf_directory=pdf_directory,
        filename=filename,
    )


def source_names(pipeline: object) -> list[SourceKind]:
    return [source.source_name for source in pipeline.sources]


def source_by_name(pipeline: object, source_name: SourceKind) -> object:
    for source in pipeline.sources:
        if source.source_name is source_name:
            return source
    raise AssertionError(f"Missing source: {source_name.value}")


def clear_optional_source_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_name in (
        "BOOKHOUND_GOOGLE_API_KEY",
        "BOOKHOUND_GOOGLE_SEARCH_ENGINE_ID",
        "BOOKHOUND_UNPAYWALL_EMAIL",
    ):
        monkeypatch.delenv(env_name, raising=False)


def clear_bookhound_env(
    monkeypatch: pytest.MonkeyPatch,
    names: Sequence[str] | None = None,
) -> None:
    env_names = names or [
        name
        for name in os.environ
        if name.startswith("BOOKHOUND_")
    ]
    for env_name in env_names:
        monkeypatch.delenv(env_name, raising=False)


class RecordingPipeline:
    def __init__(
        self,
        candidates: Sequence[RawCandidate],
        *,
        errors: Sequence[str] | None = None,
        events: Sequence[dict[str, object]] | None = None,
    ) -> None:
        self.candidates = list(candidates)
        self.errors = list(errors or [])
        self.events = list(events or [])
        self.searched_keywords: list[str] = []

    def search(self, keyword: str) -> DiscoveryPipelineResult:
        self.searched_keywords.append(keyword)
        return discovery_result(
            keyword=keyword,
            candidates=self.candidates,
            errors=self.errors,
            events=self.events,
        )

    def iter_search(self, keyword: str):
        self.searched_keywords.append(keyword)
        result = discovery_result(
            keyword=keyword,
            candidates=self.candidates,
            errors=self.errors,
            events=self.events,
        )
        if not result.candidates and not result.errors and not result.events:
            return

        first_candidate = result.candidates[0] if result.candidates else None
        yield DiscoveryStepResult(
            query_plan=result.query_plan,
            variant=result.query_plan.variants[0],
            source=(
                first_candidate.source
                if first_candidate is not None
                else SourceKind.SITEMAP
            ),
            discovery_method=(
                first_candidate.discovery_method
                if first_candidate is not None
                else DiscoveryMethod.SITEMAP
            ),
            status=(
                "failed" if result.errors and not result.candidates else "completed"
            ),
            candidates=result.candidates,
            errors=result.errors,
            events=result.events,
        )


class FailingPipeline:
    def __init__(self, message: str = "Unexpected discovery pipeline call.") -> None:
        self.message = message

    def search(self, keyword: str) -> DiscoveryPipelineResult:
        raise AssertionError(self.message)

    def iter_search(self, keyword: str):
        raise AssertionError(self.message)
        yield


def discovery_result(
    *,
    keyword: str,
    candidates: Sequence[RawCandidate],
    errors: Sequence[str] | None = None,
    events: Sequence[dict[str, object]] | None = None,
    variants: Sequence[PlannedQueryVariant] | None = None,
) -> DiscoveryPipelineResult:
    return DiscoveryPipelineResult(
        query_plan=QueryPlan(
            keyword=keyword,
            variants=list(
                variants
                or [PlannedQueryVariant(label="quoted", query=f'"{keyword}"')]
            ),
        ),
        candidates=list(candidates),
        errors=list(errors or []),
        events=list(events or []),
    )


def raw_candidate(
    *,
    title: str = "Test Report",
    url: str = "https://example.org/reports/test.pdf",
    source: SourceKind = SourceKind.SITEMAP,
    discovery_method: DiscoveryMethod = DiscoveryMethod.SITEMAP,
    query: str = '"keyword"',
    snippet: str | None = None,
    adapter_score: float | None = None,
    score: float | None = 0.9,
    metadata: Mapping[str, Any] | None = None,
) -> RawCandidate:
    return RawCandidate(
        title=title,
        url=url,
        source=source,
        discovery_method=discovery_method,
        query=query,
        snippet=snippet,
        adapter_score=adapter_score,
        score=score,
        metadata=dict(metadata or {}),
    )


def sitemap_candidate(
    *,
    title: str = "Sitemap Report",
    url: str = "https://example.org/reports/sitemap.pdf",
    query: str = '"keyword"',
    score: float | None = 0.9,
    metadata: Mapping[str, Any] | None = None,
) -> RawCandidate:
    return raw_candidate(
        title=title,
        url=url,
        source=SourceKind.SITEMAP,
        discovery_method=DiscoveryMethod.SITEMAP,
        query=query,
        score=score,
        metadata=metadata,
    )


def common_crawl_candidate(
    *,
    title: str = "Common Crawl Report",
    url: str = "https://example.org/reports/common-crawl.pdf",
    query: str = '"keyword"',
    score: float | None = 0.75,
    metadata: Mapping[str, Any] | None = None,
) -> RawCandidate:
    return raw_candidate(
        title=title,
        url=url,
        source=SourceKind.COMMON_CRAWL,
        discovery_method=DiscoveryMethod.PUBLIC_INDEX,
        query=query,
        score=score,
        metadata=metadata,
    )


def candidate_with_canonical_metadata(
    *,
    title: str = "Canonical Report",
    url: str = "https://example.org/reports/canonical.pdf",
    canonical_url: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    **overrides: Any,
) -> RawCandidate:
    candidate_metadata = {
        "canonical_url": canonical_url or url,
        **dict(metadata or {}),
    }
    return raw_candidate(
        title=title,
        url=url,
        metadata=candidate_metadata,
        **overrides,
    )


def count_rows(connection: sqlite3.Connection, table_name: str) -> int:
    row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    return int(row[0])


@dataclass(frozen=True)
class SeededDocumentUrl:
    document_id: int
    document_url_id: int
    url: str
    canonical_url: str
    source_id: int
    license_evidence_ids: tuple[int, ...] = ()


def seed_document_url(
    repositories: RepositorySet,
    *,
    title: str = "Seeded Report",
    url: str = "https://example.org/reports/seeded.pdf",
    canonical_url: str | None = None,
    source: SourceKind = SourceKind.SITEMAP,
    discovery_method: DiscoveryMethod = DiscoveryMethod.SITEMAP,
    url_type: UrlType = UrlType.PDF,
    confidence: float | None = 0.9,
    doi: str | None = None,
    isbn: str | None = None,
    authors: Sequence[str] | None = None,
    year: int | None = None,
    language: str | None = None,
    document_metadata: Mapping[str, Any] | None = None,
    url_metadata: Mapping[str, Any] | None = None,
    evidence: LicenseEvidence | Sequence[LicenseEvidence] | None = None,
    evidence_metadata: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
) -> SeededDocumentUrl:
    source_id = repositories.sources.upsert(source)
    document_id = repositories.documents.upsert(
        Document(
            title=title,
            authors=list(authors or []),
            doi=doi,
            isbn=isbn,
            year=year,
            language=language,
            metadata=dict(document_metadata or {}),
        )
    )
    saved_canonical_url = canonical_url or url
    document_url_id = repositories.document_urls.upsert(
        document_id=document_id,
        source_id=source_id,
        document_url=DocumentUrl(
            url=url,
            canonical_url=saved_canonical_url,
            source=source,
            discovery_method=discovery_method,
            url_type=url_type,
            confidence=confidence,
        ),
        metadata=dict(url_metadata or {}),
    )
    evidence_items = _normalize_evidence_items(evidence)
    evidence_metadata_items = _normalize_evidence_metadata(
        evidence_metadata,
        count=len(evidence_items),
    )
    license_evidence_ids = tuple(
        repositories.license_evidence.add(
            document_id=document_id,
            document_url_id=document_url_id,
            evidence=evidence_item,
            metadata=dict(metadata_item),
        )
        for evidence_item, metadata_item in zip(
            evidence_items,
            evidence_metadata_items,
        )
    )
    return SeededDocumentUrl(
        document_id=document_id,
        document_url_id=document_url_id,
        url=url,
        canonical_url=saved_canonical_url,
        source_id=source_id,
        license_evidence_ids=license_evidence_ids,
    )


def _normalize_evidence_items(
    evidence: LicenseEvidence | Sequence[LicenseEvidence] | None,
) -> list[LicenseEvidence]:
    if evidence is None:
        return []
    if isinstance(evidence, LicenseEvidence):
        return [evidence]
    return list(evidence)


def _normalize_evidence_metadata(
    evidence_metadata: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
    *,
    count: int,
) -> list[Mapping[str, Any]]:
    if count == 0:
        return []
    if evidence_metadata is None:
        return [{} for _ in range(count)]
    if isinstance(evidence_metadata, Mapping):
        return [evidence_metadata for _ in range(count)]
    metadata_items = list(evidence_metadata)
    if len(metadata_items) != count:
        raise ValueError("Evidence metadata count must match evidence count.")
    return metadata_items


def persisted_download_candidate(
    *,
    title: str = "Persisted Report",
    url: str = "https://example.org/reports/persisted.pdf",
    canonical_url: str | None = None,
    document_id: int = 1,
    document_url_id: int = 1,
    source: SourceKind = SourceKind.SITEMAP,
    discovery_method: DiscoveryMethod = DiscoveryMethod.SITEMAP,
    query: str = '"keyword"',
    score: float | None = 0.9,
    metadata: Mapping[str, Any] | None = None,
    license_evidence: Sequence[PersistedLicenseEvidence] | None = None,
) -> PersistedDownloadCandidate:
    return PersistedDownloadCandidate(
        candidate=raw_candidate(
            title=title,
            url=url,
            source=source,
            discovery_method=discovery_method,
            query=query,
            score=score,
            metadata=metadata,
        ),
        canonical_url=canonical_url or url,
        document_id=document_id,
        document_url_id=document_url_id,
        license_evidence=list(license_evidence or []),
    )


def license_decision(
    status: LicenseStatus,
    *,
    reason: str | None = None,
    evidence: Sequence[LicenseEvidence] | None = None,
) -> LicenseDecision:
    return LicenseDecision(
        status=status,
        reason=reason or f"Test decision: {status.value}",
        evidence=list(evidence or []),
    )


class FixedStatusClassifier:
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
        return license_decision(
            self.status,
            reason=f"Recorded decision: {self.status.value}",
            evidence=evidence[:1],
        )


class StatusMapClassifier:
    def __init__(self, statuses_by_url: Mapping[str, LicenseStatus]) -> None:
        self.statuses_by_url = dict(statuses_by_url)
        self.calls: list[dict[str, object]] = []

    def classify(
        self,
        *,
        document_url: str,
        evidence: list[LicenseEvidence],
    ) -> LicenseDecision:
        self.calls.append({"document_url": document_url, "evidence": evidence})
        status = self.statuses_by_url[document_url]
        return license_decision(
            status,
            reason=f"Recorded decision for {document_url}.",
            evidence=evidence[:1],
        )


class PromptStub:
    def __init__(self, response: bool) -> None:
        self.response = response
        self.decisions: list[LicenseDecision] = []

    def confirm_unknown_license(self, decision: LicenseDecision) -> bool:
        self.decisions.append(decision)
        return self.response


class DecliningPrompt(PromptStub):
    def __init__(self) -> None:
        super().__init__(response=False)


class FailingPrompt:
    def __init__(
        self,
        message: str = "This prompt should not be called.",
    ) -> None:
        self.message = message

    def confirm_unknown_license(self, decision: LicenseDecision) -> bool:
        raise AssertionError(self.message)


class RecordingDownloadService:
    def __init__(
        self,
        *,
        failures_by_url: Mapping[str, Exception] | None = None,
        simulate_license_gate: bool = False,
        record_status: bool = True,
    ) -> None:
        self.failures_by_url = dict(failures_by_url or {})
        self.simulate_license_gate = simulate_license_gate
        self.record_status = record_status
        self.calls: list[dict[str, object]] = []

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
        call = {
            "document_id": document_id,
            "document_url_id": document_url_id,
            "url": url,
            "license_evidence_id": license_evidence_id,
            "interactive": interactive,
        }
        if self.record_status:
            call["status"] = license_decision.status
        self.calls.append(call)

        if url in self.failures_by_url:
            raise self.failures_by_url[url]

        status = DownloadStatus.DOWNLOADED
        if self.simulate_license_gate:
            if license_decision.status is LicenseStatus.DENIED:
                status = DownloadStatus.BLOCKED
            elif license_decision.status is LicenseStatus.UNKNOWN and not interactive:
                status = DownloadStatus.BLOCKED

        return DownloadRecord(
            url=url,
            local_path=f"/tmp/bookhound/{Path(url).name}",
            status=status,
            license_decision=license_decision,
        )


class RecordingJobExecutor:
    def __init__(self) -> None:
        self.keywords: list[str] = []

    def execute_job(self, keyword: str) -> None:
        self.keywords.append(keyword)


class FailingJobExecutor:
    def __init__(self, error: RuntimeError) -> None:
        self.error = error
        self.keywords: list[str] = []

    def execute_job(self, keyword: str) -> None:
        self.keywords.append(keyword)
        raise self.error


@dataclass(frozen=True)
class FixedClock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


def format_utc_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def parse_utc_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def write_lock_with_mtime(lock_path: Path, timestamp: datetime) -> None:
    lock_path.write_text("running", encoding="utf-8")
    epoch = timestamp.timestamp()
    os.utime(lock_path, (epoch, epoch))


def daemon_config(tmp_path: Path, **overrides: Any) -> DaemonConfig:
    values = {"lock_path": tmp_path / "bookhound.lock", **overrides}
    return DaemonConfig(**values)


def robots_with_sitemaps(*sitemap_urls: str) -> bytes:
    return "\n".join(
        ["User-agent: *", "Allow: /"]
        + [f"Sitemap: {sitemap_url}" for sitemap_url in sitemap_urls]
    ).encode("utf-8")


def sitemap_urlset_xml(
    urls: Sequence[str],
    *,
    lastmods: Mapping[str, str] | None = None,
) -> bytes:
    entries = []
    for url in urls:
        lastmod = ""
        if lastmods is not None and url in lastmods:
            lastmod = f"\n    <lastmod>{lastmods[url]}</lastmod>"
        entries.append(f"<url>\n    <loc>{url}</loc>{lastmod}\n  </url>")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  {"\n  ".join(entries)}
</urlset>
""".encode("utf-8")


def sitemap_index_xml(sitemap_urls: Sequence[str]) -> bytes:
    entries = "\n  ".join(
        f"<sitemap><loc>{sitemap_url}</loc></sitemap>"
        for sitemap_url in sitemap_urls
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  {entries}
</sitemapindex>
""".encode("utf-8")


def sitemap_http_client(
    *,
    sitemap_content: bytes,
    robots_content: bytes | None = None,
    root_url: str = "https://example.org/robots.txt",
    sitemap_url: str = "https://example.org/sitemap.xml",
) -> RecordingHttpClient:
    return RecordingHttpClient.from_mapping(
        {
            root_url: text_response(
                url=root_url,
                content=robots_content or robots_with_sitemaps(sitemap_url),
            ),
            sitemap_url: xml_response(url=sitemap_url, content=sitemap_content),
        }
    )


@pytest.fixture
def recording_http_client_factory() -> type[RecordingHttpClient]:
    return RecordingHttpClient


@pytest.fixture
def http_response_factory() -> Callable[..., HttpResponse]:
    return http_response


@pytest.fixture
def json_response_factory() -> Callable[..., HttpResponse]:
    return json_response


@pytest.fixture
def xml_response_factory() -> Callable[..., HttpResponse]:
    return xml_response


@pytest.fixture
def html_response_factory() -> Callable[..., HttpResponse]:
    return html_response


@pytest.fixture
def text_response_factory() -> Callable[..., HttpResponse]:
    return text_response


@pytest.fixture
def pdf_response_factory() -> Callable[..., HttpResponse]:
    return pdf_response


@pytest.fixture
def write_bookhound_config_factory() -> Callable[..., Path]:
    return write_bookhound_config


@pytest.fixture
def write_sitemap_runtime_config_factory() -> Callable[..., Path]:
    return write_sitemap_runtime_config


@pytest.fixture
def write_isolated_download_config_factory() -> Callable[..., Path]:
    return write_isolated_download_config


@pytest.fixture
def write_minimal_paths_config_factory() -> Callable[..., Path]:
    return write_minimal_paths_config


@pytest.fixture
def source_names_helper() -> Callable[[object], list[SourceKind]]:
    return source_names


@pytest.fixture
def source_by_name_helper() -> Callable[[object, SourceKind], object]:
    return source_by_name


@pytest.fixture
def clear_optional_source_credentials_helper() -> Callable[[pytest.MonkeyPatch], None]:
    return clear_optional_source_credentials


@pytest.fixture
def clear_bookhound_env_helper() -> Callable[..., None]:
    return clear_bookhound_env


@pytest.fixture
def recording_pipeline_factory() -> type[RecordingPipeline]:
    return RecordingPipeline


@pytest.fixture
def failing_pipeline_factory() -> type[FailingPipeline]:
    return FailingPipeline


@pytest.fixture
def discovery_result_factory() -> Callable[..., DiscoveryPipelineResult]:
    return discovery_result


@pytest.fixture
def raw_candidate_factory() -> Callable[..., RawCandidate]:
    return raw_candidate


@pytest.fixture
def sitemap_candidate_factory() -> Callable[..., RawCandidate]:
    return sitemap_candidate


@pytest.fixture
def common_crawl_candidate_factory() -> Callable[..., RawCandidate]:
    return common_crawl_candidate


@pytest.fixture
def candidate_with_canonical_metadata_factory() -> Callable[..., RawCandidate]:
    return candidate_with_canonical_metadata


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "bookhound.sqlite3"


@pytest.fixture
def database_connection(database_path: Path):
    connection = initialize_database(database_path)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def repositories(database_connection) -> RepositorySet:
    return RepositorySet(database_connection)


@pytest.fixture
def count_rows_helper() -> Callable[[sqlite3.Connection, str], int]:
    return count_rows


@pytest.fixture
def seed_document_url_factory() -> Callable[..., SeededDocumentUrl]:
    return seed_document_url


@pytest.fixture
def persisted_download_candidate_factory() -> Callable[..., PersistedDownloadCandidate]:
    return persisted_download_candidate


@pytest.fixture
def license_decision_factory() -> Callable[..., LicenseDecision]:
    return license_decision


@pytest.fixture
def fixed_status_classifier_factory() -> type[FixedStatusClassifier]:
    return FixedStatusClassifier


@pytest.fixture
def status_map_classifier_factory() -> type[StatusMapClassifier]:
    return StatusMapClassifier


@pytest.fixture
def prompt_stub_factory() -> type[PromptStub]:
    return PromptStub


@pytest.fixture
def declining_prompt_factory() -> type[DecliningPrompt]:
    return DecliningPrompt


@pytest.fixture
def failing_prompt_factory() -> type[FailingPrompt]:
    return FailingPrompt


@pytest.fixture
def recording_download_service_factory() -> type[RecordingDownloadService]:
    return RecordingDownloadService


@pytest.fixture
def recording_job_executor_factory() -> type[RecordingJobExecutor]:
    return RecordingJobExecutor


@pytest.fixture
def failing_job_executor_factory() -> type[FailingJobExecutor]:
    return FailingJobExecutor


@pytest.fixture
def fixed_clock_factory() -> type[FixedClock]:
    return FixedClock


@pytest.fixture
def format_utc_datetime_helper() -> Callable[[datetime], str]:
    return format_utc_datetime


@pytest.fixture
def parse_utc_datetime_helper() -> Callable[[str], datetime]:
    return parse_utc_datetime


@pytest.fixture
def write_lock_with_mtime_helper() -> Callable[[Path, datetime], None]:
    return write_lock_with_mtime


@pytest.fixture
def daemon_config_factory() -> Callable[..., DaemonConfig]:
    return daemon_config


@pytest.fixture
def robots_with_sitemaps_factory() -> Callable[..., bytes]:
    return robots_with_sitemaps


@pytest.fixture
def sitemap_urlset_xml_factory() -> Callable[..., bytes]:
    return sitemap_urlset_xml


@pytest.fixture
def sitemap_index_xml_factory() -> Callable[[Sequence[str]], bytes]:
    return sitemap_index_xml


@pytest.fixture
def sitemap_http_client_factory() -> Callable[..., RecordingHttpClient]:
    return sitemap_http_client
