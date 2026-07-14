from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

from bookhound.http_client import BookhoundHttpClient, HttpClientConfig, HttpClientProtocol
from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind
from bookhound.sources import SourceAdapter, SourceAvailabilityError


ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM_NAMESPACE = "{http://www.w3.org/2005/Atom}"
ARXIV_NAMESPACE = "{http://arxiv.org/schemas/atom}"


@dataclass(frozen=True)
class ArxivAdapterConfig:
    max_results: int = 20
    page_size: int = 10
    request_timeout_seconds: float = 30.0
    user_agent: str = "Bookhound/0.1.0"


class ArxivAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        http_client: HttpClientProtocol | None = None,
        config: ArxivAdapterConfig | None = None,
    ) -> None:
        super().__init__(source=SourceKind.ARXIV, discovery_method=DiscoveryMethod.API)
        self.config = config or ArxivAdapterConfig()
        self.http_client = http_client or BookhoundHttpClient(
            HttpClientConfig(
                user_agent=self.config.user_agent,
                timeout_seconds=self.config.request_timeout_seconds,
            )
        )

    def search(self, query: str) -> list[RawCandidate]:
        candidates: list[RawCandidate] = []

        for start in range(0, self.config.max_results, self.config.page_size):
            max_results = min(self.config.page_size, self.config.max_results - start)
            response = self.http_client.get(
                _query_url(query=query, start=start, max_results=max_results),
                rate_limit_key=self.rate_limit_key,
            )
            if not 200 <= response.status_code < 300:
                raise SourceAvailabilityError(
                    SourceKind.ARXIV,
                    f"arXiv API returned HTTP {response.status_code}.",
                )

            candidates.extend(_parse_candidates(response.content, query=query))

        return candidates[: self.config.max_results]


def _query_url(*, query: str, start: int, max_results: int) -> str:
    return (
        f"{ARXIV_API_URL}?"
        + urlencode(
            {
                "search_query": f"all:{query}",
                "start": start,
                "max_results": max_results,
            }
        )
    )


def _parse_candidates(content: bytes, *, query: str) -> list[RawCandidate]:
    root = ET.fromstring(content)
    return [
        _candidate_from_entry(entry, query=query)
        for entry in root.findall(f"{ATOM_NAMESPACE}entry")
    ]


def _candidate_from_entry(entry: ET.Element, *, query: str) -> RawCandidate:
    arxiv_id = _arxiv_id(_text(entry, "id"))
    landing_page_url = f"https://arxiv.org/abs/{arxiv_id}"
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    authors = [
        _normalize_text(name.text or "")
        for name in entry.findall(f"{ATOM_NAMESPACE}author/{ATOM_NAMESPACE}name")
        if _normalize_text(name.text or "")
    ]
    published = _text(entry, "published")
    doi = _text(entry, "doi", namespace=ARXIV_NAMESPACE)

    metadata: dict[str, object] = {
        "arxiv_id": arxiv_id,
        "landing_page_url": landing_page_url,
        "authors": authors,
        "published": published,
    }
    if doi:
        metadata["doi"] = doi

    return RawCandidate(
        title=_text(entry, "title"),
        url=pdf_url,
        source=SourceKind.ARXIV,
        discovery_method=DiscoveryMethod.API,
        query=query,
        snippet=_text(entry, "summary"),
        score=1.0,
        metadata=metadata,
    )


def _text(
    element: ET.Element,
    name: str,
    *,
    namespace: str = ATOM_NAMESPACE,
) -> str:
    child = element.find(f"{namespace}{name}")
    if child is None or child.text is None:
        return ""
    return _normalize_text(child.text)


def _arxiv_id(entry_id: str) -> str:
    return entry_id.rstrip("/").rsplit("/", 1)[-1]


def _normalize_text(value: str) -> str:
    return " ".join(value.split())
