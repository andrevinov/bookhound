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
from bookhound.url_normalization import is_direct_pdf_url


class SeedCrawlerHttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        rate_limit_key: str | None = None,
        cache: bool = False,
    ) -> HttpResponse:
        raise NotImplementedError


class RobotsPolicy(Protocol):
    def can_fetch(self, url: str) -> bool:
        raise NotImplementedError


class AllowAllRobotsPolicy:
    def can_fetch(self, url: str) -> bool:
        return True


@dataclass(frozen=True)
class SeedCrawlerConfig:
    seed_urls: list[str]
    allowed_domains: list[str] = field(default_factory=list)
    same_domain_only: bool = True
    max_depth: int = 1
    max_pages_per_seed: int = 50
    request_timeout_seconds: float = 30.0
    user_agent: str = "Bookhound/0.1.0"


class SeedCrawlerAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        http_client: SeedCrawlerHttpClient | None = None,
        robots_policy: RobotsPolicy | None = None,
        config: SeedCrawlerConfig,
    ) -> None:
        super().__init__(
            source=SourceKind.SEED_CRAWLER,
            discovery_method=DiscoveryMethod.CRAWL,
        )
        self.config = config
        self.http_client = http_client or BookhoundHttpClient(
            HttpClientConfig(
                user_agent=self.config.user_agent,
                timeout_seconds=self.config.request_timeout_seconds,
            )
        )
        self.robots_policy = robots_policy or AllowAllRobotsPolicy()
        self.events: list[dict[str, object]] = []

    def search(self, query: str) -> list[RawCandidate]:
        self.events = []
        candidates: list[RawCandidate] = []

        for seed_url in self.config.seed_urls:
            candidates.extend(self._crawl_seed(seed_url=seed_url, query=query))

        return candidates

    def _crawl_seed(self, *, seed_url: str, query: str) -> list[RawCandidate]:
        candidates: list[RawCandidate] = []
        visited_pages: set[str] = set()
        queued_pages = deque([(seed_url, 0)])
        seed_domain = _domain(seed_url)

        while queued_pages and len(visited_pages) < self.config.max_pages_per_seed:
            page_url, depth = queued_pages.popleft()
            if page_url in visited_pages:
                continue
            if not self.robots_policy.can_fetch(page_url):
                self._record_robots_skip(page_url, seed_url)
                continue

            response = self.http_client.get(page_url, rate_limit_key=self.rate_limit_key)
            if not 200 <= response.status_code < 300:
                raise SourceAvailabilityError(
                    SourceKind.SEED_CRAWLER,
                    f"Seed crawler returned HTTP {response.status_code}.",
                )

            visited_pages.add(page_url)
            page_links = _parse_links(response.content.decode("utf-8"))
            for link in page_links:
                absolute_url = urljoin(page_url, link.href)
                if not _domain_allowed(
                    absolute_url,
                    seed_domain=seed_domain,
                    config=self.config,
                ):
                    continue
                if not self.robots_policy.can_fetch(absolute_url):
                    self._record_robots_skip(absolute_url, seed_url)
                    continue
                if not _should_collect_link(
                    absolute_url,
                    page_depth=depth,
                    config=self.config,
                ):
                    continue

                candidates.append(
                    _candidate_from_link(
                        link,
                        url=absolute_url,
                        query=query,
                        source_page_url=page_url,
                    )
                )
                if depth < self.config.max_depth and not _is_pdf_url(absolute_url):
                    queued_pages.append((absolute_url, depth + 1))

        return candidates

    def _record_robots_skip(self, url: str, seed_url: str) -> None:
        self.events.append(
            {
                "event_type": "seed_crawler.robots_disallowed",
                "message": "Skipped URL disallowed by robots policy.",
                "metadata": {
                    "url": url,
                    "seed_url": seed_url,
                },
            }
        )


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
    source_page_url: str,
) -> RawCandidate:
    return RawCandidate(
        title=link.text or _title_from_url(url),
        url=url,
        source=SourceKind.SEED_CRAWLER,
        discovery_method=DiscoveryMethod.CRAWL,
        query=query,
        score=0.6,
        metadata={
            "url_type": "pdf" if _is_pdf_url(url) else "landing_page",
            "source_page_url": source_page_url,
        },
    )


def _domain_allowed(
    url: str,
    *,
    seed_domain: str,
    config: SeedCrawlerConfig,
) -> bool:
    candidate_domain = _domain(url)
    if not config.same_domain_only:
        return True
    return (
        candidate_domain == seed_domain
        or candidate_domain in {domain.lower() for domain in config.allowed_domains}
    )


def _should_collect_link(
    url: str,
    *,
    page_depth: int,
    config: SeedCrawlerConfig,
) -> bool:
    if _is_pdf_url(url):
        return True
    return page_depth == 0 or page_depth < config.max_depth


def _domain(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _is_pdf_url(url: str) -> bool:
    try:
        return is_direct_pdf_url(url)
    except ValueError:
        return False


def _title_from_url(url: str) -> str:
    name = PurePosixPath(urlsplit(url).path).name
    return name or url
