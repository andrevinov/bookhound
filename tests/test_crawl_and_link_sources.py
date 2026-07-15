# Consolidated test module. See docs/audit-tests/03-centralize-test-files.md.
# Consolidated from test_sitemap_adapter.py

import pytest

from bookhound.models import DiscoveryMethod, SourceKind
from bookhound.sitemap import SitemapAdapter, SitemapAdapterConfig


ROBOTS_TXT = b"""
User-agent: *
Allow: /
Sitemap: https://example.org/sitemap-index.xml
Sitemap: https://example.org/secondary-sitemap.xml
"""

SITEMAP_INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://example.org/reports-sitemap.xml</loc>
    <lastmod>2026-06-01</lastmod>
  </sitemap>
</sitemapindex>
"""

URL_SET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.org/reports/climate-policy.pdf</loc>
    <lastmod>2026-06-15</lastmod>
  </url>
  <url>
    <loc>https://example.org/reports/open-access-field-guide</loc>
    <lastmod>2026-06-16</lastmod>
  </url>
  <url>
    <loc>https://example.org/about</loc>
  </url>
</urlset>
"""


@pytest.mark.revised
def test_robots_txt_fixture_points_to_sitemap_urls(
    recording_http_client_factory,
    xml_response_factory,
) -> None:
    http_client = recording_http_client_factory.from_mapping(
        {
            "https://example.org/robots.txt": xml_response_factory(
                url="https://example.org/robots.txt",
                content=ROBOTS_TXT,
            ),
            "https://example.org/sitemap-index.xml": xml_response_factory(
                url="https://example.org/sitemap-index.xml",
                content=SITEMAP_INDEX,
            ),
            "https://example.org/secondary-sitemap.xml": xml_response_factory(
                url="https://example.org/secondary-sitemap.xml",
                content=b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" />',
            ),
            "https://example.org/reports-sitemap.xml": xml_response_factory(
                url="https://example.org/reports-sitemap.xml",
                content=URL_SET,
            ),
        }
    )
    adapter = SitemapAdapter(
        http_client=http_client,
        config=SitemapAdapterConfig(domain_roots=["https://example.org/"]),
    )

    adapter.search("climate")

    assert http_client.urls[:3] == [
        "https://example.org/robots.txt",
        "https://example.org/sitemap-index.xml",
        "https://example.org/secondary-sitemap.xml",
    ]
    assert http_client.rate_limit_keys == [
        "source:sitemap",
        "source:sitemap",
        "source:sitemap",
        "source:sitemap",
    ]


@pytest.mark.revised
def test_sitemap_index_fixture_expands_to_child_sitemaps(
    recording_http_client_factory,
    xml_response_factory,
) -> None:
    http_client = recording_http_client_factory.from_mapping(
        {
            "https://example.org/robots.txt": xml_response_factory(
                url="https://example.org/robots.txt",
                content=ROBOTS_TXT,
            ),
            "https://example.org/sitemap-index.xml": xml_response_factory(
                url="https://example.org/sitemap-index.xml",
                content=SITEMAP_INDEX,
            ),
            "https://example.org/secondary-sitemap.xml": xml_response_factory(
                url="https://example.org/secondary-sitemap.xml",
                content=b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" />',
            ),
            "https://example.org/reports-sitemap.xml": xml_response_factory(
                url="https://example.org/reports-sitemap.xml",
                content=URL_SET,
            ),
        }
    )
    adapter = SitemapAdapter(
        http_client=http_client,
        config=SitemapAdapterConfig(domain_roots=["https://example.org/"]),
    )

    adapter.search("field guide")

    assert "https://example.org/reports-sitemap.xml" in http_client.urls


@pytest.mark.revised
def test_url_set_fixture_yields_pdf_candidates(
    recording_http_client_factory,
    xml_response_factory,
) -> None:
    adapter = SitemapAdapter(
        http_client=recording_http_client_factory.from_mapping(
            {
                "https://example.org/robots.txt": xml_response_factory(
                    url="https://example.org/robots.txt",
                    content=b"Sitemap: https://example.org/reports-sitemap.xml",
                ),
                "https://example.org/reports-sitemap.xml": xml_response_factory(
                    url="https://example.org/reports-sitemap.xml",
                    content=URL_SET,
                ),
            }
        ),
        config=SitemapAdapterConfig(domain_roots=["https://example.org/"]),
    )

    candidates = adapter.search("climate")

    assert adapter.source_name is SourceKind.SITEMAP
    assert adapter.discovery_method is DiscoveryMethod.SITEMAP
    assert [(candidate.title, candidate.url) for candidate in candidates] == [
        (
            "climate-policy.pdf",
            "https://example.org/reports/climate-policy.pdf",
        ),
        (
            "open-access-field-guide",
            "https://example.org/reports/open-access-field-guide",
        ),
    ]
    assert [candidate.metadata["url_type"] for candidate in candidates] == [
        "pdf",
        "landing_page",
    ]
    assert candidates[0].metadata == {
        "sitemap_url": "https://example.org/reports-sitemap.xml",
        "lastmod": "2026-06-15",
        "url_type": "pdf",
    }


@pytest.mark.revised
def test_malformed_sitemap_entries_are_ignored_with_error_event(
    recording_http_client_factory,
    xml_response_factory,
) -> None:
    malformed_url_set = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <lastmod>2026-06-15</lastmod>
  </url>
  <url>
    <loc>https://example.org/reports/valid.pdf</loc>
  </url>
</urlset>
"""
    adapter = SitemapAdapter(
        http_client=recording_http_client_factory.from_mapping(
            {
                "https://example.org/robots.txt": xml_response_factory(
                    url="https://example.org/robots.txt",
                    content=b"Sitemap: https://example.org/reports-sitemap.xml",
                ),
                "https://example.org/reports-sitemap.xml": xml_response_factory(
                    url="https://example.org/reports-sitemap.xml",
                    content=malformed_url_set,
                ),
            }
        ),
        config=SitemapAdapterConfig(domain_roots=["https://example.org/"]),
    )

    candidates = adapter.search("valid")

    assert [candidate.url for candidate in candidates] == [
        "https://example.org/reports/valid.pdf"
    ]
    assert adapter.events == [
        {
            "event_type": "sitemap.malformed_entry",
            "message": "Ignored sitemap entry without a URL.",
            "metadata": {
                "sitemap_url": "https://example.org/reports-sitemap.xml",
            },
        }
    ]


# Consolidated from test_sitemap_frontier_policy.py

import pytest

from bookhound.sitemap import SitemapAdapter, SitemapAdapterConfig


@pytest.mark.revised
def test_robots_sitemaps_outside_configured_domain_are_rejected(
    recording_http_client_factory,
    text_response_factory,
    xml_response_factory,
    sitemap_urlset_xml_factory,
) -> None:
    http_client = recording_http_client_factory.from_mapping(
        {
            "https://example.org/robots.txt": text_response_factory(
                url="https://example.org/robots.txt",
                content=b"""
Sitemap: https://outside.example.net/remote-sitemap.xml
Sitemap: https://example.org/local-sitemap.xml
""",
            ),
            "https://outside.example.net/remote-sitemap.xml": xml_response_factory(
                url="https://outside.example.net/remote-sitemap.xml",
                content=sitemap_urlset_xml_factory(
                    ["https://outside.example.net/reports/remote.pdf"]
                ),
            ),
            "https://example.org/local-sitemap.xml": xml_response_factory(
                url="https://example.org/local-sitemap.xml",
                content=sitemap_urlset_xml_factory(
                    ["https://example.org/reports/local.pdf"]
                ),
            ),
        }
    )
    adapter = SitemapAdapter(
        http_client=http_client,
        config=SitemapAdapterConfig(domain_roots=["https://example.org/"]),
    )

    candidates = adapter.search("frontier")

    assert [candidate.url for candidate in candidates] == [
        "https://example.org/reports/local.pdf"
    ]
    assert "https://outside.example.net/remote-sitemap.xml" not in http_client.urls
    assert _event_urls(adapter, "sitemap.frontier_rejected") == [
        "https://outside.example.net/remote-sitemap.xml"
    ]


@pytest.mark.revised
def test_private_and_non_http_sitemap_references_are_rejected(
    recording_http_client_factory,
    text_response_factory,
    xml_response_factory,
    sitemap_urlset_xml_factory,
) -> None:
    http_client = recording_http_client_factory.from_mapping(
        {
            "https://example.org/robots.txt": text_response_factory(
                url="https://example.org/robots.txt",
                content=b"""
Sitemap: file:///etc/passwd
Sitemap: http://localhost:8080/private-sitemap.xml
Sitemap: http://127.0.0.1/private-sitemap.xml
Sitemap: https://example.org/public-sitemap.xml
""",
            ),
            "file:///etc/passwd": xml_response_factory(
                url="file:///etc/passwd",
                content=sitemap_urlset_xml_factory(["file:///secret.pdf"]),
            ),
            "http://localhost:8080/private-sitemap.xml": xml_response_factory(
                url="http://localhost:8080/private-sitemap.xml",
                content=sitemap_urlset_xml_factory(
                    ["http://localhost:8080/private.pdf"]
                ),
            ),
            "http://127.0.0.1/private-sitemap.xml": xml_response_factory(
                url="http://127.0.0.1/private-sitemap.xml",
                content=sitemap_urlset_xml_factory(
                    ["http://127.0.0.1/private.pdf"]
                ),
            ),
            "https://example.org/public-sitemap.xml": xml_response_factory(
                url="https://example.org/public-sitemap.xml",
                content=sitemap_urlset_xml_factory(
                    ["https://example.org/reports/public.pdf"]
                ),
            ),
        }
    )
    adapter = SitemapAdapter(
        http_client=http_client,
        config=SitemapAdapterConfig(domain_roots=["https://example.org/"]),
    )

    candidates = adapter.search("safe frontier")

    assert [candidate.url for candidate in candidates] == [
        "https://example.org/reports/public.pdf"
    ]
    assert "file:///etc/passwd" not in http_client.urls
    assert "http://localhost:8080/private-sitemap.xml" not in http_client.urls
    assert "http://127.0.0.1/private-sitemap.xml" not in http_client.urls
    assert _event_urls(adapter, "sitemap.frontier_rejected") == [
        "file:///etc/passwd",
        "http://localhost:8080/private-sitemap.xml",
        "http://127.0.0.1/private-sitemap.xml",
    ]


@pytest.mark.revised
def test_explicit_allowed_sitemap_domain_can_be_traversed(
    recording_http_client_factory,
    text_response_factory,
    xml_response_factory,
    sitemap_urlset_xml_factory,
) -> None:
    http_client = recording_http_client_factory.from_mapping(
        {
            "https://example.org/robots.txt": text_response_factory(
                url="https://example.org/robots.txt",
                content=b"Sitemap: https://cdn.example.net/sitemap.xml",
            ),
            "https://cdn.example.net/sitemap.xml": xml_response_factory(
                url="https://cdn.example.net/sitemap.xml",
                content=sitemap_urlset_xml_factory(
                    ["https://example.org/reports/cdn-indexed.pdf"]
                ),
            ),
        }
    )
    adapter = SitemapAdapter(
        http_client=http_client,
        config=SitemapAdapterConfig(
            domain_roots=["https://example.org/"],
            allowed_sitemap_domains=["cdn.example.net"],
        ),
    )

    candidates = adapter.search("allowed cdn")

    assert [candidate.url for candidate in candidates] == [
        "https://example.org/reports/cdn-indexed.pdf"
    ]
    assert "https://cdn.example.net/sitemap.xml" in http_client.urls
    assert _event_urls(adapter, "sitemap.frontier_rejected") == []


@pytest.mark.revised
def test_sitemap_file_limit_stops_oversized_queue(
    recording_http_client_factory,
    text_response_factory,
    xml_response_factory,
    sitemap_index_xml_factory,
    sitemap_urlset_xml_factory,
) -> None:
    http_client = recording_http_client_factory.from_mapping(
        {
            "https://example.org/robots.txt": text_response_factory(
                url="https://example.org/robots.txt",
                content=b"Sitemap: https://example.org/root-index.xml",
            ),
            "https://example.org/root-index.xml": xml_response_factory(
                url="https://example.org/root-index.xml",
                content=sitemap_index_xml_factory(
                    [
                        "https://example.org/first.xml",
                        "https://example.org/second.xml",
                        "https://example.org/third.xml",
                    ]
                ),
            ),
            "https://example.org/first.xml": xml_response_factory(
                url="https://example.org/first.xml",
                content=sitemap_urlset_xml_factory(
                    ["https://example.org/reports/first.pdf"]
                ),
            ),
            "https://example.org/second.xml": xml_response_factory(
                url="https://example.org/second.xml",
                content=sitemap_urlset_xml_factory(
                    ["https://example.org/reports/second.pdf"]
                ),
            ),
            "https://example.org/third.xml": xml_response_factory(
                url="https://example.org/third.xml",
                content=sitemap_urlset_xml_factory(
                    ["https://example.org/reports/third.pdf"]
                ),
            ),
        }
    )
    adapter = SitemapAdapter(
        http_client=http_client,
        config=SitemapAdapterConfig(
            domain_roots=["https://example.org/"],
            max_sitemap_files=2,
        ),
    )

    candidates = adapter.search("file cap")

    assert [candidate.url for candidate in candidates] == [
        "https://example.org/reports/first.pdf"
    ]
    assert "https://example.org/second.xml" not in http_client.urls
    assert "https://example.org/third.xml" not in http_client.urls
    assert _event_urls(adapter, "sitemap.traversal_limit_reached") == [
        "https://example.org/second.xml",
        "https://example.org/third.xml",
    ]


@pytest.mark.revised
def test_url_entry_limit_stops_oversized_urlset(
    recording_http_client_factory,
    text_response_factory,
    xml_response_factory,
    sitemap_urlset_xml_factory,
) -> None:
    http_client = recording_http_client_factory.from_mapping(
        {
            "https://example.org/robots.txt": text_response_factory(
                url="https://example.org/robots.txt",
                content=b"Sitemap: https://example.org/reports.xml",
            ),
            "https://example.org/reports.xml": xml_response_factory(
                url="https://example.org/reports.xml",
                content=sitemap_urlset_xml_factory(
                    [
                        "https://example.org/reports/first.pdf",
                        "https://example.org/reports/second.pdf",
                    ]
                ),
            ),
        }
    )
    adapter = SitemapAdapter(
        http_client=http_client,
        config=SitemapAdapterConfig(
            domain_roots=["https://example.org/"],
            max_url_entries=1,
        ),
    )

    candidates = adapter.search("entry cap")

    assert [candidate.url for candidate in candidates] == [
        "https://example.org/reports/first.pdf"
    ]
    assert _event_urls(adapter, "sitemap.url_entry_limit_reached") == [
        "https://example.org/reports/second.pdf"
    ]


@pytest.mark.revised
def test_valid_same_domain_sitemap_index_traversal_still_returns_candidates(
    recording_http_client_factory,
    text_response_factory,
    xml_response_factory,
    sitemap_index_xml_factory,
    sitemap_urlset_xml_factory,
) -> None:
    http_client = recording_http_client_factory.from_mapping(
        {
            "https://example.org/robots.txt": text_response_factory(
                url="https://example.org/robots.txt",
                content=b"Sitemap: https://example.org/root-index.xml",
            ),
            "https://example.org/root-index.xml": xml_response_factory(
                url="https://example.org/root-index.xml",
                content=sitemap_index_xml_factory(
                    ["https://example.org/reports.xml"]
                ),
            ),
            "https://example.org/reports.xml": xml_response_factory(
                url="https://example.org/reports.xml",
                content=sitemap_urlset_xml_factory(
                    ["https://example.org/reports/allowed.pdf"]
                ),
            ),
        }
    )
    adapter = SitemapAdapter(
        http_client=http_client,
        config=SitemapAdapterConfig(domain_roots=["https://example.org/"]),
    )

    candidates = adapter.search("same domain")

    assert [candidate.url for candidate in candidates] == [
        "https://example.org/reports/allowed.pdf"
    ]
    assert http_client.urls == [
        "https://example.org/robots.txt",
        "https://example.org/root-index.xml",
        "https://example.org/reports.xml",
    ]
    assert _event_urls(adapter, "sitemap.frontier_rejected") == []


def _event_urls(adapter: SitemapAdapter, event_type: str) -> list[str]:
    return [
        str(event["metadata"]["url"])
        for event in adapter.events
        if event["event_type"] == event_type
    ]


# Consolidated from test_seed_crawler_adapter.py

import pytest

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


class FakeRobotsPolicy:
    def __init__(self, disallowed_urls: set[str] | None = None) -> None:
        self.disallowed_urls = disallowed_urls or set()
        self.checked_urls: list[str] = []

    def can_fetch(self, url: str) -> bool:
        self.checked_urls.append(url)
        return url not in self.disallowed_urls


@pytest.mark.revised
def test_seed_html_fixture_yields_direct_pdf_and_landing_page_candidates(
    recording_http_client_factory,
    html_response_factory,
) -> None:
    adapter = SeedCrawlerAdapter(
        http_client=recording_http_client_factory.from_mapping(
            {
                "https://example.org/library/": html_response_factory(
                    url="https://example.org/library/",
                    content=SEED_HTML,
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
def test_seed_crawler_decodes_non_utf8_html_and_extracts_links(
    recording_http_client_factory,
    html_response_factory,
) -> None:
    html = b"""
    <html>
      <body>
        <p>Legacy text byte: \xf4</p>
        <a href="/reports/legacy-seed.pdf">Legacy seed PDF</a>
        <a href="/reports/legacy-seed">Legacy seed landing page</a>
      </body>
    </html>
    """
    adapter = SeedCrawlerAdapter(
        http_client=recording_http_client_factory.from_mapping(
            {
                "https://example.org/library/": html_response_factory(
                    url="https://example.org/library/",
                    content=html,
                    headers={"content-type": "text/html; charset=windows-1252"},
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

    candidates = adapter.search("legacy encoding")

    assert [(candidate.title, candidate.url) for candidate in candidates] == [
        ("Legacy seed PDF", "https://example.org/reports/legacy-seed.pdf"),
        (
            "Legacy seed landing page",
            "https://example.org/reports/legacy-seed",
        ),
    ]
    assert [candidate.metadata["url_type"] for candidate in candidates] == [
        "pdf",
        "landing_page",
    ]
    assert all(candidate.query == "legacy encoding" for candidate in candidates)


@pytest.mark.revised
def test_off_domain_links_are_ignored_unless_explicitly_allowed(
    recording_http_client_factory,
    html_response_factory,
) -> None:
    html = b"""
    <html>
      <body>
        <a href="https://example.org/local.pdf">Local PDF</a>
        <a href="https://partner.example.net/remote.pdf">Partner PDF</a>
      </body>
    </html>
    """
    responses = {
        "https://example.org/library/": html_response_factory(
            url="https://example.org/library/",
            content=html,
        )
    }

    default_adapter = SeedCrawlerAdapter(
        http_client=recording_http_client_factory.from_mapping(responses),
        robots_policy=FakeRobotsPolicy(),
        config=SeedCrawlerConfig(seed_urls=["https://example.org/library/"]),
    )
    allowed_adapter = SeedCrawlerAdapter(
        http_client=recording_http_client_factory.from_mapping(responses),
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
def test_depth_and_page_count_limits_stop_expansion(
    recording_http_client_factory,
    html_response_factory,
) -> None:
    http_client = recording_http_client_factory.from_mapping(
        {
            "https://example.org/seed/": html_response_factory(
                url="https://example.org/seed/",
                content=b'<a href="/level-one/">Level one</a>',
            ),
            "https://example.org/level-one/": html_response_factory(
                url="https://example.org/level-one/",
                content=b'<a href="/level-two/">Level two</a><a href="/level-one.pdf">Level one PDF</a>',
            ),
            "https://example.org/level-two/": html_response_factory(
                url="https://example.org/level-two/",
                content=b'<a href="/too-deep.pdf">Too deep PDF</a>',
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
def test_robots_disallowed_urls_are_skipped_and_recorded_as_events(
    recording_http_client_factory,
    html_response_factory,
) -> None:
    robots_policy = FakeRobotsPolicy(
        disallowed_urls={"https://example.org/private/"}
    )
    adapter = SeedCrawlerAdapter(
        http_client=recording_http_client_factory.from_mapping(
            {
                "https://example.org/seed/": html_response_factory(
                    url="https://example.org/seed/",
                    content=b'<a href="/private/">Private page</a><a href="/public.pdf">Public PDF</a>',
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


# Consolidated from test_link_expansion_adapter.py

import pytest

from bookhound.link_expansion import LinkExpansionAdapter, LinkExpansionConfig
from bookhound.models import DiscoveryMethod, SourceKind


@pytest.mark.revised
def test_relevant_landing_page_produces_nearby_pdf_candidates(
    recording_http_client_factory,
    html_response_factory,
    sitemap_candidate_factory,
) -> None:
    landing_page = sitemap_candidate_factory(
        title="Climate Policy Landing Page",
        url="https://example.org/reports/climate-policy",
        query="original query",
        score=None,
    )
    adapter = LinkExpansionAdapter(
        http_client=recording_http_client_factory.from_mapping(
            {
                "https://example.org/reports/climate-policy": html_response_factory(
                    url="https://example.org/reports/climate-policy",
                    content=b"""
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
def test_already_seen_urls_are_not_requeued(
    recording_http_client_factory,
    html_response_factory,
    sitemap_candidate_factory,
) -> None:
    landing_page = sitemap_candidate_factory(
        title="Known Landing Page",
        url="https://example.org/reports/known",
        query="original query",
        score=None,
    )
    known_pdf = sitemap_candidate_factory(
        title="Known PDF",
        url="https://example.org/reports/known.pdf",
        query="original query",
        score=None,
    )
    adapter = LinkExpansionAdapter(
        http_client=recording_http_client_factory.from_mapping(
            {
                "https://example.org/reports/known": html_response_factory(
                    url="https://example.org/reports/known",
                    content=b"""
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
def test_expansion_stays_within_configured_domain_policy(
    recording_http_client_factory,
    html_response_factory,
    sitemap_candidate_factory,
) -> None:
    landing_page = sitemap_candidate_factory(
        title="Repository Page",
        url="https://example.org/reports/index",
        query="original query",
        score=None,
    )
    responses = {
        "https://example.org/reports/index": html_response_factory(
            url="https://example.org/reports/index",
            content=b"""
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
        http_client=recording_http_client_factory.from_mapping(responses),
        config=LinkExpansionConfig(),
    )
    allowed_adapter = LinkExpansionAdapter(
        http_client=recording_http_client_factory.from_mapping(responses),
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
def test_non_successful_expansion_page_is_skipped_and_frontier_continues(
    recording_http_client_factory,
    html_response_factory,
    http_response_factory,
    sitemap_candidate_factory,
) -> None:
    redirecting_page = sitemap_candidate_factory(
        title="Donation Page",
        url="https://openstax.org/give",
        query="original query",
        score=None,
    )
    healthy_page = sitemap_candidate_factory(
        title="Healthy Landing Page",
        url="https://openstax.org/books/college-success/pages/index",
        query="original query",
        score=None,
    )
    http_client = recording_http_client_factory.from_mapping(
        {
            "https://openstax.org/give": http_response_factory(
                url="https://openstax.org/give",
                status_code=301,
                content_type="text/html",
                headers={
                    "location": "https://riceconnect.rice.edu/donation/support-openstax"
                },
                content=b"<html><body>Moved permanently.</body></html>",
            ),
            "https://openstax.org/books/college-success/pages/index": html_response_factory(
                url="https://openstax.org/books/college-success/pages/index",
                content=b"""
                <html>
                  <body>
                    <a href="/reports/open-report.pdf">Open report PDF</a>
                  </body>
                </html>
                """,
            ),
        }
    )
    adapter = LinkExpansionAdapter(
        http_client=http_client,
        config=LinkExpansionConfig(max_depth=1, max_candidates=10),
    )

    candidates = adapter.expand(
        [redirecting_page, healthy_page],
        query="machine learning",
    )

    assert http_client.urls == [
        "https://openstax.org/give",
        "https://openstax.org/books/college-success/pages/index",
    ]
    assert [(candidate.title, candidate.url) for candidate in candidates] == [
        ("Open report PDF", "https://openstax.org/reports/open-report.pdf")
    ]
    assert candidates[0].metadata == {
        "source_candidate_url": "https://openstax.org/books/college-success/pages/index",
        "source_page_url": "https://openstax.org/books/college-success/pages/index",
        "anchor_text": "Open report PDF",
        "depth": 1,
        "url_type": "pdf",
    }


@pytest.mark.revised
def test_expansion_decodes_non_utf8_html_and_extracts_links(
    recording_http_client_factory,
    html_response_factory,
    sitemap_candidate_factory,
) -> None:
    landing_page = sitemap_candidate_factory(
        title="Legacy Encoded Landing Page",
        url="https://example.org/reports/legacy",
        query="original query",
        score=None,
    )
    html = b"""
    <html>
      <body>
        <p>Legacy text byte: \xf4</p>
        <a href="/reports/legacy.pdf">Legacy PDF</a>
      </body>
    </html>
    """
    adapter = LinkExpansionAdapter(
        http_client=recording_http_client_factory.from_mapping(
            {
                "https://example.org/reports/legacy": html_response_factory(
                    url="https://example.org/reports/legacy",
                    content=html,
                    headers={"content-type": "text/html; charset=windows-1252"},
                )
            }
        ),
        config=LinkExpansionConfig(max_depth=1, max_candidates=10),
    )

    candidates = adapter.expand([landing_page], query="legacy encoding")

    assert [(candidate.title, candidate.url) for candidate in candidates] == [
        ("Legacy PDF", "https://example.org/reports/legacy.pdf")
    ]
    assert candidates[0].metadata == {
        "source_candidate_url": "https://example.org/reports/legacy",
        "source_page_url": "https://example.org/reports/legacy",
        "anchor_text": "Legacy PDF",
        "depth": 1,
        "url_type": "pdf",
    }


@pytest.mark.revised
def test_frontier_stops_at_configured_depth_and_candidate_limits(
    recording_http_client_factory,
    html_response_factory,
    sitemap_candidate_factory,
) -> None:
    landing_page = sitemap_candidate_factory(
        title="Root Landing Page",
        url="https://example.org/root",
        query="original query",
        score=None,
    )
    http_client = recording_http_client_factory.from_mapping(
        {
            "https://example.org/root": html_response_factory(
                url="https://example.org/root",
                content=b"""
                <html>
                  <body>
                    <a href="/first.pdf">First PDF</a>
                    <a href="/first-landing">First landing page</a>
                    <a href="/second.pdf">Second PDF</a>
                  </body>
                </html>
                """,
            ),
            "https://example.org/first-landing": html_response_factory(
                url="https://example.org/first-landing",
                content=b'<a href="/too-deep.pdf">Too deep PDF</a>',
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
