import json
from urllib.parse import parse_qs, urlsplit

import pytest

from bookhound.google_search import GoogleSearchAdapter, GoogleSearchAdapterConfig
from bookhound.http_client import HttpResponse
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
def test_google_json_fixture_becomes_candidates() -> None:
    adapter = GoogleSearchAdapter(
        http_client=FakeHttpClient([_json_response(GOOGLE_FIXTURE)]),
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
def test_sent_query_preserves_the_planned_variant() -> None:
    http_client = FakeHttpClient([_json_response(GOOGLE_FIXTURE)])
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
def test_quota_error_becomes_typed_error_and_does_not_take_down_pipeline() -> None:
    adapter = GoogleSearchAdapter(
        http_client=FakeHttpClient(
            [
                _json_response(
                    {
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


def _json_response(payload: dict[str, object], *, status_code: int = 200) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        headers={"content-type": "application/json"},
        content=json.dumps(payload).encode("utf-8"),
        url="https://www.googleapis.com/customsearch/v1",
    )
