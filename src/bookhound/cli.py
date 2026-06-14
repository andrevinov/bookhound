from typing import Annotated
import json

import typer

from bookhound import __version__
from bookhound.discovery_pipeline import DiscoveryPipeline
from bookhound.models import RawCandidate


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


def main() -> None:
    app()
