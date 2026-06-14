import pytest

import json

from typer.testing import CliRunner

import bookhound.cli as cli
from bookhound.discovery_pipeline import DiscoveryPipelineResult
from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind
from bookhound.query_planner import PlannedQueryVariant, QueryPlan


class FakePipeline:
    def __init__(self, candidates: list[RawCandidate]) -> None:
        self.candidates = candidates
        self.searched_keywords: list[str] = []

    def search(self, keyword: str) -> DiscoveryPipelineResult:
        self.searched_keywords.append(keyword)
        return DiscoveryPipelineResult(
            query_plan=QueryPlan(
                keyword=keyword,
                variants=[PlannedQueryVariant(label="quoted", query=f'"{keyword}"')],
            ),
            candidates=self.candidates,
            errors=[],
        )

@pytest.mark.revised
def test_search_command_calls_pipeline(monkeypatch) -> None:
    pipeline = FakePipeline([_candidate(title="Climate Report")])
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    result = CliRunner().invoke(cli.app, ["search", "climate policy"])

    assert result.exit_code == 0
    assert pipeline.searched_keywords == ["climate policy"]


@pytest.mark.revised
def test_search_table_output_contains_main_fields(monkeypatch) -> None:
    pipeline = FakePipeline(
        [
            _candidate(
                title="Machine Learning Notes",
                url="https://example.org/notes.pdf",
                source=SourceKind.COMMON_CRAWL,
                score=0.82,
            )
        ]
    )
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    result = CliRunner().invoke(cli.app, ["search", "machine learning"])

    assert result.exit_code == 0
    assert "Machine Learning Notes" in result.stdout
    assert "https://example.org/notes.pdf" in result.stdout
    assert "common_crawl" in result.stdout
    assert "0.82" in result.stdout
    assert "unknown" in result.stdout


@pytest.mark.revised
def test_search_json_returns_parseable_json(monkeypatch) -> None:
    pipeline = FakePipeline(
        [
            _candidate(
                title="Statistics Handbook",
                url="https://example.org/statistics.pdf",
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                score=0.91,
            )
        ]
    )
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    result = CliRunner().invoke(cli.app, ["search", "statistics", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["keyword"] == "statistics"
    assert payload["results"] == [
        {
            "title": "Statistics Handbook",
            "url": "https://example.org/statistics.pdf",
            "source": "sitemap",
            "score": 0.91,
            "preliminary_status": "unknown",
        }
    ]


@pytest.mark.revised
def test_search_result_limit_is_respected(monkeypatch) -> None:
    pipeline = FakePipeline(
        [
            _candidate(title="First Result", score=0.90),
            _candidate(title="Second Result", score=0.80),
        ]
    )
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    result = CliRunner().invoke(cli.app, ["search", "biology", "--limit", "1", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [candidate["title"] for candidate in payload["results"]] == ["First Result"]


def _candidate(
    *,
    title: str,
    url: str = "https://example.org/result.pdf",
    source: SourceKind = SourceKind.COMMON_CRAWL,
    discovery_method: DiscoveryMethod = DiscoveryMethod.PUBLIC_INDEX,
    score: float = 0.75,
) -> RawCandidate:
    return RawCandidate(
        title=title,
        url=url,
        source=source,
        discovery_method=discovery_method,
        query='"keyword"',
        score=score,
    )
