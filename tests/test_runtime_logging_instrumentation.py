from datetime import datetime, timezone
import logging
import sqlite3

import pytest

from bookhound.database import initialize_database
from bookhound.discovery_pipeline import DiscoveryPipeline, DiscoveryStepResult
from bookhound.http_client import (
    BookhoundHttpClient,
    HttpClientConfig,
    HttpResponse,
    HttpTimeoutError,
)
from bookhound.models import DiscoveryMethod, LicenseEvidence, SourceKind
from bookhound.query_planner import (
    PlannedQueryVariant,
    QueryPlan,
    QueryPlanner,
    QueryPlannerConfig,
)
from bookhound.repositories import RepositorySet
from bookhound.sources import (
    DisabledSourceAdapter,
    FakeSourceAdapter,
    SourceAvailabilityError,
    run_source_search,
)


@pytest.fixture(autouse=True)
def reset_bookhound_logging():
    logger = logging.getLogger("bookhound")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    logger.handlers = []
    logger.propagate = True

    yield

    for handler in logger.handlers:
        handler.close()
    logger.handlers = original_handlers
    logger.setLevel(original_level)
    logger.propagate = original_propagate


@pytest.mark.revised
def test_discovery_pipeline_logs_source_start_and_completion(
    caplog,
    common_crawl_candidate_factory,
    sitemap_candidate_factory,
) -> None:
    caplog.set_level(logging.DEBUG, logger="bookhound")
    common_crawl_source = FakeSourceAdapter(
        source=SourceKind.COMMON_CRAWL,
        discovery_method=DiscoveryMethod.PUBLIC_INDEX,
        candidates=[
            common_crawl_candidate_factory(
                title="Common Crawl Logging Report",
                url="https://example.org/common-crawl.pdf",
            )
        ],
    )
    sitemap_source = FakeSourceAdapter(
        source=SourceKind.SITEMAP,
        discovery_method=DiscoveryMethod.SITEMAP,
        candidates=[
            sitemap_candidate_factory(
                title="Sitemap Logging Report",
                url="https://example.org/sitemap.pdf",
            )
        ],
    )
    pipeline = DiscoveryPipeline(
        sources=[common_crawl_source, sitemap_source],
        query_planner=_single_variant_query_planner(),
    )

    result = pipeline.search("logging")

    assert len(result.candidates) == 2
    common_crawl_started = _single_record(
        caplog.records,
        "discovery.source.started",
        source="common_crawl",
    )
    common_crawl_completed = _single_record(
        caplog.records,
        "discovery.source.completed",
        source="common_crawl",
    )
    sitemap_started = _single_record(
        caplog.records,
        "discovery.source.started",
        source="sitemap",
    )
    sitemap_completed = _single_record(
        caplog.records,
        "discovery.source.completed",
        source="sitemap",
    )

    assert common_crawl_started.query_variant_label == "quoted"
    assert common_crawl_started.query == '"logging"'
    assert common_crawl_completed.candidate_count == 1
    assert common_crawl_completed.error_count == 0
    assert common_crawl_completed.duration_ms >= 0
    assert sitemap_started.query_variant_label == "quoted"
    assert sitemap_completed.candidate_count == 1
    assert sitemap_completed.error_count == 0
    assert sitemap_completed.duration_ms >= 0


@pytest.mark.revised
def test_discovery_pipeline_logs_deduplicated_result_count(
    caplog,
    common_crawl_candidate_factory,
    sitemap_candidate_factory,
) -> None:
    caplog.set_level(logging.INFO, logger="bookhound")
    duplicate_url = "https://example.org/duplicate.pdf?utm_source=test"
    pipeline = DiscoveryPipeline(
        sources=[
            FakeSourceAdapter(
                source=SourceKind.COMMON_CRAWL,
                discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                candidates=[
                    common_crawl_candidate_factory(
                        title="Common Crawl Duplicate",
                        url=duplicate_url,
                    )
                ],
            ),
            FakeSourceAdapter(
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                candidates=[
                    sitemap_candidate_factory(
                        title="Sitemap Duplicate",
                        url="https://example.org/duplicate.pdf",
                    )
                ],
            ),
        ],
        query_planner=_single_variant_query_planner(),
    )

    result = pipeline.search("dedupe")

    assert len(result.candidates) == 1
    completed = _single_record(caplog.records, "discovery.pipeline.completed")
    assert completed.keyword == "dedupe"
    assert completed.variant_count == 1
    assert completed.source_count == 2
    assert completed.raw_candidate_count == 2
    assert completed.candidate_count == 1
    assert completed.duration_ms >= 0


@pytest.mark.revised
def test_run_source_search_logs_disabled_source_warning(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="bookhound")
    adapter = DisabledSourceAdapter(
        source=SourceKind.GOOGLE,
        discovery_method=DiscoveryMethod.API,
        reason="Missing API key.",
    )

    result = run_source_search(adapter, query="machine learning")

    assert result.candidates == []
    assert result.errors == ["Source google is disabled: Missing API key."]
    record = _single_record(caplog.records, "source.disabled")
    assert record.levelno == logging.WARNING
    assert record.source == "google"
    assert record.query == "machine learning"
    assert record.reason == "Missing API key"


@pytest.mark.revised
def test_run_source_search_logs_source_error_warning(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="bookhound")
    adapter = FakeSourceAdapter(
        source=SourceKind.COMMON_CRAWL,
        discovery_method=DiscoveryMethod.PUBLIC_INDEX,
        candidates=[],
        error=SourceAvailabilityError(
            SourceKind.COMMON_CRAWL,
            "Index unavailable.",
        ),
    )

    result = run_source_search(adapter, query="open access")

    assert result.candidates == []
    assert result.errors == ["availability: Index unavailable."]
    record = _single_record(caplog.records, "source.search_failed")
    assert record.levelno == logging.WARNING
    assert record.source == "common_crawl"
    assert record.query == "open access"
    assert record.error_kind == "availability"
    assert record.error == "availability: Index unavailable."


@pytest.mark.revised
def test_http_client_logs_retry_warning_with_sanitized_url(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="bookhound")
    transport = FakeTransport(
        [
            HttpResponse(
                status_code=503,
                headers={},
                content=b"Service unavailable",
                url="https://example.org/report.pdf?token=secret#fragment",
            ),
            HttpResponse(
                status_code=200,
                headers={},
                content=b"%PDF-1.7",
                url="https://example.org/report.pdf?token=secret#fragment",
            ),
        ]
    )
    client = BookhoundHttpClient(
        config=HttpClientConfig(
            user_agent="BookhoundTest/1.0",
            timeout_seconds=5.0,
            max_retries=1,
            retry_backoff_seconds=0.25,
        ),
        transport=transport,
        sleep=lambda seconds: None,
    )

    response = client.get("https://example.org/report.pdf?token=secret#fragment")

    assert response.status_code == 200
    record = _single_record(caplog.records, "http.retry")
    assert record.levelno == logging.WARNING
    assert record.method == "GET"
    assert record.url == "https://example.org/report.pdf"
    assert record.status_code == 503
    assert record.attempt == 1
    assert record.total_attempts == 2
    assert "secret" not in record.url


@pytest.mark.revised
def test_http_client_logs_timeout_warning(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="bookhound")
    client = BookhoundHttpClient(
        config=HttpClientConfig(
            user_agent="BookhoundTest/1.0",
            timeout_seconds=2.5,
        ),
        transport=FakeTransport([TimeoutError("request timed out")]),
    )

    with pytest.raises(HttpTimeoutError):
        client.get("https://example.org/slow.pdf?token=secret")

    record = _single_record(caplog.records, "http.timeout")
    assert record.levelno == logging.WARNING
    assert record.method == "GET"
    assert record.url == "https://example.org/slow.pdf"
    assert record.attempt == 1
    assert record.total_attempts == 1
    assert "secret" not in record.url


@pytest.mark.revised
def test_http_client_logs_cache_and_rate_limit_at_debug(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="bookhound")
    clock = FakeClock()
    transport = FakeTransport(
        [
            HttpResponse(
                status_code=200,
                headers={},
                content=b"cached",
                url="https://example.org/cache.html?token=secret",
            ),
            HttpResponse(
                status_code=200,
                headers={},
                content=b"one",
                url="https://example.org/rate-one.html",
            ),
            HttpResponse(
                status_code=200,
                headers={},
                content=b"two",
                url="https://example.org/rate-two.html",
            ),
        ]
    )
    client = BookhoundHttpClient(
        config=HttpClientConfig(
            user_agent="BookhoundTest/1.0",
            timeout_seconds=5.0,
            rate_limit_per_second=2.0,
        ),
        transport=transport,
        clock=clock.monotonic,
        sleep=clock.sleep,
    )

    first_response = client.get(
        "https://example.org/cache.html?token=secret",
        cache=True,
    )
    cached_response = client.get(
        "https://example.org/cache.html?token=secret",
        cache=True,
    )
    client.get("https://example.org/rate-one.html", rate_limit_key="source:sitemap")
    client.get("https://example.org/rate-two.html", rate_limit_key="source:sitemap")

    assert first_response.content == b"cached"
    assert cached_response.content == b"cached"
    cache_record = _single_record(caplog.records, "http.cache_hit")
    rate_limit_record = _single_record(caplog.records, "http.rate_limited")
    assert cache_record.levelno == logging.DEBUG
    assert cache_record.url == "https://example.org/cache.html"
    assert "secret" not in cache_record.url
    assert rate_limit_record.levelno == logging.DEBUG
    assert rate_limit_record.rate_limit_key == "source:sitemap"
    assert rate_limit_record.wait_seconds == 0.5
    assert clock.sleep_calls == [0.5]


@pytest.mark.revised
def test_collect_incremental_logs_step_persistence_completed(
    caplog,
    tmp_path,
    sitemap_candidate_factory,
) -> None:
    caplog.set_level(logging.INFO, logger="bookhound")
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    query_plan = _query_plan("step logging")
    candidate = sitemap_candidate_factory(
        title="Step Logging Report",
        url="https://example.org/step-logging.pdf",
        query=query_plan.variants[0].query,
    )

    try:
        query_id = repositories.begin_collection(query_plan)
        summary = repositories.save_collection_step(
            query_id,
            _step(
                query_plan=query_plan,
                source=SourceKind.SITEMAP,
                discovery_method=DiscoveryMethod.SITEMAP,
                status="completed",
                candidates=[candidate],
            ),
        )
    finally:
        repositories.close()

    record = _single_record(caplog.records, "collect.step.persistence.completed")
    assert summary.new == 1
    assert record.levelno == logging.INFO
    assert record.keyword == "step logging"
    assert record.query_id == query_id
    assert record.step_id > 0
    assert record.query_variant_label == "quoted"
    assert record.query == '"step logging"'
    assert record.source == "sitemap"
    assert record.discovery_method == "sitemap"
    assert record.status == "completed"
    assert record.candidate_count == 1
    assert record.new == 1
    assert record.updated == 0
    assert record.duplicate == 0
    assert record.error_count == 0
    assert record.duration_ms >= 0


@pytest.mark.revised
def test_collect_incremental_logs_step_persistence_failed(
    caplog,
    tmp_path,
    common_crawl_candidate_factory,
) -> None:
    caplog.set_level(logging.ERROR, logger="bookhound")
    repositories = RepositorySet(initialize_database(tmp_path / "bookhound.sqlite3"))
    query_plan = _query_plan("step failure logging")
    invalid_evidence = LicenseEvidence.model_construct(
        source="common_crawl",
        evidence_type="metadata",
        value="invalid status",
        suggested_status="invalid-license-status",
        confidence=0.2,
        collected_at=datetime(2026, 6, 7, 9, 0, tzinfo=timezone.utc),
    )
    candidate = common_crawl_candidate_factory(
        title="Broken Evidence Result",
        url="https://example.org/broken-evidence.pdf",
        query=query_plan.variants[0].query,
        metadata={
            "license_evidence": [
                {
                    "evidence": invalid_evidence,
                    "metadata": {"source_event": "invalid_fixture"},
                }
            ],
        },
    )

    try:
        query_id = repositories.begin_collection(query_plan)
        with pytest.raises(sqlite3.IntegrityError):
            repositories.save_collection_step(
                query_id,
                _step(
                    query_plan=query_plan,
                    source=SourceKind.COMMON_CRAWL,
                    discovery_method=DiscoveryMethod.PUBLIC_INDEX,
                    status="completed",
                    candidates=[candidate],
                ),
            )
    finally:
        repositories.close()

    record = _single_record(caplog.records, "collect.step.persistence.failed")
    assert record.levelno == logging.ERROR
    assert record.keyword == "step failure logging"
    assert record.query_id == query_id
    assert record.step_id > 0
    assert record.query_variant_label == "quoted"
    assert record.query == '"step failure logging"'
    assert record.source == "common_crawl"
    assert record.discovery_method == "public_index"
    assert record.status == "completed"
    assert record.candidate_count == 1
    assert record.error_count == 0
    assert record.duration_ms >= 0
    assert "CHECK constraint failed" in record.error


class FakeTransport:
    def __init__(self, outcomes: list[HttpResponse | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClock:
    def __init__(self) -> None:
        self.current_time = 0.0
        self.sleep_calls: list[float] = []

    def monotonic(self) -> float:
        return self.current_time

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.current_time += seconds


def _single_variant_query_planner() -> QueryPlanner:
    return QueryPlanner(QueryPlannerConfig(max_variants=1))


def _query_plan(keyword: str) -> QueryPlan:
    return QueryPlan(
        keyword=keyword,
        variants=[PlannedQueryVariant(label="quoted", query=f'"{keyword}"')],
    )


def _step(
    *,
    query_plan: QueryPlan,
    source: SourceKind,
    discovery_method: DiscoveryMethod,
    status: str,
    candidates=None,
    errors=None,
) -> DiscoveryStepResult:
    return DiscoveryStepResult(
        query_plan=query_plan,
        variant=query_plan.variants[0],
        source=source,
        discovery_method=discovery_method,
        status=status,
        candidates=list(candidates or []),
        errors=list(errors or []),
        events=[],
    )


def _single_record(
    records: list[logging.LogRecord],
    event: str,
    *,
    source: str | None = None,
) -> logging.LogRecord:
    matches = [
        record
        for record in records
        if getattr(record, "event", None) == event
        and (source is None or getattr(record, "source", None) == source)
    ]
    assert len(matches) == 1, [
        {
            "event": getattr(record, "event", None),
            "source": getattr(record, "source", None),
            "message": record.getMessage(),
        }
        for record in records
    ]
    return matches[0]
