# Consolidated test module. See docs/audit-tests/03-centralize-test-files.md.
# Consolidated from test_search_cli.py

import pytest

import json

from typer.testing import CliRunner

import bookhound.cli as cli

@pytest.mark.revised
def test_search_command_calls_pipeline(
    monkeypatch,
    recording_pipeline_factory,
    common_crawl_candidate_factory,
) -> None:
    pipeline = recording_pipeline_factory(
        [common_crawl_candidate_factory(title="Climate Report")]
    )
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    result = CliRunner().invoke(cli.app, ["search", "climate policy"])

    assert result.exit_code == 0
    assert pipeline.searched_keywords == ["climate policy"]


@pytest.mark.revised
def test_search_table_output_contains_main_fields(
    monkeypatch,
    recording_pipeline_factory,
    common_crawl_candidate_factory,
) -> None:
    pipeline = recording_pipeline_factory(
        [
            common_crawl_candidate_factory(
                title="Machine Learning Notes",
                url="https://example.org/notes.pdf",
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
def test_search_json_returns_parseable_json(
    monkeypatch,
    recording_pipeline_factory,
    sitemap_candidate_factory,
) -> None:
    pipeline = recording_pipeline_factory(
        [
            sitemap_candidate_factory(
                title="Statistics Handbook",
                url="https://example.org/statistics.pdf",
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
def test_search_result_limit_is_respected(
    monkeypatch,
    recording_pipeline_factory,
    common_crawl_candidate_factory,
) -> None:
    pipeline = recording_pipeline_factory(
        [
            common_crawl_candidate_factory(title="First Result", score=0.90),
            common_crawl_candidate_factory(title="Second Result", score=0.80),
        ]
    )
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    result = CliRunner().invoke(cli.app, ["search", "biology", "--limit", "1", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [candidate["title"] for candidate in payload["results"]] == ["First Result"]


# Consolidated from test_search_runtime_cli.py

import json
from pathlib import Path

import pytest

from typer.testing import CliRunner

import bookhound.cli as cli


@pytest.mark.revised
def test_search_command_uses_runtime_pipeline_and_prints_real_adapter_results(
    tmp_path: Path,
    monkeypatch,
    sitemap_http_client_factory,
    write_sitemap_runtime_config_factory,
) -> None:
    database_path = tmp_path / "search.sqlite3"
    config_path = write_sitemap_runtime_config_factory(
        tmp_path,
        database_path=database_path,
    )
    monkeypatch.setattr(
        cli,
        "build_http_client",
        lambda settings: sitemap_http_client_factory(
            robots_content=b"Sitemap: https://example.org/sitemap.xml",
            sitemap_content=b"""
                <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <url>
                    <loc>https://example.org/reports/machine-learning.pdf</loc>
                    <lastmod>2026-07-01</lastmod>
                  </url>
                  <url>
                    <loc>https://example.org/about</loc>
                  </url>
                </urlset>
                """,
        ),
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["--config", str(config_path), "search", "machine learning"],
    )

    assert result.exit_code == 0
    assert "machine-learning.pdf" in result.stdout
    assert "https://example.org/reports/machine-learning.pdf" in result.stdout
    assert "sitemap" in result.stdout
    assert database_path.exists() is False


@pytest.mark.revised
def test_search_json_uses_runtime_pipeline_and_does_not_persist_results(
    tmp_path: Path,
    monkeypatch,
    sitemap_http_client_factory,
    write_sitemap_runtime_config_factory,
) -> None:
    database_path = tmp_path / "search.sqlite3"
    config_path = write_sitemap_runtime_config_factory(
        tmp_path,
        database_path=database_path,
    )
    monkeypatch.setattr(
        cli,
        "build_http_client",
        lambda settings: sitemap_http_client_factory(
            robots_content=b"Sitemap: https://example.org/sitemap.xml",
            sitemap_content=b"""
                <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <url>
                    <loc>https://example.org/reports/machine-learning.pdf</loc>
                    <lastmod>2026-07-01</lastmod>
                  </url>
                  <url>
                    <loc>https://example.org/about</loc>
                  </url>
                </urlset>
                """,
        ),
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["--config", str(config_path), "search", "machine learning", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "keyword": "machine learning",
        "results": [
            {
                "title": "machine-learning.pdf",
                "url": "https://example.org/reports/machine-learning.pdf",
                "source": "sitemap",
                "score": 0.7,
                "preliminary_status": "unknown",
            }
        ],
    }
    assert database_path.exists() is False


# Consolidated from test_cli_config_option.py

from pathlib import Path
import sqlite3

from typer.testing import CliRunner

import bookhound.cli as cli


def test_global_config_option_controls_runtime_settings(
    tmp_path: Path,
    monkeypatch,
    recording_pipeline_factory,
    sitemap_candidate_factory,
) -> None:
    database_path = tmp_path / "state" / "configured.sqlite3"
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        f"""
[paths]
database_path = "{database_path}"
pdf_directory = "{tmp_path / "pdfs"}"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli,
        "build_search_pipeline",
        lambda: recording_pipeline_factory(
            [
                sitemap_candidate_factory(
                    title="Configured Result",
                    url="https://example.org/configured.pdf",
                    query='"configured search"',
                    score=0.8,
                )
            ]
        ),
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["--config", str(config_path), "collect", "configured search"],
    )

    assert result.exit_code == 0
    assert database_path.exists()
    with sqlite3.connect(database_path) as connection:
        query = connection.execute("SELECT keyword FROM queries").fetchone()
    assert query[0] == "configured search"
