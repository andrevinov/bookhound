from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
import json
from urllib.parse import urlsplit, urlunsplit

import typer

from bookhound import __version__
from bookhound.arxiv import ArxivAdapter, ArxivAdapterConfig
from bookhound.common_crawl import CommonCrawlAdapter, CommonCrawlAdapterConfig
from bookhound.config import AppSettings, load_settings
from bookhound.database import initialize_database
from bookhound.discovery_pipeline import DiscoveryPipeline, DiscoveryPipelineResult
from bookhound.downloader import DownloadService, DownloadServiceConfig, DownloadPrompt
from bookhound.google_search import GoogleSearchAdapter, GoogleSearchAdapterConfig
from bookhound.http_client import BookhoundHttpClient, HttpClientConfig
from bookhound.license_classifier import LicenseClassifier
from bookhound.models import (
    Document,
    DocumentUrl,
    DownloadRecord,
    DownloadStatus,
    ExecutionMode,
    LicenseDecision,
    LicenseStatus,
    RawCandidate,
    SearchQuery,
    SourceKind,
    UrlType,
)
from bookhound.repositories import RepositorySet
from bookhound.seed_crawler import SeedCrawlerAdapter, SeedCrawlerConfig
from bookhound.sitemap import SitemapAdapter, SitemapAdapterConfig
from bookhound.sources import SourceAdapter
from bookhound.url_normalization import canonicalize_url, is_direct_pdf_url


_runtime_config_path: Path | None = None


app = typer.Typer(
    name="bookhound",
    help="Discover, catalog, and selectively download PDFs by keyword.",
    no_args_is_help=True,
)


def version_callback(show_version: bool) -> None:
    if show_version:
        typer.echo(f"bookhound {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to a Bookhound TOML configuration file.",
            dir_okay=False,
            resolve_path=True,
        ),
    ] = None,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=version_callback,
            is_eager=True,
            help="Show the application version and exit.",
        ),
    ] = False,
) -> None:
    global _runtime_config_path
    _runtime_config_path = config


@app.command()
def search(
    keyword: Annotated[str, typer.Argument(help="Keyword to search for.")],
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output search results as JSON.",
        ),
    ] = False,
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            min=1,
            help="Maximum number of results to show.",
        ),
    ] = 20,
) -> None:
    pipeline = build_search_pipeline()
    result = pipeline.search(keyword)
    candidates = result.candidates[:limit]

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "keyword": result.query_plan.keyword,
                    "results": [
                        _candidate_output(candidate)
                        for candidate in candidates
                    ],
                }
            )
        )
        return

    _echo_search_table(candidates)


@app.command()
def collect(
    keyword: Annotated[str, typer.Argument(help="Keyword to collect PDFs for.")],
) -> None:
    pipeline = build_search_pipeline()
    result = pipeline.search(keyword)
    settings = load_runtime_settings()
    repositories = RepositorySet(initialize_database(settings.database_path))

    try:
        summary = _save_collect_result(repositories, result)
    finally:
        repositories.close()

    typer.echo(
        "Collected "
        f"{summary.total} {_candidate_count_label(summary.total)}: "
        f"new: {summary.new}, "
        f"updated: {summary.updated}, "
        f"duplicate: {summary.duplicate}"
    )


@app.command()
def download(
    keyword: Annotated[str, typer.Argument(help="Keyword to download PDFs for.")],
    collected_only: Annotated[
        bool,
        typer.Option(
            "--collected-only",
            help="Download only documents already collected in the database.",
        ),
    ] = False,
) -> None:
    settings = load_runtime_settings()
    repositories = RepositorySet(initialize_database(settings.database_path))
    prompt = TyperDownloadPrompt()

    try:
        candidates = _download_candidates(keyword, collected_only, repositories)
        classifier = build_license_classifier()
        service = build_download_service(repositories, settings, prompt)
        summary = _download_candidates_with_license_gate(
            candidates,
            classifier=classifier,
            service=service,
            prompt=prompt,
        )
    finally:
        repositories.close()

    typer.echo(
        "Download summary: "
        f"downloaded: {summary.downloaded}, "
        f"blocked: {summary.blocked}, "
        f"pending: {summary.pending}, "
        f"failed: {summary.failed}"
    )


def build_search_pipeline(settings: AppSettings | None = None) -> DiscoveryPipeline:
    settings = settings or load_runtime_settings()
    http_client = build_http_client(settings)
    return DiscoveryPipeline(sources=_build_search_sources(settings, http_client))


def load_runtime_settings():
    return load_settings(config_path=_runtime_config_path)


def build_http_client(settings) -> BookhoundHttpClient:
    return BookhoundHttpClient(
        HttpClientConfig(
            user_agent=settings.user_agent,
            timeout_seconds=settings.request_timeout_seconds,
            rate_limit_per_second=settings.per_domain_rate_limit_per_second,
        )
    )


def _build_search_sources(
    settings: AppSettings,
    http_client: BookhoundHttpClient,
) -> list[SourceAdapter]:
    sources: list[SourceAdapter] = [
        ArxivAdapter(
            http_client=http_client,
            config=ArxivAdapterConfig(
                request_timeout_seconds=settings.request_timeout_seconds,
                user_agent=settings.user_agent,
            ),
        ),
    ]

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

    sitemap_domain_roots = _sitemap_domain_roots(settings)
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


def _sitemap_domain_roots(settings: AppSettings) -> list[str]:
    return _domain_roots_from_urls(settings.sources.seed_crawler.seed_urls)


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


def _secret_value(secret) -> str | None:
    if secret is None:
        return None
    return secret.get_secret_value()


def build_license_classifier() -> LicenseClassifier:
    return LicenseClassifier()


def build_download_service(
    repositories: RepositorySet,
    settings,
    prompt: DownloadPrompt,
) -> DownloadService:
    return DownloadService(
        repositories=repositories,
        config=DownloadServiceConfig(download_directory=settings.pdf_directory),
        prompt=prompt,
    )


def _candidate_output(candidate: RawCandidate) -> dict[str, object]:
    return {
        "title": candidate.title,
        "url": candidate.url,
        "source": candidate.source.value,
        "score": candidate.score,
        "preliminary_status": "unknown",
    }


def _echo_search_table(candidates: list[RawCandidate]) -> None:
    typer.echo("Title\tURL\tSource\tScore\tPreliminary status")
    for candidate in candidates:
        output = _candidate_output(candidate)
        typer.echo(
            "\t".join(
                [
                    str(output["title"]),
                    str(output["url"]),
                    str(output["source"]),
                    _format_score(output["score"]),
                    str(output["preliminary_status"]),
                ]
            )
        )


def _format_score(score: object) -> str:
    if isinstance(score, float):
        return f"{score:.2f}"
    if score is None:
        return ""
    return str(score)


@dataclass(frozen=True)
class CollectSummary:
    total: int
    new: int
    updated: int
    duplicate: int


@dataclass(frozen=True)
class DownloadSummary:
    downloaded: int = 0
    blocked: int = 0
    pending: int = 0
    failed: int = 0


class TyperDownloadPrompt:
    def confirm_unknown_license(self, decision: LicenseDecision) -> bool:
        return typer.confirm(
            f"License is unknown ({decision.reason}). Download anyway?",
            default=False,
        )


def _save_collect_result(
    repositories: RepositorySet,
    result: DiscoveryPipelineResult,
) -> CollectSummary:
    variants = [variant.query for variant in result.query_plan.variants]
    summary = CollectSummary(
        total=len(result.candidates),
        new=0,
        updated=0,
        duplicate=0,
    )

    try:
        repositories.connection.execute("BEGIN")
        query_id = repositories.queries.create(
            SearchQuery(
                keyword=result.query_plan.keyword,
                mode=ExecutionMode.COLLECT,
                variants=variants,
            ),
            parameters={},
            commit=False,
        )

        for candidate in result.candidates:
            summary = _save_candidate(repositories, candidate, summary)

        repositories.events.add(
            event_type="collect.completed",
            entity_type="query",
            entity_id=query_id,
            message=(
                f"Collected {summary.total} "
                f"{_candidate_count_label(summary.total)} "
                f"for {result.query_plan.keyword}."
            ),
            metadata={
                "keyword": result.query_plan.keyword,
                "new": summary.new,
                "updated": summary.updated,
                "duplicate": summary.duplicate,
                "errors": list(result.errors),
            },
            commit=False,
        )
    except Exception:
        repositories.connection.rollback()
        raise

    repositories.connection.commit()
    return summary


def _download_candidates(
    keyword: str,
    collected_only: bool,
    repositories: RepositorySet,
) -> list[RawCandidate]:
    if collected_only:
        return _collected_candidates(repositories)

    pipeline = build_search_pipeline()
    return pipeline.search(keyword).candidates


def _download_candidates_with_license_gate(
    candidates: list[RawCandidate],
    *,
    classifier,
    service,
    prompt: TyperDownloadPrompt,
) -> DownloadSummary:
    summary = DownloadSummary()
    for candidate in candidates:
        decision = classifier.classify(document_url=candidate.url, evidence=[])
        if decision.status is LicenseStatus.DENIED:
            summary = _increment_download_summary(summary, blocked=1)
            continue
        if decision.status is LicenseStatus.UNKNOWN:
            if not prompt.confirm_unknown_license(decision):
                summary = _increment_download_summary(summary, pending=1)
                continue
            interactive = True
        else:
            interactive = False

        try:
            record = service.download(
                document_id=int(candidate.metadata.get("document_id", 0)),
                document_url_id=int(candidate.metadata.get("document_url_id", 0)),
                url=candidate.url,
                license_decision=decision,
                interactive=interactive,
            )
        except Exception:
            summary = _increment_download_summary(summary, failed=1)
            continue

        summary = _summary_for_download_record(summary, record)

    return summary


def _collected_candidates(repositories: RepositorySet) -> list[RawCandidate]:
    rows = repositories.connection.execute(
        """
        SELECT
            documents.id,
            document_urls.id,
            documents.title,
            document_urls.url,
            document_urls.discovery_method,
            document_urls.confidence
        FROM document_urls
        JOIN documents ON documents.id = document_urls.document_id
        ORDER BY document_urls.discovered_at, document_urls.id
        """
    ).fetchall()
    return [
        RawCandidate(
            title=str(row[2]),
            url=str(row[3]),
            source=SourceKind.SITEMAP,
            discovery_method=_discovery_method_or_default(str(row[4])),
            query="collected",
            score=row[5],
            metadata={
                "document_id": int(row[0]),
                "document_url_id": int(row[1]),
            },
        )
        for row in rows
    ]


def _summary_for_download_record(
    summary: DownloadSummary,
    record: DownloadRecord,
) -> DownloadSummary:
    if record.status is DownloadStatus.DOWNLOADED:
        return _increment_download_summary(summary, downloaded=1)
    if record.status is DownloadStatus.BLOCKED:
        return _increment_download_summary(summary, blocked=1)
    if record.status is DownloadStatus.FAILED:
        return _increment_download_summary(summary, failed=1)
    return _increment_download_summary(summary, pending=1)


def _increment_download_summary(
    summary: DownloadSummary,
    *,
    downloaded: int = 0,
    blocked: int = 0,
    pending: int = 0,
    failed: int = 0,
) -> DownloadSummary:
    return DownloadSummary(
        downloaded=summary.downloaded + downloaded,
        blocked=summary.blocked + blocked,
        pending=summary.pending + pending,
        failed=summary.failed + failed,
    )


def _discovery_method_or_default(value: str):
    from bookhound.models import DiscoveryMethod

    try:
        return DiscoveryMethod(value)
    except ValueError:
        return DiscoveryMethod.SITEMAP


def _save_candidate(
    repositories: RepositorySet,
    candidate: RawCandidate,
    summary: CollectSummary,
) -> CollectSummary:
    document = _candidate_document(candidate)
    document_url = _candidate_document_url(candidate)
    existing_document_id = _existing_document_id(repositories, document)
    existing_document_url_id = _existing_document_url_id(
        repositories,
        document_url.canonical_url,
    )

    source_id = repositories.sources.upsert(candidate.source, commit=False)

    if existing_document_url_id is not None:
        return _increment_summary(summary, duplicate=1)

    document_id = repositories.documents.upsert(document, commit=False)
    repositories.document_urls.upsert(
        document_id=document_id,
        source_id=source_id,
        document_url=document_url,
        metadata={
            "query": candidate.query,
            "score": candidate.score,
            "snippet": candidate.snippet,
        },
        commit=False,
    )

    if existing_document_id is None:
        return _increment_summary(summary, new=1)

    return _increment_summary(summary, updated=1)


def _candidate_document(candidate: RawCandidate) -> Document:
    metadata = dict(candidate.metadata)
    return Document(
        title=candidate.title,
        authors=_metadata_string_list(metadata.get("authors")),
        doi=_metadata_optional_string(metadata.get("doi")),
        isbn=_metadata_optional_string(metadata.get("isbn")),
        year=_metadata_optional_int(metadata.get("year")),
        language=_metadata_optional_string(metadata.get("language")),
        metadata=metadata,
    )


def _candidate_document_url(candidate: RawCandidate) -> DocumentUrl:
    canonical_url = candidate.metadata.get("canonical_url")
    if not isinstance(canonical_url, str) or not canonical_url.strip():
        canonical_url = canonicalize_url(candidate.url)

    return DocumentUrl(
        url=candidate.url,
        canonical_url=canonical_url,
        source=candidate.source,
        discovery_method=candidate.discovery_method,
        url_type=UrlType.PDF if is_direct_pdf_url(candidate.url) else UrlType.LANDING_PAGE,
        confidence=candidate.score,
        discovered_at=candidate.discovered_at,
    )


def _existing_document_id(
    repositories: RepositorySet,
    document: Document,
) -> int | None:
    if document.doi:
        row = repositories.connection.execute(
            "SELECT id FROM documents WHERE doi = ?",
            (document.doi,),
        ).fetchone()
        if row is not None:
            return int(row[0])

    if document.isbn:
        row = repositories.connection.execute(
            "SELECT id FROM documents WHERE isbn = ?",
            (document.isbn,),
        ).fetchone()
        if row is not None:
            return int(row[0])

    return None


def _existing_document_url_id(
    repositories: RepositorySet,
    canonical_url: str,
) -> int | None:
    row = repositories.connection.execute(
        "SELECT id FROM document_urls WHERE canonical_url = ?",
        (canonical_url,),
    ).fetchone()
    if row is None:
        return None
    return int(row[0])


def _metadata_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    return [item for item in value if isinstance(item, str) and item.strip()]


def _metadata_optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _metadata_optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _increment_summary(
    summary: CollectSummary,
    *,
    new: int = 0,
    updated: int = 0,
    duplicate: int = 0,
) -> CollectSummary:
    return CollectSummary(
        total=summary.total,
        new=summary.new + new,
        updated=summary.updated + updated,
        duplicate=summary.duplicate + duplicate,
    )


def _candidate_count_label(count: int) -> str:
    if count == 1:
        return "candidate"
    return "candidates"


def main() -> None:
    app()
