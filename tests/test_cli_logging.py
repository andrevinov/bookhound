import json
from pathlib import Path

from typer.testing import CliRunner

import pytest

import bookhound.cli as cli
from bookhound.daemon import DaemonRunResult
from bookhound.download_workflow import DownloadSummary


@pytest.mark.revised
def test_search_json_output_remains_clean_when_json_logging_is_enabled(
    tmp_path: Path,
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

    result = CliRunner().invoke(
        cli.app,
        ["search", "statistics", "--json"],
        env=_logging_env(tmp_path),
    )

    assert result.exit_code == 0
    assert "search.started" not in result.stdout
    assert "search.completed" not in result.stdout
    assert json.loads(result.stdout) == {
        "keyword": "statistics",
        "results": [
            {
                "title": "Statistics Handbook",
                "url": "https://example.org/statistics.pdf",
                "source": "sitemap",
                "score": 0.91,
                "preliminary_status": "unknown",
            }
        ],
    }

    logs = _json_logs(result.stderr)
    assert _event(logs, "search.started")["mode"] == "search"
    assert _event(logs, "search.completed")["result_count"] == 1


@pytest.mark.revised
def test_search_emits_started_and_completed_logs(
    tmp_path: Path,
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

    result = CliRunner().invoke(
        cli.app,
        ["search", "machine learning"],
        env=_logging_env(tmp_path),
    )

    assert result.exit_code == 0
    logs = _json_logs(result.stderr)
    started = _event(logs, "search.started")
    completed = _event(logs, "search.completed")

    assert started["level"] == "INFO"
    assert started["mode"] == "search"
    assert started["keyword"] == "machine learning"
    assert completed["mode"] == "search"
    assert completed["keyword"] == "machine learning"
    assert completed["result_count"] == 1
    assert completed["duration_ms"] >= 0
    assert started["run_id"] == completed["run_id"]


@pytest.mark.revised
def test_collect_emits_summary_log_with_counts(
    tmp_path: Path,
    monkeypatch,
    recording_pipeline_factory,
    sitemap_candidate_factory,
) -> None:
    pipeline = recording_pipeline_factory(
        [
            sitemap_candidate_factory(
                title="Collected Logging Report",
                url="https://example.org/logging.pdf",
            )
        ]
    )
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    result = CliRunner().invoke(
        cli.app,
        ["collect", "logging"],
        env=_logging_env(tmp_path),
    )

    assert result.exit_code == 0
    assert "Collected 1 candidate: new: 1, updated: 0, duplicate: 0" in result.stdout

    logs = _json_logs(result.stderr)
    started = _event(logs, "collect.started")
    completed = _event(logs, "collect.completed")

    assert started["mode"] == "collect"
    assert started["keyword"] == "logging"
    assert completed["mode"] == "collect"
    assert completed["keyword"] == "logging"
    assert completed["total"] == 1
    assert completed["new"] == 1
    assert completed["updated"] == 0
    assert completed["duplicate"] == 0
    assert completed["duration_ms"] >= 0
    assert started["run_id"] == completed["run_id"]


@pytest.mark.revised
def test_collect_emits_incremental_run_and_step_logs(
    tmp_path: Path,
    monkeypatch,
    recording_pipeline_factory,
    sitemap_candidate_factory,
) -> None:
    pipeline = recording_pipeline_factory(
        [
            sitemap_candidate_factory(
                title="Incremental Logging Report",
                url="https://example.org/incremental-logging.pdf",
            )
        ]
    )
    monkeypatch.setattr(cli, "build_search_pipeline", lambda: pipeline, raising=False)

    result = CliRunner().invoke(
        cli.app,
        ["collect", "incremental logging"],
        env=_logging_env(tmp_path),
    )

    assert result.exit_code == 0
    logs = _json_logs(result.stderr)
    run_started = _event(logs, "collect.run.started")
    step_started = _event(logs, "collect.step.started")
    step_completed = _event(logs, "collect.step.completed")
    run_completed = _event(logs, "collect.run.completed")

    assert run_started["mode"] == "collect"
    assert run_started["keyword"] == "incremental logging"
    assert run_started["query_id"] > 0
    assert step_started["query_id"] == run_started["query_id"]
    assert step_started["query_variant_label"] == "quoted"
    assert step_started["query"] == '"incremental logging"'
    assert step_started["source"] == "sitemap"
    assert step_started["discovery_method"] == "sitemap"
    assert step_started["candidate_count"] == 1
    assert step_started["error_count"] == 0
    assert step_completed["query_id"] == run_started["query_id"]
    assert step_completed["source"] == "sitemap"
    assert step_completed["candidate_count"] == 1
    assert step_completed["new"] == 1
    assert step_completed["updated"] == 0
    assert step_completed["duplicate"] == 0
    assert step_completed["duration_ms"] >= 0
    assert run_completed["query_id"] == run_started["query_id"]
    assert run_completed["total"] == 1
    assert run_completed["new"] == 1
    assert run_completed["updated"] == 0
    assert run_completed["duplicate"] == 0
    assert run_completed["error_count"] == 0
    assert run_completed["duration_ms"] >= 0
    assert {
        run_started["run_id"],
        step_started["run_id"],
        step_completed["run_id"],
        run_completed["run_id"],
    } == {run_started["run_id"]}


@pytest.mark.revised
def test_download_emits_summary_log_without_bypassing_cli_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_download_candidates(keyword, collected_only, repositories):
        assert keyword == "logging"
        assert collected_only is False
        return [], 0

    def fake_download_candidates_with_license_gate(
        candidates,
        *,
        classifier,
        service,
        prompt,
    ) -> DownloadSummary:
        assert candidates == []
        return DownloadSummary(downloaded=1, blocked=1, pending=1, failed=0)

    monkeypatch.setattr(
        cli,
        "_download_candidates",
        fake_download_candidates,
        raising=False,
    )
    monkeypatch.setattr(cli, "build_license_classifier", lambda: object(), raising=False)
    monkeypatch.setattr(
        cli,
        "build_download_service",
        lambda repositories, settings, prompt: object(),
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "_download_candidates_with_license_gate",
        fake_download_candidates_with_license_gate,
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["download", "logging"],
        env=_logging_env(tmp_path),
    )

    assert result.exit_code == 0
    assert "Download summary: downloaded: 1, blocked: 1, pending: 1, failed: 0" in result.stdout

    logs = _json_logs(result.stderr)
    started = _event(logs, "download.started")
    completed = _event(logs, "download.completed")

    assert started["mode"] == "download"
    assert started["keyword"] == "logging"
    assert completed["mode"] == "download"
    assert completed["keyword"] == "logging"
    assert completed["downloaded"] == 1
    assert completed["blocked"] == 1
    assert completed["pending"] == 1
    assert completed["failed"] == 0
    assert completed["duration_ms"] >= 0
    assert started["run_id"] == completed["run_id"]


@pytest.mark.revised
def test_job_add_daemon_run_once_and_export_emit_boundary_logs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "bookhound.sqlite3"
    env = _logging_env(tmp_path, database_path=database_path)

    job_result = CliRunner().invoke(
        cli.app,
        ["job", "add", "logging job", "--priority", "3"],
        env=env,
    )

    class FakeDaemonRunner:
        def run_once(self) -> DaemonRunResult:
            return DaemonRunResult(locked=False, job_id=7)

    monkeypatch.setattr(
        cli,
        "build_daemon_runner",
        lambda repositories, settings: FakeDaemonRunner(),
        raising=False,
    )
    daemon_result = CliRunner().invoke(cli.app, ["daemon", "run-once"], env=env)
    export_path = tmp_path / "export.jsonl"
    export_result = CliRunner().invoke(
        cli.app,
        ["export", "--output", str(export_path)],
        env=env,
    )

    assert job_result.exit_code == 0
    assert daemon_result.exit_code == 0
    assert export_result.exit_code == 0

    job_log = _event(_json_logs(job_result.stderr), "job.created")
    daemon_log = _event(_json_logs(daemon_result.stderr), "daemon.run_once.completed")
    export_log = _event(_json_logs(export_result.stderr), "export.completed")

    assert job_log["keyword"] == "logging job"
    assert job_log["priority"] == 3
    assert job_log["job_id"] == 1
    assert daemon_log["mode"] == "daemon"
    assert daemon_log["job_id"] == 7
    assert daemon_log["locked"] is False
    assert export_log["mode"] == "export"
    assert export_log["row_count"] == 0
    assert export_log["output"] == str(export_path)
    assert export_log["format"] == "jsonl"


@pytest.mark.revised
def test_command_failure_is_logged_to_stderr(tmp_path: Path) -> None:
    missing_config = tmp_path / "missing-bookhound.toml"

    result = CliRunner().invoke(
        cli.app,
        ["--config", str(missing_config), "collect", "missing config"],
        env=_logging_env(tmp_path),
    )

    assert result.exit_code == 1
    assert f"Error: Configuration file not found: {missing_config}" in result.stdout
    assert "Traceback" not in result.stdout

    failure_log = _event(_json_logs(result.stderr), "collect.failed")
    assert failure_log["level"] == "ERROR"
    assert failure_log["mode"] == "collect"
    assert failure_log["keyword"] == "missing config"
    assert str(missing_config) in failure_log["error"]


@pytest.mark.revised
def test_unexpected_command_failure_logs_error_type_and_traceback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FailingPipeline:
        def search(self, keyword: str) -> object:
            assert keyword == "runtime failure"
            raise RuntimeError("simulated pipeline failure")

        def iter_search(self, keyword: str):
            assert keyword == "runtime failure"
            raise RuntimeError("simulated pipeline failure")
            yield

    monkeypatch.setattr(
        cli,
        "build_search_pipeline",
        lambda: FailingPipeline(),
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["collect", "runtime failure"],
        env=_logging_env(tmp_path),
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.stdout

    failure_log = _event(_json_logs(result.stderr), "collect.failed")
    assert failure_log["level"] == "ERROR"
    assert failure_log["mode"] == "collect"
    assert failure_log["keyword"] == "runtime failure"
    assert failure_log["error"] == "simulated pipeline failure"
    assert failure_log["error_type"] == "RuntimeError"
    assert "Traceback (most recent call last)" in failure_log["exception"]
    assert "RuntimeError: simulated pipeline failure" in failure_log["exception"]


@pytest.mark.revised
def test_collect_fatal_failure_emits_incremental_run_failed_log(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FailingPipeline:
        def iter_search(self, keyword: str):
            assert keyword == "incremental runtime failure"
            raise RuntimeError("simulated incremental failure")
            yield

    monkeypatch.setattr(
        cli,
        "build_search_pipeline",
        lambda: FailingPipeline(),
        raising=False,
    )

    result = CliRunner().invoke(
        cli.app,
        ["collect", "incremental runtime failure"],
        env=_logging_env(tmp_path),
    )

    assert result.exit_code == 1
    logs = _json_logs(result.stderr)
    run_failed = _event(logs, "collect.run.failed")
    command_failed = _event(logs, "collect.failed")

    assert run_failed["level"] == "ERROR"
    assert run_failed["mode"] == "collect"
    assert run_failed["keyword"] == "incremental runtime failure"
    assert run_failed["query_id"] is None
    assert run_failed["total"] == 0
    assert run_failed["new"] == 0
    assert run_failed["updated"] == 0
    assert run_failed["duplicate"] == 0
    assert run_failed["error_count"] == 0
    assert run_failed["error"] == "simulated incremental failure"
    assert run_failed["error_type"] == "RuntimeError"
    assert "RuntimeError: simulated incremental failure" in run_failed["exception"]
    assert command_failed["event"] == "collect.failed"
    assert command_failed["error"] == "simulated incremental failure"


def _logging_env(
    tmp_path: Path,
    *,
    database_path: Path | None = None,
) -> dict[str, str | None]:
    return {
        "BOOKHOUND_DATABASE_PATH": str(database_path or tmp_path / "bookhound.sqlite3"),
        "BOOKHOUND_LOG_LEVEL": "INFO",
        "BOOKHOUND_LOG_FORMAT": "json",
        "BOOKHOUND_LOG_DESTINATION": "stderr",
        "BOOKHOUND_LOG_FILE": None,
    }


def _json_logs(stderr: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in stderr.splitlines() if line.strip()]


def _event(
    logs: list[dict[str, object]],
    event_type: str,
) -> dict[str, object]:
    matches = [log for log in logs if log.get("event") == event_type]
    assert len(matches) == 1, logs
    return matches[0]
