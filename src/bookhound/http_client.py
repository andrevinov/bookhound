from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import logging
import time
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HttpClientConfig:
    user_agent: str
    timeout_seconds: float
    default_headers: Mapping[str, str] = field(default_factory=dict)
    max_retries: int = 0
    retry_backoff_seconds: float = 1.0
    rate_limit_per_second: float | None = None


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str]
    timeout_seconds: float


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes
    url: str


class HttpClientProtocol(Protocol):
    def get(
        self,
        url: str,
        *,
        rate_limit_key: str | None = None,
        cache: bool = False,
    ) -> HttpResponse:
        raise NotImplementedError


class HttpTransport(Protocol):
    def request(self, request: HttpRequest) -> HttpResponse:
        raise NotImplementedError


class HttpClientError(Exception):
    pass


class HttpTimeoutError(HttpClientError):
    def __init__(self, url: str) -> None:
        super().__init__(f"HTTP request timed out for {url}.")
        self.url = url


class HttpxTransport:
    def request(self, request: HttpRequest) -> HttpResponse:
        try:
            import httpx
        except ImportError as error:
            raise RuntimeError("httpx is required for the default HTTP transport.") from error

        try:
            response = httpx.request(
                request.method,
                request.url,
                headers=request.headers,
                timeout=request.timeout_seconds,
            )
        except httpx.TimeoutException as error:
            raise TimeoutError(str(error)) from error
        except httpx.RequestError as error:
            raise HttpClientError(f"HTTP request failed for {request.url}: {error}") from error

        return HttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            content=response.content,
            url=str(response.url),
        )


class BookhoundHttpClient:
    def __init__(
        self,
        config: HttpClientConfig,
        transport: HttpTransport | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or HttpxTransport()
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self._last_request_at_by_key: dict[str, float] = {}
        self._cache: dict[str, HttpResponse] = {}

    def get(
        self,
        url: str,
        *,
        rate_limit_key: str | None = None,
        cache: bool = False,
    ) -> HttpResponse:
        if cache and url in self._cache:
            logger.debug(
                "HTTP cache hit.",
                extra={
                    "event": "http.cache_hit",
                    "method": "GET",
                    "url": _sanitize_url(url),
                    "cache": True,
                },
            )
            return self._cache[url]

        request = HttpRequest(
            method="GET",
            url=url,
            headers=self._headers(),
            timeout_seconds=self.config.timeout_seconds,
        )
        response = self._send_with_retries(request, rate_limit_key=rate_limit_key)

        if cache and _is_cacheable_response(response):
            self._cache[url] = response

        return response

    def _send_with_retries(
        self,
        request: HttpRequest,
        *,
        rate_limit_key: str | None,
    ) -> HttpResponse:
        attempts = self.config.max_retries + 1

        for attempt_index in range(attempts):
            attempt = attempt_index + 1
            self._apply_rate_limit(rate_limit_key)
            logger.debug(
                "HTTP request started.",
                extra={
                    "event": "http.request_started",
                    "method": request.method,
                    "url": _sanitize_url(request.url),
                    "attempt": attempt,
                    "total_attempts": attempts,
                    "rate_limit_key": rate_limit_key,
                },
            )
            try:
                response = self.transport.request(request)
            except TimeoutError as error:
                logger.warning(
                    "HTTP request timed out.",
                    extra={
                        "event": "http.timeout",
                        "method": request.method,
                        "url": _sanitize_url(request.url),
                        "attempt": attempt,
                        "total_attempts": attempts,
                        "rate_limit_key": rate_limit_key,
                    },
                )
                raise HttpTimeoutError(request.url) from error

            logger.debug(
                "HTTP response received.",
                extra={
                    "event": "http.response",
                    "method": request.method,
                    "url": _sanitize_url(request.url),
                    "status_code": response.status_code,
                    "attempt": attempt,
                    "total_attempts": attempts,
                    "rate_limit_key": rate_limit_key,
                },
            )
            if not _is_transient_status(response.status_code) or attempt_index == attempts - 1:
                return response

            logger.warning(
                "HTTP request will be retried.",
                extra={
                    "event": "http.retry",
                    "method": request.method,
                    "url": _sanitize_url(request.url),
                    "status_code": response.status_code,
                    "attempt": attempt,
                    "total_attempts": attempts,
                    "rate_limit_key": rate_limit_key,
                },
            )
            self.sleep(self.config.retry_backoff_seconds)

        raise RuntimeError("HTTP retry loop exited unexpectedly.")

    def _apply_rate_limit(self, rate_limit_key: str | None) -> None:
        if (
            rate_limit_key is None
            or self.config.rate_limit_per_second is None
            or self.config.rate_limit_per_second <= 0
        ):
            return

        minimum_interval = 1.0 / self.config.rate_limit_per_second
        now = self.clock()
        last_request_at = self._last_request_at_by_key.get(rate_limit_key)

        if last_request_at is not None:
            elapsed = now - last_request_at
            wait_seconds = minimum_interval - elapsed
            if wait_seconds > 0:
                logger.debug(
                    "HTTP rate limit applied.",
                    extra={
                        "event": "http.rate_limited",
                        "rate_limit_key": rate_limit_key,
                        "wait_seconds": wait_seconds,
                    },
                )
                self.sleep(wait_seconds)
                now = self.clock()

        self._last_request_at_by_key[rate_limit_key] = now

    def _headers(self) -> dict[str, str]:
        headers = dict(self.config.default_headers)
        headers["User-Agent"] = self.config.user_agent
        return headers


def _is_transient_status(status_code: int) -> bool:
    return status_code in {408, 429, 500, 502, 503, 504}


def _is_cacheable_response(response: HttpResponse) -> bool:
    return 200 <= response.status_code < 300


def _sanitize_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
