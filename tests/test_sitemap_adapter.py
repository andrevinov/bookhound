import pytest

from bookhound.http_client import HttpResponse
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
def test_robots_txt_fixture_points_to_sitemap_urls() -> None:
    http_client = FakeHttpClient(
        {
            "https://example.org/robots.txt": _response(
                "https://example.org/robots.txt",
                ROBOTS_TXT,
            ),
            "https://example.org/sitemap-index.xml": _response(
                "https://example.org/sitemap-index.xml",
                SITEMAP_INDEX,
            ),
            "https://example.org/secondary-sitemap.xml": _response(
                "https://example.org/secondary-sitemap.xml",
                b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" />',
            ),
            "https://example.org/reports-sitemap.xml": _response(
                "https://example.org/reports-sitemap.xml",
                URL_SET,
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
def test_sitemap_index_fixture_expands_to_child_sitemaps() -> None:
    http_client = FakeHttpClient(
        {
            "https://example.org/robots.txt": _response(
                "https://example.org/robots.txt",
                ROBOTS_TXT,
            ),
            "https://example.org/sitemap-index.xml": _response(
                "https://example.org/sitemap-index.xml",
                SITEMAP_INDEX,
            ),
            "https://example.org/secondary-sitemap.xml": _response(
                "https://example.org/secondary-sitemap.xml",
                b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" />',
            ),
            "https://example.org/reports-sitemap.xml": _response(
                "https://example.org/reports-sitemap.xml",
                URL_SET,
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
def test_url_set_fixture_yields_pdf_candidates() -> None:
    adapter = SitemapAdapter(
        http_client=FakeHttpClient(
            {
                "https://example.org/robots.txt": _response(
                    "https://example.org/robots.txt",
                    b"Sitemap: https://example.org/reports-sitemap.xml",
                ),
                "https://example.org/reports-sitemap.xml": _response(
                    "https://example.org/reports-sitemap.xml",
                    URL_SET,
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
def test_malformed_sitemap_entries_are_ignored_with_error_event() -> None:
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
        http_client=FakeHttpClient(
            {
                "https://example.org/robots.txt": _response(
                    "https://example.org/robots.txt",
                    b"Sitemap: https://example.org/reports-sitemap.xml",
                ),
                "https://example.org/reports-sitemap.xml": _response(
                    "https://example.org/reports-sitemap.xml",
                    malformed_url_set,
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


def _response(url: str, content: bytes, *, status_code: int = 200) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        headers={"content-type": "application/xml"},
        content=content,
        url=url,
    )
