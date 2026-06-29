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

    def search(self, keyword: str) -> DiscoveryPipelineResult:
        return DiscoveryPipelineResult(
            query_plan=QueryPlan(
                keyword=keyword,
                variants=[PlannedQueryVariant(label="quoted", query=f'"{keyword}"')],
            ),
            candidates=self.candidates,
            errors=[],
        )


def test_global_config_option_controls_runtime_settings(
    tmp_path: Path,
    monkeypatch,
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
        lambda: FakePipeline([_candidate()]),
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


def _candidate() -> RawCandidate:
    return RawCandidate(
        title="Configured Result",
        url="https://example.org/configured.pdf",
        source=SourceKind.SITEMAP,
        discovery_method=DiscoveryMethod.SITEMAP,
        query='"configured search"',
        score=0.8,
    )
