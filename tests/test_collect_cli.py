import json
import pytest
from pathlib import Path
import sqlite3

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
def test_collect_saves_candidates_to_sqlite(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    pipeline = FakePipeline(
        [
            _candidate(
                title="Open Climate Policy",
                url="https://example.org/climate.pdf",
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                metadata={
                    "doi": "10.1234/climate",
                    "authors": ["Ada Lovelace"],
                    "year": 2026,
                    "language": "en",
                },
            )
        ]
    )
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    result = CliRunner().invoke(
        cli.app,
        ["collect", "climate policy"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert result.exit_code == 0
    assert pipeline.searched_keywords == ["climate policy"]

    with sqlite3.connect(database_path) as connection:
        assert _table_count(connection, "queries") == 1
        assert _table_count(connection, "sources") == 1
        assert _table_count(connection, "documents") == 1
        assert _table_count(connection, "document_urls") == 1
        query = connection.execute(
            "SELECT keyword, mode, variants_json FROM queries"
        ).fetchone()
        document = connection.execute(
            "SELECT title, doi, authors_json, year, language FROM documents"
        ).fetchone()
        document_url = connection.execute(
            "SELECT url, canonical_url, url_type, discovery_method FROM document_urls"
        ).fetchone()
        source = connection.execute("SELECT name FROM sources").fetchone()

    assert query[0] == "climate policy"
    assert query[1] == "collect"
    assert json.loads(query[2]) == ['"climate policy"']
    assert document == (
        "Open Climate Policy",
        "10.1234/climate",
        '["Ada Lovelace"]',
        2026,
        "en",
    )
    assert document_url == (
        "https://example.org/climate.pdf",
        "https://example.org/climate.pdf",
        "pdf",
        "sitemap",
    )
    assert source[0] == "sitemap"
    assert "new: 1" in result.stdout


@pytest.mark.revised
def test_collect_does_not_download_pdfs(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    pipeline = FakePipeline([_candidate(title="No Download", url="https://example.org/file.pdf")])
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    result = CliRunner().invoke(
        cli.app,
        ["collect", "no download"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert result.exit_code == 0
    with sqlite3.connect(database_path) as connection:
        assert _table_count(connection, "downloads") == 0


@pytest.mark.revised
def test_collect_running_twice_does_not_duplicate_equivalent_documents(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    pipeline = FakePipeline(
        [
            _candidate(
                title="Duplicate Report",
                url="https://example.org/duplicate.pdf?utm_source=newsletter",
                metadata={"doi": "10.1234/duplicate"},
            )
        ]
    )
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    first_result = CliRunner().invoke(
        cli.app,
        ["collect", "duplicate"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )
    second_result = CliRunner().invoke(
        cli.app,
        ["collect", "duplicate"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert first_result.exit_code == 0
    assert second_result.exit_code == 0
    with sqlite3.connect(database_path) as connection:
        assert _table_count(connection, "queries") == 2
        assert _table_count(connection, "documents") == 1
        assert _table_count(connection, "document_urls") == 1

    assert "new: 1" in first_result.stdout
    assert "duplicate: 1" in second_result.stdout


@pytest.mark.revised
def test_collect_records_collection_events(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    pipeline = FakePipeline([_candidate(title="Evented Report")])
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    result = CliRunner().invoke(
        cli.app,
        ["collect", "events"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert result.exit_code == 0
    with sqlite3.connect(database_path) as connection:
        event = connection.execute(
            "SELECT event_type, message, metadata_json FROM events"
        ).fetchone()

    assert event[0] == "collect.completed"
    assert event[1] == "Collected 1 candidate for events."
    assert json.loads(event[2]) == {
        "keyword": "events",
        "new": 1,
        "updated": 0,
        "duplicate": 0,
        "errors": [],
    }


def _candidate(
    *,
    title: str,
    url: str = "https://example.org/result.pdf",
    source: SourceKind = SourceKind.COMMON_CRAWL,
    discovery_method: DiscoveryMethod = DiscoveryMethod.PUBLIC_INDEX,
    score: float = 0.75,
    metadata: dict[str, object] | None = None,
) -> RawCandidate:
    return RawCandidate(
        title=title,
        url=url,
        source=source,
        discovery_method=discovery_method,
        query='"keyword"',
        score=score,
        metadata=metadata or {},
    )


def _table_count(connection: sqlite3.Connection, table_name: str) -> int:
    row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    return int(row[0])
