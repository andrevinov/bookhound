from pathlib import Path
from typing import Annotated
import json

import typer

from bookhound import __version__
from bookhound import app_factory
from bookhound.collect_service import CollectService, CollectSummary
from bookhound.config import AppSettings, load_settings
from bookhound.database import initialize_database
from bookhound.discovery_pipeline import DiscoveryPipeline, DiscoveryPipelineResult
from bookhound.download_workflow import (
    DownloadFailure,
    DownloadSummary,
    DownloadWorkflowService,
)
from bookhound.downloader import DownloadService, DownloadPrompt
from bookhound.daemon import DaemonRunner
from bookhound.export import ExportService
from bookhound.http_client import BookhoundHttpClient, HttpClientProtocol
from bookhound.jobs import CrawlJobRepository
from bookhound.license_classifier import LicenseClassifier
from bookhound.models import (
    LicenseDecision,
    PersistedDownloadCandidate,
    RawCandidate,
)
from bookhound.repositories import RepositorySet
from bookhound.sources import SourceAdapter
from bookhound.url_normalization import canonicalize_url


_runtime_config_path: Path | None = None


app = typer.Typer(
    name="bookhound",
    help="Discover, catalog, and selectively download PDFs by keyword.",
    no_args_is_help=True,
)
job_app = typer.Typer(help="Manage queued crawl jobs.", no_args_is_help=True)
daemon_app = typer.Typer(help="Run non-interactive background work.", no_args_is_help=True)
app.add_typer(job_app, name="job")
app.add_typer(daemon_app, name="daemon")


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
        candidates, preparation_failed = _download_candidates(
            keyword,
            collected_only,
            repositories,
        )
        classifier = build_license_classifier()
        service = build_download_service(repositories, settings, prompt)
        summary = _download_candidates_with_license_gate(
            candidates,
            classifier=classifier,
            service=service,
            prompt=prompt,
        )
        summary = _add_preparation_failures(summary, preparation_failed)
    finally:
        repositories.close()

    typer.echo(
        "Download summary: "
        f"downloaded: {summary.downloaded}, "
        f"blocked: {summary.blocked}, "
        f"pending: {summary.pending}, "
        f"failed: {summary.failed}"
    )
    _print_download_failures(summary.failures)


@job_app.command("add")
def add_job(
    keyword: Annotated[str, typer.Argument(help="Keyword to collect PDFs for.")],
    priority: Annotated[
        int,
        typer.Option(
            "--priority",
            help="Job priority. Higher values run first.",
        ),
    ] = 0,
) -> None:
    settings = load_runtime_settings()
    repositories = RepositorySet(initialize_database(settings.database_path))
    try:
        job_id = CrawlJobRepository(repositories.connection).create(
            keyword,
            priority=priority,
        )
    finally:
        repositories.close()

    typer.echo(f"Created job {job_id} for {keyword}.")


@daemon_app.command("run-once")
def daemon_run_once() -> None:
    settings = load_runtime_settings()
    repositories = RepositorySet(initialize_database(settings.database_path))
    try:
        runner = build_daemon_runner(repositories, settings)
        result = runner.run_once()
    finally:
        repositories.close()

    if result.locked:
        typer.echo("Daemon run skipped: lock held.")
        return

    typer.echo("Daemon run completed.")
    if result.job_id is not None:
        typer.echo(f"job: {result.job_id}")


@app.command("export")
def export_command(
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Path to write the export file.",
            dir_okay=False,
            resolve_path=True,
        ),
    ],
    export_format: Annotated[
        str,
        typer.Option(
            "--format",
            help="Export format: jsonl or csv.",
        ),
    ] = "jsonl",
) -> None:
    settings = load_runtime_settings()
    repositories = RepositorySet(initialize_database(settings.database_path))
    try:
        exporter = ExportService(repositories.connection)
        row_count = _export_row_count(repositories)
        normalized_format = export_format.strip().lower()
        if normalized_format == "jsonl":
            exporter.export_jsonl(output)
        elif normalized_format == "csv":
            exporter.export_csv(output)
        else:
            raise typer.BadParameter("Export format must be jsonl or csv.")
    finally:
        repositories.close()

    typer.echo(f"Exported {row_count} {_row_count_label(row_count)} to {output}.")


def build_search_pipeline(settings: AppSettings | None = None) -> DiscoveryPipeline:
    settings = settings or load_runtime_settings()
    http_client = build_http_client(settings)
    return app_factory.build_search_pipeline(
        settings,
        http_client=http_client,
    )


def load_runtime_settings():
    try:
        return load_settings(config_path=_runtime_config_path)
    except FileNotFoundError as error:
        typer.echo(f"Error: {error}")
        raise typer.Exit(1) from error


def build_http_client(settings) -> BookhoundHttpClient:
    return app_factory.build_http_client(settings)


def build_daemon_runner(
    repositories: RepositorySet,
    settings: AppSettings,
) -> DaemonRunner:
    return app_factory.build_daemon_runner(
        repositories,
        settings,
        search_pipeline_builder=build_search_pipeline,
        collect_result_saver=_save_collect_result,
    )


def _build_search_sources(
    settings: AppSettings,
    http_client: HttpClientProtocol,
) -> list[SourceAdapter]:
    return app_factory.build_search_sources(settings, http_client)


def _build_link_expander(
    settings: AppSettings,
    http_client: HttpClientProtocol,
):
    return app_factory.build_link_expander(settings, http_client)


def _sitemap_domain_roots(settings: AppSettings) -> list[str]:
    return app_factory.sitemap_domain_roots_from_settings(settings)


def _domain_roots_from_urls(urls: list[str]) -> list[str]:
    return app_factory._domain_roots_from_urls(urls)


def _deduplicate_urls(urls: list[str]) -> list[str]:
    return app_factory._deduplicate_urls(urls)


def _secret_value(secret) -> str | None:
    return app_factory._secret_value(secret)


def build_license_classifier() -> LicenseClassifier:
    return app_factory.build_license_classifier()


def build_download_service(
    repositories: RepositorySet,
    settings,
    prompt: DownloadPrompt,
) -> DownloadService:
    return app_factory.build_download_service(repositories, settings, prompt)


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


def _export_row_count(repositories: RepositorySet) -> int:
    return repositories.document_urls.count_export_rows()


def _row_count_label(count: int) -> str:
    if count == 1:
        return "row"
    return "rows"


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
    return CollectService(repositories).save_result(result)


def _print_download_failures(failures: list[DownloadFailure]) -> None:
    if not failures:
        return

    typer.echo("Download failures:")
    for failure in failures:
        typer.echo(
            f"- {failure.title}: {failure.url} "
            f"({failure.error})"
        )


def _download_candidates(
    keyword: str,
    collected_only: bool,
    repositories: RepositorySet,
) -> tuple[list[PersistedDownloadCandidate], int]:
    if collected_only:
        return _collected_candidates(repositories), 0

    pipeline = build_search_pipeline()
    return _persist_discovered_download_candidates(
        repositories,
        pipeline.search(keyword),
    )


def _persist_discovered_download_candidates(
    repositories: RepositorySet,
    result: DiscoveryPipelineResult,
) -> tuple[list[PersistedDownloadCandidate], int]:
    valid_candidates: list[RawCandidate] = []
    canonical_urls: list[str] = []
    failed = 0

    for candidate in result.candidates:
        try:
            canonical_url = _candidate_canonical_url(candidate)
        except ValueError:
            failed += 1
            continue

        valid_candidates.append(candidate)
        canonical_urls.append(canonical_url)

    if not valid_candidates:
        return [], failed

    try:
        _save_collect_result(
            repositories,
            result.__class__(
                query_plan=result.query_plan,
                candidates=valid_candidates,
                errors=result.errors,
            ),
        )
    except Exception:
        return [], failed + len(valid_candidates)

    prepared_candidates: list[PersistedDownloadCandidate] = []
    for candidate, canonical_url in zip(valid_candidates, canonical_urls):
        prepared_candidate = _persisted_download_candidate(
            repositories,
            candidate=candidate,
            canonical_url=canonical_url,
        )
        if prepared_candidate is None:
            failed += 1
            continue
        prepared_candidates.append(prepared_candidate)

    return prepared_candidates, failed


def _candidate_canonical_url(candidate: RawCandidate) -> str:
    metadata_canonical_url = candidate.metadata.get("canonical_url")
    if isinstance(metadata_canonical_url, str) and metadata_canonical_url.strip():
        return metadata_canonical_url
    return canonicalize_url(candidate.url)


def _persisted_download_candidate(
    repositories: RepositorySet,
    *,
    candidate: RawCandidate,
    canonical_url: str,
) -> PersistedDownloadCandidate | None:
    return repositories.document_urls.find_persisted_download_candidate(
        canonical_url=canonical_url,
        candidate=candidate,
        license_evidence=repositories.license_evidence,
    )


def _add_preparation_failures(
    summary: DownloadSummary,
    preparation_failed: int,
) -> DownloadSummary:
    if preparation_failed == 0:
        return summary
    return DownloadSummary(
        downloaded=summary.downloaded,
        blocked=summary.blocked,
        pending=summary.pending,
        failed=summary.failed + preparation_failed,
        failures=list(summary.failures),
    )


def _download_candidates_with_license_gate(
    candidates: list[PersistedDownloadCandidate],
    *,
    classifier,
    service,
    prompt: TyperDownloadPrompt,
) -> DownloadSummary:
    return DownloadWorkflowService(
        classifier=classifier,
        service=service,
        prompt=prompt,
    ).run(candidates)


def _collected_candidates(
    repositories: RepositorySet,
) -> list[PersistedDownloadCandidate]:
    return repositories.document_urls.list_persisted_download_candidates(
        repositories.license_evidence
    )


def _license_evidence_for_document_url(
    repositories: RepositorySet,
    *,
    document_id: int,
    document_url_id: int,
) -> list[dict[str, object]]:
    return repositories.license_evidence.list_for_document_url(
        document_id=document_id,
        document_url_id=document_url_id,
    )


def _candidate_count_label(count: int) -> str:
    if count == 1:
        return "candidate"
    return "candidates"


def main() -> None:
    app()
