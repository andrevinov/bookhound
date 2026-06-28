from pathlib import Path

import pytest

from typer.testing import CliRunner

import bookhound.cli as cli
from bookhound.discovery_pipeline import DiscoveryPipelineResult
from bookhound.models import (
    DiscoveryMethod,
    DownloadRecord,
    DownloadStatus,
    LicenseDecision,
    LicenseStatus,
    RawCandidate,
    SourceKind,
)
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


class FakeClassifier:
    def __init__(self, decisions_by_url: dict[str, LicenseDecision]) -> None:
        self.decisions_by_url = decisions_by_url

    def classify(self, *, document_url: str, evidence: list) -> LicenseDecision:
        return self.decisions_by_url[document_url]


class FakeDownloadService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def download(
        self,
        *,
        document_id: int,
        document_url_id: int,
        url: str,
        license_decision: LicenseDecision,
        license_evidence_id: int | None = None,
        interactive: bool = False,
    ) -> DownloadRecord:
        self.calls.append(
            {
                "url": url,
                "status": license_decision.status,
                "interactive": interactive,
            }
        )
        if license_decision.status is LicenseStatus.DENIED:
            status = DownloadStatus.BLOCKED
        elif license_decision.status is LicenseStatus.UNKNOWN and not interactive:
            status = DownloadStatus.BLOCKED
        else:
            status = DownloadStatus.DOWNLOADED
        return DownloadRecord(
            url=url,
            local_path=f"/tmp/bookhound/{Path(url).name}",
            status=status,
            license_decision=license_decision,
        )


@pytest.mark.revised
def test_download_command_downloads_only_allowed_candidates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    allowed_candidate = _candidate("Allowed", "https://example.org/allowed.pdf")
    denied_candidate = _candidate("Denied", "https://example.org/denied.pdf")
    pipeline = FakePipeline([allowed_candidate, denied_candidate])
    download_service = FakeDownloadService()
    _patch_download_dependencies(
        monkeypatch,
        pipeline=pipeline,
        download_service=download_service,
        decisions={
            allowed_candidate.url: _decision(LicenseStatus.ALLOWED),
            denied_candidate.url: _decision(LicenseStatus.DENIED),
        },
    )

    result = CliRunner().invoke(
        cli.app,
        ["download", "climate"],
        env={"BOOKHOUND_DATABASE_PATH": str(tmp_path / "bookhound.sqlite3")},
    )

    assert result.exit_code == 0
    assert pipeline.searched_keywords == ["climate"]
    assert [call["url"] for call in download_service.calls] == [
        "https://example.org/allowed.pdf"
    ]
    assert "downloaded: 1" in result.stdout
    assert "blocked: 1" in result.stdout


@pytest.mark.revised
def test_download_command_downloads_manually_authorized_candidates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    candidate = _candidate("Manual", "https://example.org/manual.pdf")
    download_service = FakeDownloadService()
    _patch_download_dependencies(
        monkeypatch,
        pipeline=FakePipeline([candidate]),
        download_service=download_service,
        decisions={candidate.url: _decision(LicenseStatus.MANUALLY_AUTHORIZED)},
    )

    result = CliRunner().invoke(
        cli.app,
        ["download", "manual"],
        env={"BOOKHOUND_DATABASE_PATH": str(tmp_path / "bookhound.sqlite3")},
    )

    assert result.exit_code == 0
    assert [call["url"] for call in download_service.calls] == [
        "https://example.org/manual.pdf"
    ]
    assert "downloaded: 1" in result.stdout


@pytest.mark.revised
def test_previously_collected_only_option_does_not_call_external_discovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pipeline = FakePipeline([_candidate("Collected", "https://example.org/collected.pdf")])
    download_service = FakeDownloadService()
    database_path = tmp_path / "bookhound.sqlite3"
    _patch_download_dependencies(
        monkeypatch,
        pipeline=pipeline,
        download_service=download_service,
        decisions={"https://example.org/collected.pdf": _decision(LicenseStatus.ALLOWED)},
    )
    collect_result = CliRunner().invoke(
        cli.app,
        ["collect", "collected"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )
    assert collect_result.exit_code == 0
    pipeline.searched_keywords.clear()

    result = CliRunner().invoke(
        cli.app,
        ["download", "collected", "--collected-only"],
        env={"BOOKHOUND_DATABASE_PATH": str(database_path)},
    )

    assert result.exit_code == 0
    assert pipeline.searched_keywords == []


@pytest.mark.revised
def test_unknown_prompt_respects_the_user_response(
    tmp_path: Path,
    monkeypatch,
) -> None:
    candidate = _candidate("Unknown", "https://example.org/unknown.pdf")
    download_service = FakeDownloadService()
    _patch_download_dependencies(
        monkeypatch,
        pipeline=FakePipeline([candidate]),
        download_service=download_service,
        decisions={candidate.url: _decision(LicenseStatus.UNKNOWN)},
    )

    declined = CliRunner().invoke(
        cli.app,
        ["download", "unknown"],
        input="n\n",
        env={"BOOKHOUND_DATABASE_PATH": str(tmp_path / "declined.sqlite3")},
    )
    accepted = CliRunner().invoke(
        cli.app,
        ["download", "unknown"],
        input="y\n",
        env={"BOOKHOUND_DATABASE_PATH": str(tmp_path / "accepted.sqlite3")},
    )

    assert declined.exit_code == 0
    assert accepted.exit_code == 0
    assert "pending: 1" in declined.stdout
    assert "downloaded: 1" in accepted.stdout


@pytest.mark.revised
def test_download_final_summary_shows_correct_counts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    allowed = _candidate("Allowed", "https://example.org/allowed.pdf")
    denied = _candidate("Denied", "https://example.org/denied.pdf")
    unknown = _candidate("Unknown", "https://example.org/unknown.pdf")
    _patch_download_dependencies(
        monkeypatch,
        pipeline=FakePipeline([allowed, denied, unknown]),
        download_service=FakeDownloadService(),
        decisions={
            allowed.url: _decision(LicenseStatus.ALLOWED),
            denied.url: _decision(LicenseStatus.DENIED),
            unknown.url: _decision(LicenseStatus.UNKNOWN),
        },
    )

    result = CliRunner().invoke(
        cli.app,
        ["download", "summary"],
        input="n\n",
        env={"BOOKHOUND_DATABASE_PATH": str(tmp_path / "bookhound.sqlite3")},
    )

    assert result.exit_code == 0
    assert "downloaded: 1" in result.stdout
    assert "blocked: 1" in result.stdout
    assert "pending: 1" in result.stdout
    assert "failed: 0" in result.stdout


def _patch_download_dependencies(
    monkeypatch,
    *,
    pipeline: FakePipeline,
    download_service: FakeDownloadService,
    decisions: dict[str, LicenseDecision],
) -> None:
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)
    monkeypatch.setattr(
        cli,
        "build_license_classifier",
        lambda: FakeClassifier(decisions),
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "build_download_service",
        lambda repositories, settings, prompt: download_service,
        raising=False,
    )


def _candidate(title: str, url: str) -> RawCandidate:
    return RawCandidate(
        title=title,
        url=url,
        source=SourceKind.SITEMAP,
        discovery_method=DiscoveryMethod.SITEMAP,
        query='"keyword"',
        score=0.9,
    )


def _decision(status: LicenseStatus) -> LicenseDecision:
    return LicenseDecision(status=status, reason=f"Test decision: {status.value}")
