from dataclasses import dataclass
from typing import Annotated
import json

import typer

from bookhound import __version__
from bookhound.config import load_settings
from bookhound.database import initialize_database
from bookhound.discovery_pipeline import DiscoveryPipeline, DiscoveryPipelineResult
from bookhound.models import (
    Document,
    DocumentUrl,
    ExecutionMode,
    RawCandidate,
    SearchQuery,
    UrlType,
)
from bookhound.repositories import RepositorySet
from bookhound.url_normalization import canonicalize_url, is_direct_pdf_url


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
    pass


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
    settings = load_settings()
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


def build_search_pipeline() -> DiscoveryPipeline:
    return DiscoveryPipeline(sources=[])


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
