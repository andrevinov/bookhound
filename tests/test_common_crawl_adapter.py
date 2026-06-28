from urllib.parse import parse_qs, urlsplit

import pytest

from bookhound.common_crawl import CommonCrawlAdapter, CommonCrawlAdapterConfig
from bookhound.http_client import HttpResponse
from bookhound.models import DiscoveryMethod, SourceKind


CDXJ_FIXTURE = b"""org,example)/reports/climate-policy.pdf 20260601010101 {"url":"https://example.org/reports/climate-policy.pdf","mime":"application/pdf","status":"200","digest":"ABC123","length":"2048","offset":"512","filename":"crawl-data/CC-MAIN-2026-10/segments/file.warc.gz"}
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
def test_cdxj_fixture_becomes_candidates() -> None:
    adapter = CommonCrawlAdapter(
        http_client=FakeHttpClient([_response(CDXJ_FIXTURE)]),
        config=CommonCrawlAdapterConfig(
            crawl_indexes=["CC-MAIN-2026-10"],
            result_limit=1,
        ),
    )

    candidates = adapter.search("climate policy")

    assert adapter.source_name is SourceKind.COMMON_CRAWL
    assert adapter.discovery_method is DiscoveryMethod.PUBLIC_INDEX
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.title == "climate-policy.pdf"
    assert candidate.url == "https://example.org/reports/climate-policy.pdf"
    assert candidate.source is SourceKind.COMMON_CRAWL
    assert candidate.discovery_method is DiscoveryMethod.PUBLIC_INDEX
    assert candidate.query == "climate policy"
    assert candidate.metadata == {
        "crawl_index": "CC-MAIN-2026-10",
        "timestamp": "20260601010101",
        "mime": "application/pdf",
        "status": "200",
        "digest": "ABC123",
        "length": "2048",
        "offset": "512",
        "filename": "crawl-data/CC-MAIN-2026-10/segments/file.warc.gz",
    }


@pytest.mark.revised
def test_non_pdf_entries_are_filtered_when_configured() -> None:
    fixture = b"""org,example)/reports/page.html 20260601010101 {"url":"https://example.org/reports/page.html","mime":"text/html","status":"200"}
org,example)/reports/appendix.pdf 20260601010102 {"url":"https://example.org/reports/appendix.pdf","mime":"application/octet-stream","status":"200"}
"""
    adapter = CommonCrawlAdapter(
        http_client=FakeHttpClient([_response(fixture)]),
        config=CommonCrawlAdapterConfig(
            crawl_indexes=["CC-MAIN-2026-10"],
            filter_non_pdf=True,
            result_limit=10,
        ),
    )

    candidates = adapter.search("reports")

    assert [candidate.url for candidate in candidates] == [
        "https://example.org/reports/appendix.pdf"
    ]


@pytest.mark.revised
def test_crawls_are_queried_in_configured_order() -> None:
    http_client = FakeHttpClient(
        [
            _response(b""),
            _response(CDXJ_FIXTURE),
        ]
    )
    adapter = CommonCrawlAdapter(
        http_client=http_client,
        config=CommonCrawlAdapterConfig(
            crawl_indexes=["CC-MAIN-2026-05", "CC-MAIN-2026-10"],
            result_limit=1,
        ),
    )

    adapter.search("climate policy")

    first_url = urlsplit(http_client.urls[0])
    second_url = urlsplit(http_client.urls[1])
    first_query = parse_qs(first_url.query)
    second_query = parse_qs(second_url.query)
    assert first_url.path == "/CC-MAIN-2026-05-index"
    assert second_url.path == "/CC-MAIN-2026-10-index"
    assert first_query["url"] == ["*climate*policy*"]
    assert second_query["url"] == ["*climate*policy*"]
    assert first_query["output"] == ["cdxj"]
    assert second_query["output"] == ["cdxj"]
    assert http_client.rate_limit_keys == [
        "source:common_crawl",
        "source:common_crawl",
    ]


@pytest.mark.revised
def test_malformed_line_is_ignored_with_error_event() -> None:
    fixture = b"""this is not valid cdxj
org,example)/reports/climate-policy.pdf 20260601010101 {"url":"https://example.org/reports/climate-policy.pdf","mime":"application/pdf","status":"200"}
"""
    adapter = CommonCrawlAdapter(
        http_client=FakeHttpClient([_response(fixture)]),
        config=CommonCrawlAdapterConfig(
            crawl_indexes=["CC-MAIN-2026-10"],
            result_limit=10,
        ),
    )

    candidates = adapter.search("climate policy")

    assert len(candidates) == 1
    assert adapter.events == [
        {
            "event_type": "common_crawl.malformed_line",
            "message": "Ignored malformed CDXJ line.",
            "metadata": {
                "crawl_index": "CC-MAIN-2026-10",
                "line_number": 1,
            },
        }
    ]


def _response(content: bytes, *, status_code: int = 200) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        headers={"content-type": "text/plain"},
        content=content,
        url="https://index.commoncrawl.org/CC-MAIN-2026-10-index",
    )
