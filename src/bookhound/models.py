from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_serializer, field_validator


class ExecutionMode(str, Enum):
    SEARCH = "search"
    COLLECT = "collect"
    DOWNLOAD = "download"
    DAEMON = "daemon"
    EXPORT = "export"


class UrlType(str, Enum):
    PDF = "pdf"
    LANDING_PAGE = "landing_page"
    OTHER = "other"


class SourceKind(str, Enum):
    GOOGLE = "google"
    ARXIV = "arxiv"
    UNPAYWALL = "unpaywall"
    COMMON_CRAWL = "common_crawl"
    SEED_CRAWLER = "seed_crawler"
    SITEMAP = "sitemap"
    LINK_EXPANSION = "link_expansion"


class DiscoveryMethod(str, Enum):
    API = "api"
    PUBLIC_INDEX = "public_index"
    CRAWL = "crawl"
    SITEMAP = "sitemap"
    LINK_EXPANSION = "link_expansion"
    ENRICHMENT = "enrichment"


class LicenseStatus(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    UNKNOWN = "unknown"
    MANUALLY_AUTHORIZED = "manually_authorized"


class DownloadStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADED = "downloaded"
    BLOCKED = "blocked"
    FAILED = "failed"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_non_empty(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("Value must not be empty.")
    return value


def _format_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class BookhoundModel(BaseModel):
    @field_serializer("*", when_used="json")
    def serialize_datetimes(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return _format_datetime(value)
        return value


class SearchQuery(BookhoundModel):
    keyword: str
    mode: ExecutionMode
    variants: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("keyword")
    @classmethod
    def validate_keyword(cls, value: str) -> str:
        return _validate_non_empty(value)


class RawCandidate(BookhoundModel):
    title: str
    url: str
    source: SourceKind
    discovery_method: DiscoveryMethod
    query: str
    snippet: str | None = None
    adapter_score: float | None = Field(default=None, ge=0.0, le=1.0)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    discovered_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "url", "query")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _validate_non_empty(value)


class Document(BookhoundModel):
    title: str
    authors: list[str] = Field(default_factory=list)
    doi: str | None = None
    isbn: str | None = None
    year: int | None = None
    language: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _validate_non_empty(value)


class DocumentUrl(BookhoundModel):
    url: str
    canonical_url: str
    source: SourceKind
    discovery_method: DiscoveryMethod
    url_type: UrlType
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    discovered_at: datetime = Field(default_factory=_utc_now)
    http_status: int | None = None

    @field_validator("url", "canonical_url")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _validate_non_empty(value)


class LicenseEvidence(BookhoundModel):
    source: str
    evidence_type: str
    value: str
    suggested_status: LicenseStatus
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    collected_at: datetime = Field(default_factory=_utc_now)

    @field_validator("source", "evidence_type", "value")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _validate_non_empty(value)


class LicenseDecision(BookhoundModel):
    status: LicenseStatus
    reason: str
    evidence: list[LicenseEvidence] = Field(default_factory=list)
    decided_at: datetime = Field(default_factory=_utc_now)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _validate_non_empty(value)


class DownloadRecord(BookhoundModel):
    url: str
    local_path: str
    status: DownloadStatus
    sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    license_decision: LicenseDecision | None = None
    downloaded_at: datetime | None = None
    error: str | None = None

    @field_validator("url", "local_path")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _validate_non_empty(value)


class SourceResult(BookhoundModel):
    source: SourceKind
    discovery_method: DiscoveryMethod
    candidates: list[RawCandidate] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=_utc_now)
