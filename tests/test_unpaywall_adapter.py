import json
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from bookhound.http_client import HttpResponse
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
def test_best_oa_location_fixture_produces_candidate_and_license_evidence() -> None:
    adapter = UnpaywallAdapter(
        http_client=FakeHttpClient([_json_response(UNPAYWALL_FIXTURE)]),
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
def test_lookup_url_includes_encoded_doi_and_configured_email() -> None:
    http_client = FakeHttpClient([_json_response(UNPAYWALL_FIXTURE)])
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
def test_record_without_oa_location_does_not_produce_false_allowed() -> None:
    fixture = {
        "doi": "10.1234/closed",
        "title": "Closed Record",
        "is_oa": False,
        "oa_status": "closed",
        "best_oa_location": None,
    }
    adapter = UnpaywallAdapter(
        http_client=FakeHttpClient([_json_response(fixture)]),
        config=UnpaywallAdapterConfig(email="researcher@example.org"),
    )

    result = adapter.enrich_doi("10.1234/closed")

    assert result.candidate is None
    assert result.evidence == []
    assert result.metadata["doi"] == "10.1234/closed"
    assert result.metadata["oa_status"] == "closed"


@pytest.mark.revised
def test_null_license_becomes_unknown_evidence() -> None:
    fixture = {
        **UNPAYWALL_FIXTURE,
        "best_oa_location": {
            **UNPAYWALL_FIXTURE["best_oa_location"],
            "license": None,
        },
    }
    adapter = UnpaywallAdapter(
        http_client=FakeHttpClient([_json_response(fixture)]),
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


def _json_response(payload: dict[str, object], *, status_code: int = 200) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        headers={"content-type": "application/json"},
        content=json.dumps(payload).encode("utf-8"),
        url="https://api.unpaywall.org/v2/10.1234/bookhound.2026",
    )
