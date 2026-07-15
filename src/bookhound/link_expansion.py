from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import logging
from urllib.parse import urljoin, urlsplit, urlunsplit

from bookhound.html_links import HtmlLink, parse_links
from bookhound.http_client import BookhoundHttpClient, HttpClientConfig, HttpClientProtocol
from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind
from bookhound.sources import SourceAdapter
from bookhound.text_decoding import decode_http_text
from bookhound.url_normalization import (
    canonicalize_url_or_raw,
    safe_is_direct_pdf_url,
    title_from_url,
    url_domain,
)


logger = logging.getLogger(__name__)


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
        http_client: HttpClientProtocol | None = None,
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
        seen_urls = {
            canonicalize_url_or_raw(candidate.url)
            for candidate in existing_candidates
        }
        queued_pages = deque(
            (candidate.url, 0, candidate.url)
            for candidate in existing_candidates
            if not safe_is_direct_pdf_url(candidate.url)
        )
        visited_pages: set[str] = set()

        while queued_pages and len(expanded_candidates) < self.config.max_candidates:
            page_url, depth, source_candidate_url = queued_pages.popleft()
            canonical_page_url = canonicalize_url_or_raw(page_url)
            if canonical_page_url in visited_pages:
                continue
            if depth >= self.config.max_depth + 1:
                continue

            response = self.http_client.get(page_url, rate_limit_key=self.rate_limit_key)
            if not 200 <= response.status_code < 300:
                visited_pages.add(canonical_page_url)
                logger.warning(
                    "Link expansion page skipped.",
                    extra={
                        "event": "link_expansion.page_skipped",
                        "url": _sanitize_url(page_url),
                        "status_code": response.status_code,
                        "source_candidate_url": _sanitize_url(source_candidate_url),
                        "depth": depth,
                    },
                )
                continue
            visited_pages.add(canonical_page_url)

            for link in parse_links(decode_http_text(response.content, response.headers)):
                absolute_url = urljoin(page_url, link.href)
                canonical_url = canonicalize_url_or_raw(absolute_url)
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
                if (
                    depth + 1 < self.config.max_depth
                    and not safe_is_direct_pdf_url(absolute_url)
                ):
                    queued_pages.append((absolute_url, depth + 1, source_candidate_url))

        return expanded_candidates


def _candidate_from_link(
    link: HtmlLink,
    *,
    url: str,
    query: str,
    source_candidate_url: str,
    source_page_url: str,
    depth: int,
) -> RawCandidate:
    return RawCandidate(
        title=link.text or title_from_url(url),
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
            "url_type": "pdf" if safe_is_direct_pdf_url(url) else "landing_page",
        },
    )


def _score_link(link: HtmlLink, url: str) -> float:
    score = 0.5
    if safe_is_direct_pdf_url(url):
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

    candidate_domain = url_domain(candidate_url)
    source_domain = url_domain(source_page_url)
    allowed_domains = {domain.lower() for domain in config.allowed_domains}
    return candidate_domain == source_domain or candidate_domain in allowed_domains


def _sanitize_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
