from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Protocol
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

from bookhound.http_client import BookhoundHttpClient, HttpClientConfig, HttpResponse
from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind
from bookhound.sources import SourceAdapter, SourceAvailabilityError
from bookhound.url_normalization import is_direct_pdf_url


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


class SitemapHttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        rate_limit_key: str | None = None,
        cache: bool = False,
    ) -> HttpResponse:
        raise NotImplementedError


@dataclass(frozen=True)
class SitemapAdapterConfig:
    domain_roots: list[str]
    request_timeout_seconds: float = 30.0
    user_agent: str = "Bookhound/0.1.0"


class SitemapAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        http_client: SitemapHttpClient | None = None,
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

        for domain_root in self.config.domain_roots:
            sitemap_queue = deque(self._sitemaps_from_root(domain_root))
            while sitemap_queue:
                sitemap_url = sitemap_queue.popleft()
                child_sitemaps, sitemap_candidates = self._read_sitemap(
                    sitemap_url,
                    query=query,
                    seen_sitemaps=seen_sitemaps,
                )
                sitemap_queue.extend(child_sitemaps)
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
            )

        return [], []

    def _candidates_from_urlset(
        self,
        root: ET.Element,
        *,
        sitemap_url: str,
        query: str,
    ) -> list[RawCandidate]:
        candidates: list[RawCandidate] = []
        for entry in root.findall(f"{SITEMAP_NAMESPACE}url"):
            url = _child_text(entry, "loc")
            if not url:
                self._record_malformed_entry(sitemap_url)
                continue
            if not _is_candidate_url(url):
                continue

            url_type = "pdf" if _is_pdf_url(url) else "landing_page"
            candidates.append(
                RawCandidate(
                    title=_title_from_url(url),
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
    return _is_pdf_url(url) or _looks_like_document_landing_page(url)


def _is_pdf_url(url: str) -> bool:
    try:
        return is_direct_pdf_url(url)
    except ValueError:
        return False


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


def _title_from_url(url: str) -> str:
    name = PurePosixPath(urlsplit(url).path).name
    return name or url
