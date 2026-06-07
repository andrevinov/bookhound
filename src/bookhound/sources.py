from abc import ABC, abstractmethod
from datetime import datetime, timezone

from bookhound.models import DiscoveryMethod, RawCandidate, SourceKind, SourceResult


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
        self.reason = reason

    @property
    def enabled(self) -> bool:
        return False

    def search(self, query: str) -> list[RawCandidate]:
        return []


def run_source_search(adapter: SourceAdapter, query: str) -> SourceResult:
    if not adapter.enabled:
        reason = adapter.reason.rstrip(".")
        return _empty_result(
            adapter,
            errors=[f"Source {adapter.source_name.value} is disabled: {reason}."],
        )

    try:
        candidates = adapter.search(query)
    except SourceError as error:
        return _empty_result(adapter, errors=[error.as_result_error()])

    return SourceResult(
        source=adapter.source_name,
        discovery_method=adapter.discovery_method,
        candidates=candidates,
        errors=[],
        fetched_at=_utc_now(),
    )


def _empty_result(adapter: SourceAdapter, errors: list[str]) -> SourceResult:
    return SourceResult(
        source=adapter.source_name,
        discovery_method=adapter.discovery_method,
        candidates=[],
        errors=errors,
        fetched_at=_utc_now(),
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
