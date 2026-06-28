import pytest

from bookhound.http_client import HttpResponse
from bookhound.link_expansion import LinkExpansionAdapter, LinkExpansionConfig
from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind


class FakeHttpClient:
    def __init__(self, responses_by_url: dict[str, HttpResponse]) -> None:
        self.responses_by_url = responses_by_url
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
        return self.responses_by_url[url]


@pytest.mark.revised
def test_relevant_landing_page_produces_nearby_pdf_candidates() -> None:
    landing_page = _candidate(
        title="Climate Policy Landing Page",
        url="https://example.org/reports/climate-policy",
    )
    adapter = LinkExpansionAdapter(
        http_client=FakeHttpClient(
            {
                "https://example.org/reports/climate-policy": _html_response(
                    "https://example.org/reports/climate-policy",
                    b"""
                    <html>
                      <body>
                        <h1>Climate Policy Report</h1>
                        <p>Download the full report and technical appendix.</p>
                        <a href="/reports/climate-policy.pdf">Full report PDF</a>
                        <a href="/reports/climate-appendix.pdf">Technical appendix PDF</a>
                      </body>
                    </html>
                    """,
                )
            }
        ),
        config=LinkExpansionConfig(max_depth=1, max_candidates=10),
    )

    candidates = adapter.expand([landing_page], query="climate policy")

    assert adapter.source_name is SourceKind.LINK_EXPANSION
    assert adapter.discovery_method is DiscoveryMethod.LINK_EXPANSION
    assert [(candidate.title, candidate.url) for candidate in candidates] == [
        ("Full report PDF", "https://example.org/reports/climate-policy.pdf"),
        ("Technical appendix PDF", "https://example.org/reports/climate-appendix.pdf"),
    ]
    assert all(candidate.source is SourceKind.LINK_EXPANSION for candidate in candidates)
    assert all(candidate.query == "climate policy" for candidate in candidates)
    assert candidates[0].metadata == {
        "source_candidate_url": "https://example.org/reports/climate-policy",
        "source_page_url": "https://example.org/reports/climate-policy",
        "anchor_text": "Full report PDF",
        "depth": 1,
        "url_type": "pdf",
    }


@pytest.mark.revised
def test_already_seen_urls_are_not_requeued() -> None:
    landing_page = _candidate(
        title="Known Landing Page",
        url="https://example.org/reports/known",
    )
    known_pdf = _candidate(
        title="Known PDF",
        url="https://example.org/reports/known.pdf",
    )
    adapter = LinkExpansionAdapter(
        http_client=FakeHttpClient(
            {
                "https://example.org/reports/known": _html_response(
                    "https://example.org/reports/known",
                    b"""
                    <html>
                      <body>
                        <a href="/reports/known.pdf">Known PDF</a>
                        <a href="/reports/new.pdf">New PDF</a>
                        <a href="/reports/known">Loop back</a>
                      </body>
                    </html>
                    """,
                )
            }
        ),
        config=LinkExpansionConfig(max_depth=2, max_candidates=10),
    )

    candidates = adapter.expand([landing_page, known_pdf], query="known")

    assert [candidate.url for candidate in candidates] == [
        "https://example.org/reports/new.pdf"
    ]


@pytest.mark.revised
def test_expansion_stays_within_configured_domain_policy() -> None:
    landing_page = _candidate(
        title="Repository Page",
        url="https://example.org/reports/index",
    )
    responses = {
        "https://example.org/reports/index": _html_response(
            "https://example.org/reports/index",
            b"""
            <html>
              <body>
                <a href="/reports/local.pdf">Local PDF</a>
                <a href="https://partner.example.net/remote.pdf">Partner PDF</a>
              </body>
            </html>
            """,
        )
    }

    default_adapter = LinkExpansionAdapter(
        http_client=FakeHttpClient(responses),
        config=LinkExpansionConfig(),
    )
    allowed_adapter = LinkExpansionAdapter(
        http_client=FakeHttpClient(responses),
        config=LinkExpansionConfig(allowed_domains=["partner.example.net"]),
    )

    assert [candidate.url for candidate in default_adapter.expand([landing_page], query="reports")] == [
        "https://example.org/reports/local.pdf"
    ]
    assert [candidate.url for candidate in allowed_adapter.expand([landing_page], query="reports")] == [
        "https://example.org/reports/local.pdf",
        "https://partner.example.net/remote.pdf",
    ]


@pytest.mark.revised
def test_frontier_stops_at_configured_depth_and_candidate_limits() -> None:
    landing_page = _candidate(
        title="Root Landing Page",
        url="https://example.org/root",
    )
    http_client = FakeHttpClient(
        {
            "https://example.org/root": _html_response(
                "https://example.org/root",
                b"""
                <html>
                  <body>
                    <a href="/first.pdf">First PDF</a>
                    <a href="/first-landing">First landing page</a>
                    <a href="/second.pdf">Second PDF</a>
                  </body>
                </html>
                """,
            ),
            "https://example.org/first-landing": _html_response(
                "https://example.org/first-landing",
                b'<a href="/too-deep.pdf">Too deep PDF</a>',
            ),
        }
    )
    adapter = LinkExpansionAdapter(
        http_client=http_client,
        config=LinkExpansionConfig(max_depth=1, max_candidates=2),
    )

    candidates = adapter.expand([landing_page], query="bounded")

    assert [candidate.url for candidate in candidates] == [
        "https://example.org/first.pdf",
        "https://example.org/first-landing",
    ]
    assert http_client.urls == ["https://example.org/root"]


def _candidate(title: str, url: str) -> RawCandidate:
    return RawCandidate(
        title=title,
        url=url,
        source=SourceKind.SITEMAP,
        discovery_method=DiscoveryMethod.SITEMAP,
        query="original query",
    )


def _html_response(url: str, content: bytes) -> HttpResponse:
    return HttpResponse(
        status_code=200,
        headers={"content-type": "text/html"},
        content=content,
        url=url,
    )
