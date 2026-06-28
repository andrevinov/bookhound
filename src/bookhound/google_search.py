from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol
from urllib.parse import urlencode

from bookhound.http_client import BookhoundHttpClient, HttpClientConfig, HttpResponse
from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind
from bookhound.sources import SourceAdapter, SourceAvailabilityError, SourceQuotaError


GOOGLE_SEARCH_API_URL = "https://www.googleapis.com/customsearch/v1"
MISSING_CREDENTIAL_REASON = "Missing Google API key or search engine ID."


class GoogleSearchHttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        rate_limit_key: str | None = None,
        cache: bool = False,
    ) -> HttpResponse:
        raise NotImplementedError


@dataclass(frozen=True)
class GoogleSearchAdapterConfig:
    api_key: str | None = None
    search_engine_id: str | None = None
    result_limit: int = 10
    request_timeout_seconds: float = 30.0
    user_agent: str = "Bookhound/0.1.0"


class GoogleSearchAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        http_client: GoogleSearchHttpClient | None = None,
        config: GoogleSearchAdapterConfig | None = None,
    ) -> None:
        super().__init__(source=SourceKind.GOOGLE, discovery_method=DiscoveryMethod.API)
        self.config = config or GoogleSearchAdapterConfig()
        self.reason = MISSING_CREDENTIAL_REASON
        self.http_client = http_client or BookhoundHttpClient(
            HttpClientConfig(
                user_agent=self.config.user_agent,
                timeout_seconds=self.config.request_timeout_seconds,
            )
        )

    @property
    def enabled(self) -> bool:
        return bool(self.config.api_key and self.config.search_engine_id)

    def search(self, query: str) -> list[RawCandidate]:
        response = self.http_client.get(
            _search_url(
                query=query,
                api_key=str(self.config.api_key),
                search_engine_id=str(self.config.search_engine_id),
                result_limit=self.config.result_limit,
            ),
            rate_limit_key=self.rate_limit_key,
        )
        if response.status_code in {403, 429}:
            raise SourceQuotaError(SourceKind.GOOGLE, "Google API quota exceeded.")
        if not 200 <= response.status_code < 300:
            raise SourceAvailabilityError(
                SourceKind.GOOGLE,
                f"Google API returned HTTP {response.status_code}.",
            )

        payload = json.loads(response.content.decode("utf-8"))
        return [
            _candidate_from_item(item, query=query)
            for item in payload.get("items", [])
            if isinstance(item, dict)
        ][: self.config.result_limit]


def _search_url(
    *,
    query: str,
    api_key: str,
    search_engine_id: str,
    result_limit: int,
) -> str:
    return (
        f"{GOOGLE_SEARCH_API_URL}?"
        + urlencode(
            {
                "q": query,
                "key": api_key,
                "cx": search_engine_id,
                "num": result_limit,
            }
        )
    )


def _candidate_from_item(item: dict[str, Any], *, query: str) -> RawCandidate:
    return RawCandidate(
        title=_string_value(item.get("title")),
        url=_string_value(item.get("link")),
        source=SourceKind.GOOGLE,
        discovery_method=DiscoveryMethod.API,
        query=query,
        snippet=_optional_string(item.get("snippet")),
        score=1.0,
        metadata={
            "display_link": _string_value(item.get("displayLink")),
            "mime": _string_value(item.get("mime")),
            "file_format": _string_value(item.get("fileFormat")),
        },
    )


def _string_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""


def _optional_string(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None
