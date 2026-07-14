# Consolidated test module. See docs/audit-tests/03-centralize-test-files.md.
# Consolidated from test_arxiv_adapter.py

from urllib.parse import parse_qs, urlsplit

import pytest

from bookhound.arxiv import ArxivAdapter, ArxivAdapterConfig
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


@pytest.mark.revised
def test_arxiv_atom_fixture_becomes_candidates(
    recording_http_client_factory,
    http_response_factory,
) -> None:
    adapter = ArxivAdapter(
        http_client=recording_http_client_factory.from_queue(
            [
                http_response_factory(
                    url="https://export.arxiv.org/api/query",
                    content=ARXIV_FIXTURE,
                    content_type="application/atom+xml",
                )
            ]
        ),
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
def test_pdf_url_is_derived_from_arxiv_abs_url(
    recording_http_client_factory,
    http_response_factory,
) -> None:
    adapter = ArxivAdapter(
        http_client=recording_http_client_factory.from_queue(
            [
                http_response_factory(
                    url="https://export.arxiv.org/api/query",
                    content=ARXIV_FIXTURE,
                    content_type="application/atom+xml",
                )
            ]
        ),
        config=ArxivAdapterConfig(max_results=1, page_size=1),
    )

    candidate = adapter.search("search systems")[0]

    assert candidate.url == "https://arxiv.org/pdf/2401.01234v2.pdf"
    assert candidate.metadata["landing_page_url"] == (
        "https://arxiv.org/abs/2401.01234v2"
    )


@pytest.mark.revised
def test_pagination_uses_start_and_max_results_query_parameters(
    recording_http_client_factory,
    http_response_factory,
) -> None:
    http_client = recording_http_client_factory.from_queue(
        [
            http_response_factory(
                url="https://export.arxiv.org/api/query",
                content=ARXIV_FIXTURE,
                content_type="application/atom+xml",
            ),
            http_response_factory(
                url="https://export.arxiv.org/api/query",
                content=ARXIV_FIXTURE.replace(b"2401.01234v2", b"2401.05678v1"),
                content_type="application/atom+xml",
            ),
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
def test_http_error_becomes_typed_source_error(
    recording_http_client_factory,
    http_response_factory,
) -> None:
    adapter = ArxivAdapter(
        http_client=recording_http_client_factory.from_queue(
            [
                http_response_factory(
                    url="https://export.arxiv.org/api/query",
                    content=b"Service unavailable",
                    content_type="application/atom+xml",
                    status_code=503,
                )
            ]
        ),
        config=ArxivAdapterConfig(max_results=1, page_size=1),
    )

    with pytest.raises(SourceAvailabilityError) as error:
        adapter.search("quantum computing")

    assert error.value.source is SourceKind.ARXIV
    assert "503" in error.value.message


# Consolidated from test_google_adapter.py

from urllib.parse import parse_qs, urlsplit

import pytest

from bookhound.google_search import GoogleSearchAdapter, GoogleSearchAdapterConfig
from bookhound.models import DiscoveryMethod, SourceKind
from bookhound.query_planner import QueryPlanner, QueryPlannerConfig
from bookhound.sources import run_source_search


GOOGLE_FIXTURE = {
    "items": [
        {
            "title": "Open Climate Policy PDF",
            "link": "https://example.org/reports/climate-policy.pdf",
            "snippet": "A public report about climate policy and planning.",
            "displayLink": "example.org",
            "mime": "application/pdf",
            "fileFormat": "PDF/Adobe Acrobat",
        }
    ]
}


@pytest.mark.revised
def test_google_json_fixture_becomes_candidates(
    recording_http_client_factory,
    json_response_factory,
) -> None:
    adapter = GoogleSearchAdapter(
        http_client=recording_http_client_factory.from_queue(
            [
                json_response_factory(
                    url="https://www.googleapis.com/customsearch/v1",
                    payload=GOOGLE_FIXTURE,
                )
            ]
        ),
        config=GoogleSearchAdapterConfig(
            api_key="test-api-key",
            search_engine_id="test-search-engine",
            result_limit=1,
        ),
    )

    candidates = adapter.search('"climate policy" filetype:pdf')

    assert adapter.source_name is SourceKind.GOOGLE
    assert adapter.discovery_method is DiscoveryMethod.API
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.title == "Open Climate Policy PDF"
    assert candidate.url == "https://example.org/reports/climate-policy.pdf"
    assert candidate.source is SourceKind.GOOGLE
    assert candidate.discovery_method is DiscoveryMethod.API
    assert candidate.query == '"climate policy" filetype:pdf'
    assert candidate.snippet == "A public report about climate policy and planning."
    assert candidate.metadata == {
        "display_link": "example.org",
        "mime": "application/pdf",
        "file_format": "PDF/Adobe Acrobat",
    }


@pytest.mark.revised
def test_missing_credential_marks_adapter_as_disabled() -> None:
    adapter = GoogleSearchAdapter(
        config=GoogleSearchAdapterConfig(
            api_key=None,
            search_engine_id="test-search-engine",
        ),
    )

    result = run_source_search(adapter, query='"climate policy"')

    assert adapter.enabled is False
    assert result.source is SourceKind.GOOGLE
    assert result.candidates == []
    assert result.errors == [
        "Source google is disabled: Missing Google API key or search engine ID."
    ]


@pytest.mark.revised
def test_sent_query_preserves_the_planned_variant(
    recording_http_client_factory,
    json_response_factory,
) -> None:
    http_client = recording_http_client_factory.from_queue(
        [
            json_response_factory(
                url="https://www.googleapis.com/customsearch/v1",
                payload=GOOGLE_FIXTURE,
            )
        ]
    )
    adapter = GoogleSearchAdapter(
        http_client=http_client,
        config=GoogleSearchAdapterConfig(
            api_key="test-api-key",
            search_engine_id="test-search-engine",
            result_limit=1,
        ),
    )
    planned_variant = QueryPlanner(
        QueryPlannerConfig(max_variants=2)
    ).plan_queries("climate policy").variants[1].query

    adapter.search(planned_variant)

    parsed_url = urlsplit(http_client.urls[0])
    query = parse_qs(parsed_url.query)
    assert parsed_url.scheme == "https"
    assert parsed_url.netloc == "www.googleapis.com"
    assert parsed_url.path == "/customsearch/v1"
    assert query["q"] == [planned_variant]
    assert query["key"] == ["test-api-key"]
    assert query["cx"] == ["test-search-engine"]
    assert query["num"] == ["1"]
    assert http_client.rate_limit_keys == ["source:google"]


@pytest.mark.revised
def test_quota_error_becomes_typed_error_and_does_not_take_down_pipeline(
    recording_http_client_factory,
    json_response_factory,
) -> None:
    adapter = GoogleSearchAdapter(
        http_client=recording_http_client_factory.from_queue(
            [
                json_response_factory(
                    url="https://www.googleapis.com/customsearch/v1",
                    payload={
                        "error": {
                            "code": 429,
                            "message": "Quota exceeded for quota metric.",
                        }
                    },
                    status_code=429,
                )
            ]
        ),
        config=GoogleSearchAdapterConfig(
            api_key="test-api-key",
            search_engine_id="test-search-engine",
            result_limit=1,
        ),
    )

    result = run_source_search(adapter, query='"climate policy" filetype:pdf')

    assert result.source is SourceKind.GOOGLE
    assert result.candidates == []
    assert result.errors == ["quota: Google API quota exceeded."]


# Consolidated from test_unpaywall_adapter.py

from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from bookhound.models import DiscoveryMethod, LicenseStatus, SourceKind
from bookhound.unpaywall import UnpaywallAdapter, UnpaywallAdapterConfig


UNPAYWALL_FIXTURE = {
    "doi": "10.1234/bookhound.2026",
    "title": "Open Access Field Guide",
    "year": 2026,
    "is_oa": True,
    "oa_status": "gold",
    "best_oa_location": {
        "url": "https://repository.example.org/articles/bookhound",
        "url_for_pdf": "https://repository.example.org/articles/bookhound.pdf",
        "url_for_landing_page": "https://repository.example.org/articles/bookhound",
        "host_type": "repository",
        "license": "cc-by",
    },
}


@pytest.mark.revised
def test_best_oa_location_fixture_produces_candidate_and_license_evidence(
    recording_http_client_factory,
    json_response_factory,
) -> None:
    adapter = UnpaywallAdapter(
        http_client=recording_http_client_factory.from_queue(
            [
                json_response_factory(
                    url="https://api.unpaywall.org/v2/10.1234/bookhound.2026",
                    payload=UNPAYWALL_FIXTURE,
                )
            ]
        ),
        config=UnpaywallAdapterConfig(email="researcher@example.org"),
    )

    result = adapter.enrich_doi("10.1234/bookhound.2026")

    assert result.candidate is not None
    assert result.candidate.title == "Open Access Field Guide"
    assert result.candidate.url == "https://repository.example.org/articles/bookhound.pdf"
    assert result.candidate.source is SourceKind.UNPAYWALL
    assert result.candidate.discovery_method is DiscoveryMethod.ENRICHMENT
    assert result.candidate.query == "10.1234/bookhound.2026"
    assert result.candidate.metadata["doi"] == "10.1234/bookhound.2026"
    assert result.candidate.metadata["landing_page_url"] == (
        "https://repository.example.org/articles/bookhound"
    )
    assert result.candidate.metadata["host_type"] == "repository"
    assert result.candidate.metadata["license"] == "cc-by"
    assert result.candidate.metadata["oa_status"] == "gold"
    assert result.evidence[0].source == "unpaywall"
    assert result.evidence[0].evidence_type == "api_license"
    assert result.evidence[0].value == "cc-by"
    assert result.evidence[0].suggested_status is LicenseStatus.ALLOWED


@pytest.mark.revised
def test_lookup_url_includes_encoded_doi_and_configured_email(
    recording_http_client_factory,
    json_response_factory,
) -> None:
    http_client = recording_http_client_factory.from_queue(
        [
            json_response_factory(
                url="https://api.unpaywall.org/v2/10.1234/bookhound.2026",
                payload=UNPAYWALL_FIXTURE,
            )
        ]
    )
    adapter = UnpaywallAdapter(
        http_client=http_client,
        config=UnpaywallAdapterConfig(email="researcher@example.org"),
    )

    adapter.enrich_doi("10.1234/bookhound.2026")

    parsed_url = urlsplit(http_client.urls[0])
    query = parse_qs(parsed_url.query)
    assert parsed_url.scheme == "https"
    assert parsed_url.netloc == "api.unpaywall.org"
    assert unquote(parsed_url.path) == "/v2/10.1234/bookhound.2026"
    assert query["email"] == ["researcher@example.org"]
    assert http_client.rate_limit_keys == ["source:unpaywall"]


@pytest.mark.revised
def test_record_without_oa_location_does_not_produce_false_allowed(
    recording_http_client_factory,
    json_response_factory,
) -> None:
    fixture = {
        "doi": "10.1234/closed",
        "title": "Closed Record",
        "is_oa": False,
        "oa_status": "closed",
        "best_oa_location": None,
    }
    adapter = UnpaywallAdapter(
        http_client=recording_http_client_factory.from_queue(
            [
                json_response_factory(
                    url="https://api.unpaywall.org/v2/10.1234/bookhound.2026",
                    payload=fixture,
                )
            ]
        ),
        config=UnpaywallAdapterConfig(email="researcher@example.org"),
    )

    result = adapter.enrich_doi("10.1234/closed")

    assert result.candidate is None
    assert result.evidence == []
    assert result.metadata["doi"] == "10.1234/closed"
    assert result.metadata["oa_status"] == "closed"


@pytest.mark.revised
def test_null_license_becomes_unknown_evidence(
    recording_http_client_factory,
    json_response_factory,
) -> None:
    fixture = {
        **UNPAYWALL_FIXTURE,
        "best_oa_location": {
            **UNPAYWALL_FIXTURE["best_oa_location"],
            "license": None,
        },
    }
    adapter = UnpaywallAdapter(
        http_client=recording_http_client_factory.from_queue(
            [
                json_response_factory(
                    url="https://api.unpaywall.org/v2/10.1234/bookhound.2026",
                    payload=fixture,
                )
            ]
        ),
        config=UnpaywallAdapterConfig(email="researcher@example.org"),
    )

    result = adapter.enrich_doi("10.1234/bookhound.2026")

    assert result.candidate is not None
    assert result.evidence[0].source == "unpaywall"
    assert result.evidence[0].evidence_type == "api_license"
    assert result.evidence[0].value == "unknown"
    assert result.evidence[0].suggested_status is LicenseStatus.UNKNOWN


@pytest.mark.revised
def test_required_email_in_configuration_is_validated() -> None:
    with pytest.raises(ValueError, match="email"):
        UnpaywallAdapterConfig(email="")


# Consolidated from test_common_crawl_adapter.py

from urllib.parse import parse_qs, urlsplit

import pytest

from bookhound.common_crawl import CommonCrawlAdapter, CommonCrawlAdapterConfig
from bookhound.models import DiscoveryMethod, SourceKind


CDXJ_FIXTURE = b"""org,example)/reports/climate-policy.pdf 20260601010101 {"url":"https://example.org/reports/climate-policy.pdf","mime":"application/pdf","status":"200","digest":"ABC123","length":"2048","offset":"512","filename":"crawl-data/CC-MAIN-2026-10/segments/file.warc.gz"}
"""


@pytest.mark.revised
def test_cdxj_fixture_becomes_candidates(
    recording_http_client_factory,
    text_response_factory,
) -> None:
    adapter = CommonCrawlAdapter(
        http_client=recording_http_client_factory.from_queue(
            [
                text_response_factory(
                    url="https://index.commoncrawl.org/CC-MAIN-2026-10-index",
                    content=CDXJ_FIXTURE,
                )
            ]
        ),
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
def test_non_pdf_entries_are_filtered_when_configured(
    recording_http_client_factory,
    text_response_factory,
) -> None:
    fixture = b"""org,example)/reports/page.html 20260601010101 {"url":"https://example.org/reports/page.html","mime":"text/html","status":"200"}
org,example)/reports/appendix.pdf 20260601010102 {"url":"https://example.org/reports/appendix.pdf","mime":"application/octet-stream","status":"200"}
"""
    adapter = CommonCrawlAdapter(
        http_client=recording_http_client_factory.from_queue(
            [
                text_response_factory(
                    url="https://index.commoncrawl.org/CC-MAIN-2026-10-index",
                    content=fixture,
                )
            ]
        ),
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
def test_crawls_are_queried_in_configured_order(
    recording_http_client_factory,
    text_response_factory,
) -> None:
    http_client = recording_http_client_factory.from_queue(
        [
            text_response_factory(
                url="https://index.commoncrawl.org/CC-MAIN-2026-10-index",
                content=b"",
            ),
            text_response_factory(
                url="https://index.commoncrawl.org/CC-MAIN-2026-10-index",
                content=CDXJ_FIXTURE,
            ),
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
def test_malformed_line_is_ignored_with_error_event(
    recording_http_client_factory,
    text_response_factory,
) -> None:
    fixture = b"""this is not valid cdxj
org,example)/reports/climate-policy.pdf 20260601010101 {"url":"https://example.org/reports/climate-policy.pdf","mime":"application/pdf","status":"200"}
"""
    adapter = CommonCrawlAdapter(
        http_client=recording_http_client_factory.from_queue(
            [
                text_response_factory(
                    url="https://index.commoncrawl.org/CC-MAIN-2026-10-index",
                    content=fixture,
                )
            ]
        ),
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


@pytest.mark.revised
def test_common_crawl_request_includes_result_limit(
    recording_http_client_factory,
    text_response_factory,
) -> None:
    http_client = recording_http_client_factory.from_queue(
        [
            text_response_factory(
                url="https://index.commoncrawl.org/CC-MAIN-2026-10-index",
                content=CDXJ_FIXTURE,
            )
        ]
    )
    adapter = CommonCrawlAdapter(
        http_client=http_client,
        config=CommonCrawlAdapterConfig(
            crawl_indexes=["CC-MAIN-2026-10"],
            result_limit=3,
        ),
    )

    adapter.search("climate policy")

    query = parse_qs(urlsplit(http_client.urls[0]).query)
    assert query["limit"] == ["3"]


@pytest.mark.revised
def test_common_crawl_requests_use_remaining_result_budget(
    recording_http_client_factory,
    text_response_factory,
) -> None:
    http_client = recording_http_client_factory.from_queue(
        [
            text_response_factory(
                url="https://index.commoncrawl.org/CC-MAIN-2026-10-index",
                content=_pdf_line("first.pdf"),
            ),
            text_response_factory(
                url="https://index.commoncrawl.org/CC-MAIN-2026-10-index",
                content=b"",
            ),
        ]
    )
    adapter = CommonCrawlAdapter(
        http_client=http_client,
        config=CommonCrawlAdapterConfig(
            crawl_indexes=["CC-MAIN-2026-05", "CC-MAIN-2026-10"],
            result_limit=3,
        ),
    )

    adapter.search("climate")

    queries = [parse_qs(urlsplit(url).query) for url in http_client.urls]
    assert [query["limit"] for query in queries] == [["3"], ["2"]]


@pytest.mark.revised
def test_parser_stops_after_result_limit_and_preserves_prior_events(
    recording_http_client_factory,
    text_response_factory,
) -> None:
    fixture = (
        b"this is not valid cdxj\n"
        + _pdf_line("first.pdf")
        + b"this later malformed line should not be inspected\n"
        + _pdf_line("second.pdf")
    )
    adapter = CommonCrawlAdapter(
        http_client=recording_http_client_factory.from_queue(
            [
                text_response_factory(
                    url="https://index.commoncrawl.org/CC-MAIN-2026-10-index",
                    content=fixture,
                )
            ]
        ),
        config=CommonCrawlAdapterConfig(
            crawl_indexes=["CC-MAIN-2026-10"],
            result_limit=1,
        ),
    )

    candidates = adapter.search("climate")

    assert [candidate.url for candidate in candidates] == [
        "https://example.org/reports/first.pdf"
    ]
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


def _pdf_line(filename: str, *, timestamp: str = "20260601010101") -> bytes:
    return (
        f'org,example)/reports/{filename} {timestamp} '
        f'{{"url":"https://example.org/reports/{filename}",'
        f'"mime":"application/pdf","status":"200"}}\n'
    ).encode("utf-8")
