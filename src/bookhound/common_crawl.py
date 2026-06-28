from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

from bookhound.http_client import BookhoundHttpClient, HttpClientConfig, HttpResponse
from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind
from bookhound.sources import SourceAdapter, SourceAvailabilityError
from bookhound.url_normalization import is_direct_pdf_url


COMMON_CRAWL_INDEX_BASE_URL = "https://index.commoncrawl.org"


class CommonCrawlHttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        rate_limit_key: str | None = None,
        cache: bool = False,
    ) -> HttpResponse:
        raise NotImplementedError


@dataclass(frozen=True)
class CommonCrawlAdapterConfig:
    crawl_indexes: list[str]
    result_limit: int = 1000
    filter_non_pdf: bool = True
    request_timeout_seconds: float = 30.0
    user_agent: str = "Bookhound/0.1.0"


@dataclass
class CommonCrawlAdapter(SourceAdapter):
    http_client: CommonCrawlHttpClient | None = None
    config: CommonCrawlAdapterConfig = field(
        default_factory=lambda: CommonCrawlAdapterConfig(crawl_indexes=[])
    )

    def __post_init__(self) -> None:
        SourceAdapter.__init__(
            self,
            source=SourceKind.COMMON_CRAWL,
            discovery_method=DiscoveryMethod.PUBLIC_INDEX,
        )
        if self.http_client is None:
            self.http_client = BookhoundHttpClient(
                HttpClientConfig(
                    user_agent=self.config.user_agent,
                    timeout_seconds=self.config.request_timeout_seconds,
                )
            )
        self.events: list[dict[str, object]] = []

    def search(self, query: str) -> list[RawCandidate]:
        candidates: list[RawCandidate] = []
        self.events = []

        for crawl_index in self.config.crawl_indexes:
            if len(candidates) >= self.config.result_limit:
                break

            response = self.http_client.get(
                _index_url(crawl_index=crawl_index, query=query),
                rate_limit_key=self.rate_limit_key,
            )
            if not 200 <= response.status_code < 300:
                raise SourceAvailabilityError(
                    SourceKind.COMMON_CRAWL,
                    f"Common Crawl index returned HTTP {response.status_code}.",
                )

            candidates.extend(
                self._parse_response(
                    response.content,
                    crawl_index=crawl_index,
                    query=query,
                )
            )

        return candidates[: self.config.result_limit]

    def _parse_response(
        self,
        content: bytes,
        *,
        crawl_index: str,
        query: str,
    ) -> list[RawCandidate]:
        candidates: list[RawCandidate] = []
        text = content.decode("utf-8")

        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            parsed_line = _parse_cdxj_line(line)
            if parsed_line is None:
                self._record_malformed_line(crawl_index, line_number)
                continue

            timestamp, record = parsed_line
            url = _string_value(record.get("url"))
            if self.config.filter_non_pdf and not _is_pdf_record(url, record):
                continue

            candidates.append(
                _candidate_from_record(
                    record,
                    timestamp=timestamp,
                    crawl_index=crawl_index,
                    query=query,
                )
            )

        return candidates

    def _record_malformed_line(self, crawl_index: str, line_number: int) -> None:
        self.events.append(
            {
                "event_type": "common_crawl.malformed_line",
                "message": "Ignored malformed CDXJ line.",
                "metadata": {
                    "crawl_index": crawl_index,
                    "line_number": line_number,
                },
            }
        )


def _index_url(*, crawl_index: str, query: str) -> str:
    return (
        f"{COMMON_CRAWL_INDEX_BASE_URL}/{crawl_index}-index?"
        + urlencode(
            {
                "url": _query_pattern(query),
                "output": "cdxj",
            }
        )
    )


def _query_pattern(query: str) -> str:
    terms = [term for term in query.split() if term]
    if not terms:
        return "*"
    return "*" + "*".join(terms) + "*"


def _parse_cdxj_line(line: str) -> tuple[str, dict[str, Any]] | None:
    parts = line.split(" ", 2)
    if len(parts) != 3:
        return None

    _, timestamp, payload = parts
    try:
        record = json.loads(payload)
    except json.JSONDecodeError:
        return None

    if not isinstance(record, dict):
        return None
    return timestamp, record


def _candidate_from_record(
    record: dict[str, Any],
    *,
    timestamp: str,
    crawl_index: str,
    query: str,
) -> RawCandidate:
    url = _string_value(record.get("url"))
    return RawCandidate(
        title=_title_from_url(url),
        url=url,
        source=SourceKind.COMMON_CRAWL,
        discovery_method=DiscoveryMethod.PUBLIC_INDEX,
        query=query,
        score=0.5,
        metadata={
            "crawl_index": crawl_index,
            "timestamp": timestamp,
            "mime": _string_value(record.get("mime")),
            "status": _string_value(record.get("status")),
            "digest": _string_value(record.get("digest")),
            "length": _string_value(record.get("length")),
            "offset": _string_value(record.get("offset")),
            "filename": _string_value(record.get("filename")),
        },
    )


def _is_pdf_record(url: str, record: dict[str, Any]) -> bool:
    try:
        if is_direct_pdf_url(url):
            return True
    except ValueError:
        return False

    return _string_value(record.get("mime")).lower() == "application/pdf"


def _title_from_url(url: str) -> str:
    parsed = urlsplit(url)
    name = PurePosixPath(parsed.path).name
    return name or url


def _string_value(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)
