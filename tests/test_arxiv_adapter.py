from urllib.parse import parse_qs, urlsplit

import pytest

from bookhound.arxiv import ArxivAdapter, ArxivAdapterConfig
from bookhound.http_client import HttpResponse
from bookhound.models import DiscoveryMethod, SourceKind
from bookhound.sources import SourceAvailabilityError


ARXIV_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.01234v2</id>
    <updated>2026-06-15T12:00:00Z</updated>
    <published>2026-06-10T08:30:00Z</published>
    <title>
      Open Access Search for Public Policy PDFs
    </title>
    <summary>
      We describe a reproducible workflow for discovering public documents.
    </summary>
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
    <arxiv:doi>10.48550/arXiv.2401.01234</arxiv:doi>
  </entry>
</feed>
"""


class FakeHttpClient:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = responses
        self.urls: list[str] = []
        self.rate_limit_keys: list[str | None] = []

    def get(
        self,
        url: str,
        *,
        rate_limit_key: str | None = None,
        cache: bool = False,
    ) -> HttpResponse:
        self.urls.append(url)
        self.rate_limit_keys.append(rate_limit_key)
        return self.responses.pop(0)


@pytest.mark.revised
def test_arxiv_atom_fixture_becomes_candidates() -> None:
    adapter = ArxivAdapter(
        http_client=FakeHttpClient([_response(ARXIV_FIXTURE)]),
        config=ArxivAdapterConfig(max_results=1, page_size=1),
    )

    candidates = adapter.search("public policy")

    assert adapter.source_name is SourceKind.ARXIV
    assert adapter.discovery_method is DiscoveryMethod.API
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.title == "Open Access Search for Public Policy PDFs"
    assert candidate.url == "https://arxiv.org/pdf/2401.01234v2.pdf"
    assert candidate.source is SourceKind.ARXIV
    assert candidate.discovery_method is DiscoveryMethod.API
    assert candidate.query == "public policy"
    assert candidate.snippet == (
        "We describe a reproducible workflow for discovering public documents."
    )
    assert candidate.metadata["arxiv_id"] == "2401.01234v2"
    assert candidate.metadata["doi"] == "10.48550/arXiv.2401.01234"
    assert candidate.metadata["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert candidate.metadata["published"] == "2026-06-10T08:30:00Z"


@pytest.mark.revised
def test_pdf_url_is_derived_from_arxiv_abs_url() -> None:
    adapter = ArxivAdapter(
        http_client=FakeHttpClient([_response(ARXIV_FIXTURE)]),
        config=ArxivAdapterConfig(max_results=1, page_size=1),
    )

    candidate = adapter.search("search systems")[0]

    assert candidate.url == "https://arxiv.org/pdf/2401.01234v2.pdf"
    assert candidate.metadata["landing_page_url"] == (
        "https://arxiv.org/abs/2401.01234v2"
    )


@pytest.mark.revised
def test_pagination_uses_start_and_max_results_query_parameters() -> None:
    http_client = FakeHttpClient(
        [
            _response(ARXIV_FIXTURE),
            _response(ARXIV_FIXTURE.replace(b"2401.01234v2", b"2401.05678v1")),
        ]
    )
    adapter = ArxivAdapter(
        http_client=http_client,
        config=ArxivAdapterConfig(max_results=2, page_size=1),
    )

    candidates = adapter.search("machine learning")

    first_query = parse_qs(urlsplit(http_client.urls[0]).query)
    second_query = parse_qs(urlsplit(http_client.urls[1]).query)
    assert len(candidates) == 2
    assert first_query["search_query"] == ["all:machine learning"]
    assert first_query["start"] == ["0"]
    assert first_query["max_results"] == ["1"]
    assert second_query["search_query"] == ["all:machine learning"]
    assert second_query["start"] == ["1"]
    assert second_query["max_results"] == ["1"]
    assert http_client.rate_limit_keys == ["source:arxiv", "source:arxiv"]


@pytest.mark.revised
def test_http_error_becomes_typed_source_error() -> None:
    adapter = ArxivAdapter(
        http_client=FakeHttpClient(
            [_response(b"Service unavailable", status_code=503)]
        ),
        config=ArxivAdapterConfig(max_results=1, page_size=1),
    )

    with pytest.raises(SourceAvailabilityError) as error:
        adapter.search("quantum computing")

    assert error.value.source is SourceKind.ARXIV
    assert "503" in error.value.message


def _response(content: bytes, *, status_code: int = 200) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        headers={"content-type": "application/atom+xml"},
        content=content,
        url="https://export.arxiv.org/api/query",
    )
