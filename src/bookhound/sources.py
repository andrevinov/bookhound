from abc import ABC, abstractmethod
from datetime import datetime, timezone
import json
import logging
import xml.etree.ElementTree as ET

from bookhound.http_client import HttpClientError
from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind, SourceResult


logger = logging.getLogger(__name__)


class SourceError(Exception):
    error_kind = "source"

    def __init__(self, source: SourceKind, message: str) -> None:
        super().__init__(message)
        self.source = source
        self.message = message

    def as_result_error(self) -> str:
        return f"{self.error_kind}: {self.message}"


class SourceQuotaError(SourceError):
    error_kind = "quota"


class SourceAvailabilityError(SourceError):
    error_kind = "availability"


class SourceAdapter(ABC):
    def __init__(self, source: SourceKind, discovery_method: DiscoveryMethod) -> None:
        self._source = source
        self._discovery_method = discovery_method

    @property
    def enabled(self) -> bool:
        return True

    @property
    def disabled_reason(self) -> str | None:
        return None

    @property
    def source_name(self) -> SourceKind:
        return self._source

    @property
    def discovery_method(self) -> DiscoveryMethod:
        return self._discovery_method

    @property
    def rate_limit_key(self) -> str:
        return f"source:{self.source_name.value}"

    @abstractmethod
    def search(self, query: str) -> list[RawCandidate]:
        raise NotImplementedError


class FakeSourceAdapter(SourceAdapter):
    def __init__(
        self,
        source: SourceKind,
        discovery_method: DiscoveryMethod,
        candidates: list[RawCandidate],
        error: SourceError | None = None,
    ) -> None:
        super().__init__(source=source, discovery_method=discovery_method)
        self._candidates = candidates
        self._error = error

    def search(self, query: str) -> list[RawCandidate]:
        if self._error is not None:
            raise self._error

        return [
            candidate.model_copy(
                update={
                    "source": self.source_name,
                    "discovery_method": self.discovery_method,
                    "query": query,
                }
            )
            for candidate in self._candidates
        ]


class DisabledSourceAdapter(SourceAdapter):
    def __init__(
        self,
        source: SourceKind,
        discovery_method: DiscoveryMethod,
        reason: str,
    ) -> None:
        super().__init__(source=source, discovery_method=discovery_method)
        self._disabled_reason = reason

    @property
    def enabled(self) -> bool:
        return False

    @property
    def disabled_reason(self) -> str | None:
        return self._disabled_reason

    def search(self, query: str) -> list[RawCandidate]:
        return []


def run_source_search(adapter: SourceAdapter, query: str) -> SourceResult:
    if not adapter.enabled:
        reason = (adapter.disabled_reason or "No reason provided").rstrip(".")
        logger.warning(
            "Source is disabled.",
            extra={
                "event": "source.disabled",
                "source": adapter.source_name.value,
                "discovery_method": adapter.discovery_method.value,
                "query": query,
                "reason": reason,
            },
        )
        return _empty_result(
            adapter,
            errors=[f"Source {adapter.source_name.value} is disabled: {reason}."],
        )

    try:
        candidates = adapter.search(query)
    except SourceError as error:
        logger.warning(
            "Source search failed.",
            extra={
                "event": "source.search_failed",
                "source": adapter.source_name.value,
                "discovery_method": adapter.discovery_method.value,
                "query": query,
                "error_kind": error.error_kind,
                "error": error.as_result_error(),
            },
        )
        return _empty_result(
            adapter,
            errors=[error.as_result_error()],
            events=_adapter_events(adapter),
        )
    except (
        HttpClientError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ET.ParseError,
    ) as error:
        source_error = SourceAvailabilityError(
            adapter.source_name,
            _external_integration_error_message(error),
        )
        logger.warning(
            "Source search failed.",
            extra={
                "event": "source.search_failed",
                "source": adapter.source_name.value,
                "discovery_method": adapter.discovery_method.value,
                "query": query,
                "error_kind": source_error.error_kind,
                "error": source_error.as_result_error(),
            },
        )
        return _empty_result(
            adapter,
            errors=[source_error.as_result_error()],
            events=_adapter_events(adapter),
        )

    return SourceResult(
        source=adapter.source_name,
        discovery_method=adapter.discovery_method,
        candidates=candidates,
        errors=[],
        events=_adapter_events(adapter),
        fetched_at=_utc_now(),
    )


def _empty_result(
    adapter: SourceAdapter,
    errors: list[str],
    events: list[dict[str, object]] | None = None,
) -> SourceResult:
    return SourceResult(
        source=adapter.source_name,
        discovery_method=adapter.discovery_method,
        candidates=[],
        errors=errors,
        events=events or [],
        fetched_at=_utc_now(),
    )


def _adapter_events(adapter: SourceAdapter) -> list[dict[str, object]]:
    raw_events = getattr(adapter, "events", [])
    if not isinstance(raw_events, list):
        return []

    events: list[dict[str, object]] = []
    for raw_event in raw_events:
        if not isinstance(raw_event, dict):
            continue

        metadata = raw_event.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        event_type = raw_event.get("event_type")
        message = raw_event.get("message")
        events.append(
            {
                "source": adapter.source_name.value,
                "event_type": (
                    str(event_type)
                    if event_type is not None
                    else f"{adapter.source_name.value}.event"
                ),
                "message": str(message) if message is not None else None,
                "metadata": dict(metadata),
            }
        )

    return events


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _external_integration_error_message(error: Exception) -> str:
    if isinstance(error, HttpClientError):
        return f"HTTP client failure: {error}"
    if isinstance(error, json.JSONDecodeError):
        return f"Malformed JSON response: {error}"
    if isinstance(error, UnicodeDecodeError):
        return f"Invalid text response encoding: {error}"
    if isinstance(error, ET.ParseError):
        return f"Malformed XML response: {error}"
    raise AssertionError(f"Unexpected external integration error: {error!r}")
