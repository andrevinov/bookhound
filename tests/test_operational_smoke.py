import json
from pathlib import Path
import sqlite3

from typer.testing import CliRunner

import pytest

import bookhound.cli as cli
from bookhound.config import load_settings
from bookhound.discovery_pipeline import DiscoveryPipeline
from bookhound.link_expansion import LinkExpansionAdapter, LinkExpansionConfig
from bookhound.models import DiscoveryMethod, SourceKind
from bookhound.query_planner import QueryPlanner, QueryPlannerConfig
from bookhound.sources import FakeSourceAdapter


@pytest.mark.revised
def test_configured_runtime_pipeline_has_expected_sources(
    tmp_path: Path,
    write_sitemap_runtime_config_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    config_path = write_sitemap_runtime_config_factory(
        tmp_path,
        database_path=database_path,
        pdf_directory=tmp_path / "pdfs",
    )

    settings = load_settings(config_path=config_path, project_root=tmp_path)
    pipeline = cli.build_search_pipeline(settings)

    assert settings.database_path == database_path
    assert [source.source_name for source in pipeline.sources] == [SourceKind.SITEMAP]
    assert pipeline.link_expander is None


def test_collect_smoke_creates_database_and_saves_fixture_candidate(
    tmp_path: Path,
    monkeypatch,
    count_rows_helper,
    sitemap_http_client_factory,
    write_sitemap_runtime_config_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    config_path = write_sitemap_runtime_config_factory(
        tmp_path,
        database_path=database_path,
        pdf_directory=tmp_path / "pdfs",
    )
    monkeypatch.setattr(
        cli,
        "build_http_client",
        lambda settings: sitemap_http_client_factory(
            robots_content=b"Sitemap: https://example.org/sitemap.xml",
            sitemap_content=b"""
                <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <url>
                    <loc>https://example.org/reports/operational-smoke.pdf</loc>
                    <lastmod>2026-07-04</lastmod>
                  </url>
                </urlset>
                """,
        ),
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["--config", str(config_path), "collect", "operational smoke"],
    )

    assert result.exit_code == 0
    assert database_path.exists()
    with sqlite3.connect(database_path) as connection:
        counts = {
            table: count_rows_helper(connection, table)
            for table in ["queries", "sources", "documents", "document_urls"]
        }
        document = connection.execute(
            "SELECT title, metadata_json FROM documents"
        ).fetchone()

    assert counts == {
        "queries": 1,
        "sources": 1,
        "documents": 1,
        "document_urls": 1,
    }
    assert document[0] == "operational-smoke.pdf"
    assert json.loads(document[1])["lastmod"] == "2026-07-04"


def test_collect_persists_expanded_links_from_non_utf8_html_without_failure_log(
    tmp_path: Path,
    monkeypatch,
    recording_http_client_factory,
    html_response_factory,
    sitemap_candidate_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    landing_page = sitemap_candidate_factory(
        title="Legacy Encoded Landing Page",
        url="https://example.org/reports/legacy",
        query="original query",
        score=0.7,
    )
    html = b"""
    <html>
      <body>
        <p>Legacy text byte: \xf4</p>
        <a href="/reports/legacy.pdf">Legacy PDF</a>
      </body>
    </html>
    """
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                SourceKind.SITEMAP,
                DiscoveryMethod.SITEMAP,
                [landing_page],
            )
        ],
        link_expander=LinkExpansionAdapter(
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
        ),
        query_planner=QueryPlanner(QueryPlannerConfig(max_variants=1)),
    )
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    result = CliRunner().invoke(
        cli.app,
        ["collect", "legacy encoding"],
        env={
            "BOOKHOUND_DATABASE_PATH": str(database_path),
            "BOOKHOUND_LOG_LEVEL": "INFO",
            "BOOKHOUND_LOG_FORMAT": "json",
            "BOOKHOUND_LOG_DESTINATION": "stderr",
            "BOOKHOUND_LOG_FILE": None,
        },
    )

    assert result.exit_code == 0
    logs = [
        json.loads(line)
        for line in result.stderr.splitlines()
        if line.strip()
    ]
    assert not any(log.get("event") == "collect.failed" for log in logs)
    assert any(log.get("event") == "collect.completed" for log in logs)

    with sqlite3.connect(database_path) as connection:
        document_urls = connection.execute(
            """
            SELECT document_urls.url, sources.name, document_urls.discovery_method
            FROM document_urls
            JOIN sources ON sources.id = document_urls.source_id
            ORDER BY document_urls.url
            """
        ).fetchall()

    assert (
        "https://example.org/reports/legacy.pdf",
        "link_expansion",
        "link_expansion",
    ) in document_urls


def test_export_smoke_writes_jsonl_after_collect(
    tmp_path: Path,
    monkeypatch,
    sitemap_http_client_factory,
    write_sitemap_runtime_config_factory,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    export_path = tmp_path / "bookhound-export.jsonl"
    config_path = write_sitemap_runtime_config_factory(
        tmp_path,
        database_path=database_path,
        pdf_directory=tmp_path / "pdfs",
    )
    monkeypatch.setattr(
        cli,
        "build_http_client",
        lambda settings: sitemap_http_client_factory(
            robots_content=b"Sitemap: https://example.org/sitemap.xml",
            sitemap_content=b"""
                <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <url>
                    <loc>https://example.org/reports/operational-smoke.pdf</loc>
                    <lastmod>2026-07-04</lastmod>
                  </url>
                </urlset>
                """,
        ),
        raising=False,
    )

    collect_result = CliRunner().invoke(
        cli.app,
        ["--config", str(config_path), "collect", "operational smoke"],
    )
    export_result = CliRunner().invoke(
        cli.app,
        [
            "--config",
            str(config_path),
            "export",
            "--format",
            "jsonl",
            "--output",
            str(export_path),
        ],
    )

    assert collect_result.exit_code == 0
    assert export_result.exit_code == 0
    assert export_path.exists()
    rows = [
        json.loads(line)
        for line in export_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["title"] == "operational-smoke.pdf"
    assert rows[0]["doi"] == ""
    assert rows[0]["url"] == "https://example.org/reports/operational-smoke.pdf"
    assert (
        rows[0]["canonical_url"]
        == "https://example.org/reports/operational-smoke.pdf"
    )
    assert rows[0]["source"] == "sitemap"
    assert rows[0]["license_status"] == "unknown"
    assert rows[0]["metadata"]["lastmod"] == "2026-07-04"
    assert rows[0]["metadata"]["sitemap_url"] == "https://example.org/sitemap.xml"
    assert rows[0]["metadata"]["url_type"] == "pdf"
