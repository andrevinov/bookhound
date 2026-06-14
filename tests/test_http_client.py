import pytest

from bookhound.http_client import (
    BookhoundHttpClient,
    HttpClientConfig,
    HttpResponse,
    HttpTimeoutError,
)


class FakeTransport:
    def __init__(self, outcomes: list[HttpResponse | Exception]) -> None:
        self.outcomes = outcomes
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


@pytest.mark.revised
def test_user_agent_and_default_headers_are_sent() -> None:
    transport = FakeTransport(
        [
            HttpResponse(
                status_code=200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-1.7",
                url="https://example.org/report.pdf",
            )
        ]
    )
    client = BookhoundHttpClient(
        config=HttpClientConfig(
            user_agent="BookhoundTest/1.0",
            timeout_seconds=5.0,
            default_headers={"Accept": "application/pdf", "X-Test-Run": "task-10"},
        ),
        transport=transport,
    )

    response = client.get("https://example.org/report.pdf")

    assert response.status_code == 200
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "GET"
    assert request.url == "https://example.org/report.pdf"
    assert request.timeout_seconds == 5.0
    assert request.headers["User-Agent"] == "BookhoundTest/1.0"
    assert request.headers["Accept"] == "application/pdf"
    assert request.headers["X-Test-Run"] == "task-10"


@pytest.mark.revised
def test_timeout_produces_typed_error() -> None:
    transport = FakeTransport([TimeoutError("request timed out")])
    client = BookhoundHttpClient(
        config=HttpClientConfig(user_agent="BookhoundTest/1.0", timeout_seconds=2.5),
        transport=transport,
    )

    with pytest.raises(HttpTimeoutError) as error:
        client.get("https://example.org/slow.pdf")

    assert "https://example.org/slow.pdf" in str(error.value)
    assert transport.requests[0].timeout_seconds == 2.5


@pytest.mark.revised
def test_retry_happens_for_transient_errors() -> None:
    clock = FakeClock()
    transport = FakeTransport(
        [
            HttpResponse(
                status_code=503,
                headers={"retry-after": "ignored-by-client-backoff"},
                content=b"Service unavailable",
                url="https://example.org/report.pdf",
            ),
            HttpResponse(
                status_code=200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-1.7",
                url="https://example.org/report.pdf",
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
        clock=clock.monotonic,
        sleep=clock.sleep,
    )

    response = client.get("https://example.org/report.pdf")

    assert response.status_code == 200
    assert len(transport.requests) == 2
    assert clock.sleep_calls == [0.25]


@pytest.mark.revised
def test_rate_limit_is_applied_per_key() -> None:
    clock = FakeClock()
    transport = FakeTransport(
        [
            HttpResponse(status_code=200, headers={}, content=b"one", url="https://a.test/one"),
            HttpResponse(status_code=200, headers={}, content=b"two", url="https://a.test/two"),
            HttpResponse(status_code=200, headers={}, content=b"three", url="https://b.test/one"),
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

    client.get("https://a.test/one", rate_limit_key="source:google")
    client.get("https://a.test/two", rate_limit_key="source:google")
    client.get("https://b.test/one", rate_limit_key="source:common_crawl")

    assert len(transport.requests) == 3
    assert clock.sleep_calls == [0.5]


@pytest.mark.revised
def test_cache_avoids_second_call_for_same_cacheable_get() -> None:
    transport = FakeTransport(
        [
            HttpResponse(
                status_code=200,
                headers={"content-type": "text/html"},
                content=b"<html>first response</html>",
                url="https://example.org/index.html",
            ),
            HttpResponse(
                status_code=200,
                headers={"content-type": "text/html"},
                content=b"<html>second response</html>",
                url="https://example.org/index.html",
            ),
        ]
    )
    client = BookhoundHttpClient(
        config=HttpClientConfig(user_agent="BookhoundTest/1.0", timeout_seconds=5.0),
        transport=transport,
    )

    first_response = client.get("https://example.org/index.html", cache=True)
    second_response = client.get("https://example.org/index.html", cache=True)

    assert first_response.content == b"<html>first response</html>"
    assert second_response.content == b"<html>first response</html>"
    assert len(transport.requests) == 1
