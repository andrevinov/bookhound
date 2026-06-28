from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from bookhound.http_client import BookhoundHttpClient, HttpClientConfig, HttpResponse
from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind
from bookhound.sources import SourceAdapter, SourceAvailabilityError
from bookhound.url_normalization import canonicalize_url, is_direct_pdf_url


class LinkExpansionHttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        rate_limit_key: str | None = None,
        cache: bool = False,
    ) -> HttpResponse:
        raise NotImplementedError


@dataclass(frozen=True)
class LinkExpansionConfig:
    allowed_domains: list[str] = field(default_factory=list)
    same_domain_only: bool = True
    max_depth: int = 1
    max_candidates: int = 100
    request_timeout_seconds: float = 30.0
    user_agent: str = "Bookhound/0.1.0"


class LinkExpansionAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        http_client: LinkExpansionHttpClient | None = None,
        config: LinkExpansionConfig | None = None,
    ) -> None:
        super().__init__(
            source=SourceKind.LINK_EXPANSION,
            discovery_method=DiscoveryMethod.LINK_EXPANSION,
        )
        self.config = config or LinkExpansionConfig()
        self.http_client = http_client or BookhoundHttpClient(
            HttpClientConfig(
                user_agent=self.config.user_agent,
                timeout_seconds=self.config.request_timeout_seconds,
            )
        )

    def search(self, query: str) -> list[RawCandidate]:
        return []

    def expand(
        self,
        existing_candidates: list[RawCandidate],
        *,
        query: str,
    ) -> list[RawCandidate]:
        expanded_candidates: list[RawCandidate] = []
        seen_urls = {_canonical_or_raw(candidate.url) for candidate in existing_candidates}
        queued_pages = deque(
            (candidate.url, 0, candidate.url)
            for candidate in existing_candidates
            if not _is_pdf_url(candidate.url)
        )
        visited_pages: set[str] = set()

        while queued_pages and len(expanded_candidates) < self.config.max_candidates:
            page_url, depth, source_candidate_url = queued_pages.popleft()
            canonical_page_url = _canonical_or_raw(page_url)
            if canonical_page_url in visited_pages:
                continue
            if depth >= self.config.max_depth + 1:
                continue

            response = self.http_client.get(page_url, rate_limit_key=self.rate_limit_key)
            if not 200 <= response.status_code < 300:
                raise SourceAvailabilityError(
                    SourceKind.LINK_EXPANSION,
                    f"Link expansion returned HTTP {response.status_code}.",
                )
            visited_pages.add(canonical_page_url)

            for link in _parse_links(response.content.decode("utf-8")):
                absolute_url = urljoin(page_url, link.href)
                canonical_url = _canonical_or_raw(absolute_url)
                if canonical_url in seen_urls:
                    continue
                if not _domain_allowed(absolute_url, page_url, self.config):
                    continue

                candidate = _candidate_from_link(
                    link,
                    url=absolute_url,
                    query=query,
                    source_candidate_url=source_candidate_url,
                    source_page_url=page_url,
                    depth=depth + 1,
                )
                expanded_candidates.append(candidate)
                seen_urls.add(canonical_url)

                if len(expanded_candidates) >= self.config.max_candidates:
                    break
                if depth + 1 < self.config.max_depth and not _is_pdf_url(absolute_url):
                    queued_pages.append((absolute_url, depth + 1, source_candidate_url))

        return expanded_candidates


@dataclass(frozen=True)
class _HtmlLink:
    href: str
    text: str


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[_HtmlLink] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() != "a":
            return
        attributes = {
            name.lower(): value.strip()
            for name, value in attrs
            if value is not None
        }
        href = attributes.get("href")
        if href:
            self._current_href = href
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current_href is None:
            return
        self.links.append(
            _HtmlLink(
                href=self._current_href,
                text=" ".join("".join(self._current_text).split()),
            )
        )
        self._current_href = None
        self._current_text = []


def _parse_links(html: str) -> list[_HtmlLink]:
    parser = _LinkParser()
    parser.feed(html)
    return parser.links


def _candidate_from_link(
    link: _HtmlLink,
    *,
    url: str,
    query: str,
    source_candidate_url: str,
    source_page_url: str,
    depth: int,
) -> RawCandidate:
    return RawCandidate(
        title=link.text or _title_from_url(url),
        url=url,
        source=SourceKind.LINK_EXPANSION,
        discovery_method=DiscoveryMethod.LINK_EXPANSION,
        query=query,
        score=_score_link(link, url),
        metadata={
            "source_candidate_url": source_candidate_url,
            "source_page_url": source_page_url,
            "anchor_text": link.text,
            "depth": depth,
            "url_type": "pdf" if _is_pdf_url(url) else "landing_page",
        },
    )


def _score_link(link: _HtmlLink, url: str) -> float:
    score = 0.5
    if _is_pdf_url(url):
        score += 0.3
    if "pdf" in link.text.lower() or "report" in link.text.lower():
        score += 0.1
    return min(score, 1.0)


def _domain_allowed(
    candidate_url: str,
    source_page_url: str,
    config: LinkExpansionConfig,
) -> bool:
    if not config.same_domain_only:
        return True

    candidate_domain = _domain(candidate_url)
    source_domain = _domain(source_page_url)
    allowed_domains = {domain.lower() for domain in config.allowed_domains}
    return candidate_domain == source_domain or candidate_domain in allowed_domains


def _domain(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _is_pdf_url(url: str) -> bool:
    try:
        return is_direct_pdf_url(url)
    except ValueError:
        return False


def _canonical_or_raw(url: str) -> str:
    try:
        return canonicalize_url(url)
    except ValueError:
        return url


def _title_from_url(url: str) -> str:
    name = PurePosixPath(urlsplit(url).path).name
    return name or url
