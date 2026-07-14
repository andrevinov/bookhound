from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import ipaddress
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

from bookhound.http_client import (
    BookhoundHttpClient,
    HttpClientConfig,
    HttpClientProtocol,
    HttpResponse,
)
from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind
from bookhound.sources import SourceAdapter, SourceAvailabilityError
from bookhound.url_normalization import safe_is_direct_pdf_url, title_from_url


SITEMAP_NAMESPACE = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
DOCUMENT_URL_HINTS = {
    "article",
    "book",
    "document",
    "field-guide",
    "guide",
    "paper",
    "publication",
    "report",
    "reports",
}


@dataclass(frozen=True)
class SitemapAdapterConfig:
    domain_roots: list[str]
    allowed_sitemap_domains: list[str] = field(default_factory=list)
    max_sitemap_files: int = 100
    max_url_entries: int = 50_000
    request_timeout_seconds: float = 30.0
    user_agent: str = "Bookhound/0.1.0"


@dataclass
class SitemapTraversalState:
    fetched_sitemap_files: int = 0
    inspected_url_entries: int = 0


class SitemapAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        http_client: HttpClientProtocol | None = None,
        config: SitemapAdapterConfig,
    ) -> None:
        super().__init__(source=SourceKind.SITEMAP, discovery_method=DiscoveryMethod.SITEMAP)
        self.config = config
        self.http_client = http_client or BookhoundHttpClient(
            HttpClientConfig(
                user_agent=self.config.user_agent,
                timeout_seconds=self.config.request_timeout_seconds,
            )
        )
        self.events: list[dict[str, object]] = []

    def search(self, query: str) -> list[RawCandidate]:
        self.events = []
        candidates: list[RawCandidate] = []
        seen_sitemaps: set[str] = set()
        state = SitemapTraversalState()
        allowed_hosts = _allowed_sitemap_hosts(self.config)

        for domain_root in self.config.domain_roots:
            sitemap_queue = deque(
                self._allowed_sitemap_urls(
                    self._sitemaps_from_root(domain_root),
                    allowed_hosts=allowed_hosts,
                )
            )
            while sitemap_queue:
                sitemap_url = sitemap_queue.popleft()
                if not self._sitemap_url_allowed(sitemap_url, allowed_hosts=allowed_hosts):
                    self._record_frontier_rejection(sitemap_url)
                    continue
                if state.fetched_sitemap_files >= self.config.max_sitemap_files:
                    self._record_sitemap_limit(sitemap_url)
                    continue

                state.fetched_sitemap_files += 1
                child_sitemaps, sitemap_candidates = self._read_sitemap(
                    sitemap_url,
                    query=query,
                    seen_sitemaps=seen_sitemaps,
                    state=state,
                )
                sitemap_queue.extend(
                    self._allowed_sitemap_urls(
                        child_sitemaps,
                        allowed_hosts=allowed_hosts,
                    )
                )
                candidates.extend(sitemap_candidates)

        return candidates

    def _sitemaps_from_root(self, domain_root: str) -> list[str]:
        robots_url = urljoin(domain_root, "/robots.txt")
        response = self._get(robots_url)
        return [
            line.split(":", 1)[1].strip()
            for line in response.content.decode("utf-8").splitlines()
            if line.strip().lower().startswith("sitemap:")
            and line.split(":", 1)[1].strip()
        ]

    def _read_sitemap(
        self,
        sitemap_url: str,
        *,
        query: str,
        seen_sitemaps: set[str],
        state: SitemapTraversalState,
    ) -> tuple[list[str], list[RawCandidate]]:
        if sitemap_url in seen_sitemaps:
            return [], []
        seen_sitemaps.add(sitemap_url)

        response = self._get(sitemap_url)
        root = ET.fromstring(response.content)
        if root.tag == f"{SITEMAP_NAMESPACE}sitemapindex":
            return _sitemap_index_urls(root), []

        if root.tag == f"{SITEMAP_NAMESPACE}urlset":
            return [], self._candidates_from_urlset(
                root,
                sitemap_url=sitemap_url,
                query=query,
                state=state,
            )

        return [], []

    def _candidates_from_urlset(
        self,
        root: ET.Element,
        *,
        sitemap_url: str,
        query: str,
        state: SitemapTraversalState,
    ) -> list[RawCandidate]:
        candidates: list[RawCandidate] = []
        for entry in root.findall(f"{SITEMAP_NAMESPACE}url"):
            url = _child_text(entry, "loc")
            if state.inspected_url_entries >= self.config.max_url_entries:
                self._record_url_entry_limit(url or sitemap_url, sitemap_url)
                continue

            state.inspected_url_entries += 1
            if not url:
                self._record_malformed_entry(sitemap_url)
                continue
            if not _is_candidate_url(url):
                continue

            url_type = "pdf" if safe_is_direct_pdf_url(url) else "landing_page"
            candidates.append(
                RawCandidate(
                    title=title_from_url(url),
                    url=url,
                    source=SourceKind.SITEMAP,
                    discovery_method=DiscoveryMethod.SITEMAP,
                    query=query,
                    score=0.7,
                    metadata={
                        "sitemap_url": sitemap_url,
                        "lastmod": _child_text(entry, "lastmod"),
                        "url_type": url_type,
                    },
                )
            )
        return candidates

    def _get(self, url: str) -> HttpResponse:
        response = self.http_client.get(url, rate_limit_key=self.rate_limit_key)
        if not 200 <= response.status_code < 300:
            raise SourceAvailabilityError(
                SourceKind.SITEMAP,
                f"Sitemap request returned HTTP {response.status_code}.",
            )
        return response

    def _record_malformed_entry(self, sitemap_url: str) -> None:
        self.events.append(
            {
                "event_type": "sitemap.malformed_entry",
                "message": "Ignored sitemap entry without a URL.",
                "metadata": {
                    "sitemap_url": sitemap_url,
                },
            }
        )

    def _allowed_sitemap_urls(
        self,
        urls: list[str],
        *,
        allowed_hosts: set[str],
    ) -> list[str]:
        allowed_urls: list[str] = []
        for url in urls:
            if self._sitemap_url_allowed(url, allowed_hosts=allowed_hosts):
                allowed_urls.append(url)
            else:
                self._record_frontier_rejection(url)
        return allowed_urls

    def _sitemap_url_allowed(self, url: str, *, allowed_hosts: set[str]) -> bool:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            return False

        host = _normalized_hostname(parsed.hostname)
        if not host or _is_unsafe_sitemap_host(host):
            return False

        return host in allowed_hosts

    def _record_frontier_rejection(self, url: str) -> None:
        self.events.append(
            {
                "event_type": "sitemap.frontier_rejected",
                "message": "Rejected sitemap URL outside the configured frontier.",
                "metadata": {
                    "url": url,
                },
            }
        )

    def _record_sitemap_limit(self, url: str) -> None:
        self.events.append(
            {
                "event_type": "sitemap.traversal_limit_reached",
                "message": "Skipped sitemap URL because the sitemap file limit was reached.",
                "metadata": {
                    "url": url,
                    "limit": self.config.max_sitemap_files,
                },
            }
        )

    def _record_url_entry_limit(self, url: str, sitemap_url: str) -> None:
        self.events.append(
            {
                "event_type": "sitemap.url_entry_limit_reached",
                "message": "Skipped sitemap URL entry because the URL entry limit was reached.",
                "metadata": {
                    "url": url,
                    "sitemap_url": sitemap_url,
                    "limit": self.config.max_url_entries,
                },
            }
        )


def _sitemap_index_urls(root: ET.Element) -> list[str]:
    urls: list[str] = []
    for sitemap in root.findall(f"{SITEMAP_NAMESPACE}sitemap"):
        url = _child_text(sitemap, "loc")
        if url:
            urls.append(url)
    return urls


def _child_text(element: ET.Element, name: str) -> str:
    child = element.find(f"{SITEMAP_NAMESPACE}{name}")
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _is_candidate_url(url: str) -> bool:
    return safe_is_direct_pdf_url(url) or _looks_like_document_landing_page(url)


def _looks_like_document_landing_page(url: str) -> bool:
    parsed = urlsplit(url)
    path_parts = {
        part.lower()
        for part in PurePosixPath(parsed.path).parts
        if part not in {"/", ""}
    }
    path_slug = PurePosixPath(parsed.path).name.lower()
    slug_terms = set(path_slug.replace("-", " ").replace("_", " ").split())
    return bool((path_parts | slug_terms) & DOCUMENT_URL_HINTS)


def _allowed_sitemap_hosts(config: SitemapAdapterConfig) -> set[str]:
    hosts: set[str] = set()
    for domain_root in config.domain_roots:
        parsed = urlsplit(domain_root)
        if parsed.scheme not in {"http", "https"}:
            continue
        host = _normalized_hostname(parsed.hostname)
        if host and not _is_unsafe_sitemap_host(host):
            hosts.add(host)

    for domain in config.allowed_sitemap_domains:
        host = _hostname_from_domain_setting(domain)
        if host and not _is_unsafe_sitemap_host(host):
            hosts.add(host)

    return hosts


def _hostname_from_domain_setting(value: str) -> str:
    stripped_value = value.strip()
    if not stripped_value:
        return ""
    if "://" in stripped_value:
        return _normalized_hostname(urlsplit(stripped_value).hostname)
    return _normalized_hostname(urlsplit(f"//{stripped_value}").hostname)


def _normalized_hostname(hostname: str | None) -> str:
    return (hostname or "").rstrip(".").lower()


def _is_unsafe_sitemap_host(hostname: str) -> bool:
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True

    try:
        ip_address = ipaddress.ip_address(hostname)
    except ValueError:
        return False

    return (
        ip_address.is_loopback
        or ip_address.is_private
        or ip_address.is_link_local
        or ip_address.is_unspecified
        or ip_address.is_reserved
    )
