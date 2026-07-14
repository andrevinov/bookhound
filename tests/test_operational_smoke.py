import json
from pathlib import Path
import sqlite3

from typer.testing import CliRunner

import bookhound.cli as cli
from bookhound.config import load_settings
from bookhound.models import SourceKind


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
