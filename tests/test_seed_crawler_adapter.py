import pytest

from bookhound.http_client import HttpResponse
from bookhound.models import DiscoveryMethod, SourceKind
from bookhound.seed_crawler import SeedCrawlerAdapter, SeedCrawlerConfig


SEED_HTML = b"""
<html>
  <body>
    <a href="/reports/open-report.pdf">Download report PDF</a>
    <a href="/reports/open-report">Open report landing page</a>
  </body>
</html>
"""


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


class FakeRobotsPolicy:
    def __init__(self, disallowed_urls: set[str] | None = None) -> None:
        self.disallowed_urls = disallowed_urls or set()
        self.checked_urls: list[str] = []

    def can_fetch(self, url: str) -> bool:
        self.checked_urls.append(url)
        return url not in self.disallowed_urls


@pytest.mark.revised
def test_seed_html_fixture_yields_direct_pdf_and_landing_page_candidates() -> None:
    adapter = SeedCrawlerAdapter(
        http_client=FakeHttpClient(
            {
                "https://example.org/library/": _html_response(
                    "https://example.org/library/",
                    SEED_HTML,
                )
            }
        ),
        robots_policy=FakeRobotsPolicy(),
        config=SeedCrawlerConfig(
            seed_urls=["https://example.org/library/"],
            max_depth=0,
            max_pages_per_seed=1,
        ),
    )

    candidates = adapter.search("open report")

    assert adapter.source_name is SourceKind.SEED_CRAWLER
    assert adapter.discovery_method is DiscoveryMethod.CRAWL
    assert [(candidate.title, candidate.url) for candidate in candidates] == [
        ("Download report PDF", "https://example.org/reports/open-report.pdf"),
        ("Open report landing page", "https://example.org/reports/open-report"),
    ]
    assert [candidate.metadata["url_type"] for candidate in candidates] == [
        "pdf",
        "landing_page",
    ]
    assert all(candidate.query == "open report" for candidate in candidates)


@pytest.mark.revised
def test_off_domain_links_are_ignored_unless_explicitly_allowed() -> None:
    html = b"""
    <html>
      <body>
        <a href="https://example.org/local.pdf">Local PDF</a>
        <a href="https://partner.example.net/remote.pdf">Partner PDF</a>
      </body>
    </html>
    """
    responses = {
        "https://example.org/library/": _html_response(
            "https://example.org/library/",
            html,
        )
    }

    default_adapter = SeedCrawlerAdapter(
        http_client=FakeHttpClient(responses),
        robots_policy=FakeRobotsPolicy(),
        config=SeedCrawlerConfig(seed_urls=["https://example.org/library/"]),
    )
    allowed_adapter = SeedCrawlerAdapter(
        http_client=FakeHttpClient(responses),
        robots_policy=FakeRobotsPolicy(),
        config=SeedCrawlerConfig(
            seed_urls=["https://example.org/library/"],
            allowed_domains=["partner.example.net"],
        ),
    )

    assert [candidate.url for candidate in default_adapter.search("reports")] == [
        "https://example.org/local.pdf"
    ]
    assert [candidate.url for candidate in allowed_adapter.search("reports")] == [
        "https://example.org/local.pdf",
        "https://partner.example.net/remote.pdf",
    ]


@pytest.mark.revised
def test_depth_and_page_count_limits_stop_expansion() -> None:
    http_client = FakeHttpClient(
        {
            "https://example.org/seed/": _html_response(
                "https://example.org/seed/",
                b'<a href="/level-one/">Level one</a>',
            ),
            "https://example.org/level-one/": _html_response(
                "https://example.org/level-one/",
                b'<a href="/level-two/">Level two</a><a href="/level-one.pdf">Level one PDF</a>',
            ),
            "https://example.org/level-two/": _html_response(
                "https://example.org/level-two/",
                b'<a href="/too-deep.pdf">Too deep PDF</a>',
            ),
        }
    )
    adapter = SeedCrawlerAdapter(
        http_client=http_client,
        robots_policy=FakeRobotsPolicy(),
        config=SeedCrawlerConfig(
            seed_urls=["https://example.org/seed/"],
            max_depth=1,
            max_pages_per_seed=2,
        ),
    )

    candidates = adapter.search("limits")

    assert [candidate.url for candidate in candidates] == [
        "https://example.org/level-one/",
        "https://example.org/level-one.pdf",
    ]
    assert http_client.urls == [
        "https://example.org/seed/",
        "https://example.org/level-one/",
    ]


@pytest.mark.revised
def test_robots_disallowed_urls_are_skipped_and_recorded_as_events() -> None:
    robots_policy = FakeRobotsPolicy(
        disallowed_urls={"https://example.org/private/"}
    )
    adapter = SeedCrawlerAdapter(
        http_client=FakeHttpClient(
            {
                "https://example.org/seed/": _html_response(
                    "https://example.org/seed/",
                    b'<a href="/private/">Private page</a><a href="/public.pdf">Public PDF</a>',
                )
            }
        ),
        robots_policy=robots_policy,
        config=SeedCrawlerConfig(seed_urls=["https://example.org/seed/"]),
    )

    candidates = adapter.search("robots")

    assert [candidate.url for candidate in candidates] == [
        "https://example.org/public.pdf"
    ]
    assert adapter.events == [
        {
            "event_type": "seed_crawler.robots_disallowed",
            "message": "Skipped URL disallowed by robots policy.",
            "metadata": {
                "url": "https://example.org/private/",
                "seed_url": "https://example.org/seed/",
            },
        }
    ]


def _html_response(url: str, content: bytes) -> HttpResponse:
    return HttpResponse(
        status_code=200,
        headers={"content-type": "text/html"},
        content=content,
        url=url,
    )
