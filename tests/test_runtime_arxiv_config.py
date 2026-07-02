from pathlib import Path

import pytest

import bookhound.cli as cli
from bookhound.config import load_settings
from bookhound.models import SourceKind


@pytest.mark.revised
def test_arxiv_settings_load_from_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[sources.arxiv]
enabled = true
max_results = 40
page_size = 8
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path, project_root=tmp_path)

    assert settings.sources.arxiv.enabled is True
    assert settings.sources.arxiv.max_results == 40
    assert settings.sources.arxiv.page_size == 8


@pytest.mark.revised
def test_build_search_pipeline_can_disable_arxiv(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_optional_source_credentials(monkeypatch)
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[sources.arxiv]
enabled = false
""".strip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path=config_path, project_root=tmp_path)

    pipeline = cli.build_search_pipeline(settings)

    assert SourceKind.ARXIV not in _source_names(pipeline)


@pytest.mark.revised
def test_build_search_pipeline_passes_arxiv_limits_to_adapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_optional_source_credentials(monkeypatch)
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[sources.arxiv]
enabled = true
max_results = 25
page_size = 5
""".strip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path=config_path, project_root=tmp_path)

    pipeline = cli.build_search_pipeline(settings)

    arxiv = _source_by_name(pipeline, SourceKind.ARXIV)
    assert arxiv.config.max_results == 25
    assert arxiv.config.page_size == 5


def _source_names(pipeline) -> list[SourceKind]:
    return [source.source_name for source in pipeline.sources]


def _source_by_name(pipeline, source_name: SourceKind):
    for source in pipeline.sources:
        if source.source_name is source_name:
            return source
    raise AssertionError(f"Missing source: {source_name.value}")


def _clear_optional_source_credentials(monkeypatch) -> None:
    monkeypatch.delenv("BOOKHOUND_GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("BOOKHOUND_GOOGLE_SEARCH_ENGINE_ID", raising=False)
    monkeypatch.delenv("BOOKHOUND_UNPAYWALL_EMAIL", raising=False)
