from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

from bookhound.arxiv import ArxivAdapter, ArxivAdapterConfig
from bookhound.collect_service import CollectSummary, CollectService
from bookhound.common_crawl import CommonCrawlAdapter, CommonCrawlAdapterConfig
from bookhound.config import AppSettings
from bookhound.discovery_pipeline import DiscoveryPipeline, DiscoveryPipelineResult
from bookhound.downloader import DownloadPrompt, DownloadService, DownloadServiceConfig
from bookhound.google_search import GoogleSearchAdapter, GoogleSearchAdapterConfig
from bookhound.http_client import (
    BookhoundHttpClient,
    HttpClientConfig,
    HttpClientProtocol,
)
from bookhound.daemon import DaemonConfig, DaemonRunner
from bookhound.license_classifier import LicenseClassifier
from bookhound.link_expansion import LinkExpansionAdapter, LinkExpansionConfig
from bookhound.repositories import RepositorySet
from bookhound.seed_crawler import (
    RuntimeRobotsPolicy,
    SeedCrawlerAdapter,
    SeedCrawlerConfig,
)
from bookhound.sitemap import SitemapAdapter, SitemapAdapterConfig
from bookhound.sources import SourceAdapter


SearchPipelineBuilder = Callable[[AppSettings], DiscoveryPipeline]
CollectResultSaver = Callable[
    [RepositorySet, DiscoveryPipelineResult],
    CollectSummary,
]


def build_search_pipeline(
    settings: AppSettings,
    *,
    http_client: HttpClientProtocol | None = None,
) -> DiscoveryPipeline:
    runtime_http_client = http_client or build_http_client(settings)
    return DiscoveryPipeline(
        sources=build_search_sources(settings, runtime_http_client),
        link_expander=build_link_expander(settings, runtime_http_client),
    )


def build_http_client(settings: AppSettings) -> BookhoundHttpClient:
    return BookhoundHttpClient(
        HttpClientConfig(
            user_agent=settings.user_agent,
            timeout_seconds=settings.request_timeout_seconds,
            rate_limit_per_second=settings.per_domain_rate_limit_per_second,
        )
    )


class CollectJobExecutor:
    def __init__(
        self,
        repositories: RepositorySet,
        settings: AppSettings,
        *,
        search_pipeline_builder: SearchPipelineBuilder = build_search_pipeline,
        collect_result_saver: CollectResultSaver | None = None,
    ) -> None:
        self.repositories = repositories
        self.settings = settings
        self.search_pipeline_builder = search_pipeline_builder
        self.collect_result_saver = collect_result_saver or _save_collect_result

    def execute_job(self, keyword: str) -> None:
        result = self.search_pipeline_builder(self.settings).search(keyword)
        self.collect_result_saver(self.repositories, result)


def build_daemon_runner(
    repositories: RepositorySet,
    settings: AppSettings,
    *,
    search_pipeline_builder: SearchPipelineBuilder = build_search_pipeline,
    collect_result_saver: CollectResultSaver | None = None,
) -> DaemonRunner:
    return DaemonRunner(
        connection=repositories.connection,
        config=DaemonConfig(lock_path=settings.database_path.with_suffix(".lock")),
        executor=CollectJobExecutor(
            repositories,
            settings,
            search_pipeline_builder=search_pipeline_builder,
            collect_result_saver=collect_result_saver,
        ),
    )


def build_search_sources(
    settings: AppSettings,
    http_client: HttpClientProtocol,
) -> list[SourceAdapter]:
    sources: list[SourceAdapter] = []

    if settings.sources.arxiv.enabled:
        sources.append(
            ArxivAdapter(
                http_client=http_client,
                config=ArxivAdapterConfig(
                    max_results=settings.sources.arxiv.max_results,
                    page_size=settings.sources.arxiv.page_size,
                    request_timeout_seconds=settings.request_timeout_seconds,
                    user_agent=settings.user_agent,
                ),
            )
        )

    if settings.sources.common_crawl.enabled:
        sources.append(
            CommonCrawlAdapter(
                http_client=http_client,
                config=CommonCrawlAdapterConfig(
                    crawl_indexes=settings.sources.common_crawl.crawl_indexes,
                    result_limit=settings.sources.common_crawl.result_limit,
                    request_timeout_seconds=settings.request_timeout_seconds,
                    user_agent=settings.user_agent,
                ),
            )
        )

    if (
        settings.sources.seed_crawler.enabled
        and settings.sources.seed_crawler.seed_urls
    ):
        sources.append(
            SeedCrawlerAdapter(
                http_client=http_client,
                robots_policy=RuntimeRobotsPolicy(
                    http_client=http_client,
                    user_agent=settings.user_agent,
                ),
                config=SeedCrawlerConfig(
                    seed_urls=settings.sources.seed_crawler.seed_urls,
                    allowed_domains=settings.sources.seed_crawler.allowed_domains,
                    same_domain_only=settings.sources.seed_crawler.same_domain_only,
                    max_depth=settings.sources.seed_crawler.max_depth,
                    max_pages_per_seed=settings.sources.seed_crawler.max_pages_per_seed,
                    request_timeout_seconds=settings.request_timeout_seconds,
                    user_agent=settings.user_agent,
                ),
            )
        )

    sitemap_domain_roots = sitemap_domain_roots_from_settings(settings)
    if settings.sources.sitemap.enabled and sitemap_domain_roots:
        sources.append(
            SitemapAdapter(
                http_client=http_client,
                config=SitemapAdapterConfig(
                    domain_roots=sitemap_domain_roots,
                    request_timeout_seconds=settings.request_timeout_seconds,
                    user_agent=settings.user_agent,
                ),
            )
        )

    if settings.sources.google.enabled:
        sources.append(
            GoogleSearchAdapter(
                http_client=http_client,
                config=GoogleSearchAdapterConfig(
                    api_key=_secret_value(settings.sources.google.api_key),
                    search_engine_id=_secret_value(
                        settings.sources.google.search_engine_id
                    ),
                    request_timeout_seconds=settings.request_timeout_seconds,
                    user_agent=settings.user_agent,
                ),
            )
        )

    return sources


def build_link_expander(
    settings: AppSettings,
    http_client: HttpClientProtocol,
) -> LinkExpansionAdapter | None:
    if not settings.sources.link_expansion.enabled:
        return None

    return LinkExpansionAdapter(
        http_client=http_client,
        config=LinkExpansionConfig(
            allowed_domains=settings.sources.seed_crawler.allowed_domains,
            same_domain_only=settings.sources.link_expansion.same_domain_only,
            max_depth=settings.sources.link_expansion.max_depth,
            max_candidates=settings.sources.link_expansion.max_candidates,
            request_timeout_seconds=settings.request_timeout_seconds,
            user_agent=settings.user_agent,
        ),
    )


def sitemap_domain_roots_from_settings(settings: AppSettings) -> list[str]:
    return _deduplicate_urls(
        [
            *settings.sources.sitemap.domain_roots,
            *_domain_roots_from_urls(settings.sources.seed_crawler.seed_urls),
        ]
    )


def build_license_classifier() -> LicenseClassifier:
    return LicenseClassifier()


def build_download_service(
    repositories: RepositorySet,
    settings: AppSettings,
    prompt: DownloadPrompt,
) -> DownloadService:
    return DownloadService(
        repositories=repositories,
        config=DownloadServiceConfig(download_directory=settings.pdf_directory),
        prompt=prompt,
    )


def _save_collect_result(
    repositories: RepositorySet,
    result: DiscoveryPipelineResult,
) -> CollectSummary:
    return CollectService(repositories).save_result(result)


def _domain_roots_from_urls(urls: list[str]) -> list[str]:
    roots: list[str] = []
    seen: set[str] = set()
    for url in urls:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue

        root = urlunsplit((parsed.scheme, parsed.netloc.lower(), "/", "", ""))
        if root in seen:
            continue
        roots.append(root)
        seen.add(root)

    return roots


def _deduplicate_urls(urls: list[str]) -> list[str]:
    deduplicated_urls: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        deduplicated_urls.append(url)
        seen.add(url)
    return deduplicated_urls


def _secret_value(secret) -> str | None:
    if secret is None:
        return None
    return secret.get_secret_value()
