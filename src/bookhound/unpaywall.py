from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any
from urllib.parse import quote, urlencode

from pydantic import BaseModel, field_validator

from bookhound.http_client import BookhoundHttpClient, HttpClientConfig, HttpClientProtocol
from bookhound.models import (
    DiscoveryMethod,
    LicenseEvidence,
    LicenseStatus,
    RawCandidate,
    SourceKind,
)
from bookhound.sources import SourceAdapter, SourceAvailabilityError


UNPAYWALL_API_BASE_URL = "https://api.unpaywall.org/v2"
PERMISSIVE_LICENSE_PREFIXES = (
    "cc-",
    "creative commons",
    "https://creativecommons.org/",
)


class UnpaywallAdapterConfig(BaseModel):
    email: str
    request_timeout_seconds: float = 30.0
    user_agent: str = "Bookhound/0.1.0"

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("email is required for Unpaywall lookups.")
        return value


@dataclass(frozen=True)
class UnpaywallEnrichmentResult:
    candidate: RawCandidate | None
    evidence: list[LicenseEvidence] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


class UnpaywallAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        http_client: HttpClientProtocol | None = None,
        config: UnpaywallAdapterConfig,
    ) -> None:
        super().__init__(
            source=SourceKind.UNPAYWALL,
            discovery_method=DiscoveryMethod.ENRICHMENT,
        )
        self.config = config
        self.http_client = http_client or BookhoundHttpClient(
            HttpClientConfig(
                user_agent=self.config.user_agent,
                timeout_seconds=self.config.request_timeout_seconds,
            )
        )

    def search(self, query: str) -> list[RawCandidate]:
        result = self.enrich_doi(query)
        if result.candidate is None:
            return []
        return [result.candidate]

    def enrich_doi(self, doi: str) -> UnpaywallEnrichmentResult:
        response = self.http_client.get(
            _lookup_url(doi=doi, email=self.config.email),
            rate_limit_key=self.rate_limit_key,
        )
        if not 200 <= response.status_code < 300:
            raise SourceAvailabilityError(
                SourceKind.UNPAYWALL,
                f"Unpaywall API returned HTTP {response.status_code}.",
            )

        record = json.loads(response.content.decode("utf-8"))
        metadata = _record_metadata(record)
        location = record.get("best_oa_location")
        if not isinstance(location, dict):
            return UnpaywallEnrichmentResult(
                candidate=None,
                evidence=[],
                metadata=metadata,
            )

        candidate = _candidate_from_record(record, location, doi=doi)
        evidence = [_license_evidence(location.get("license"))]
        return UnpaywallEnrichmentResult(
            candidate=candidate,
            evidence=evidence,
            metadata=metadata,
        )


def _lookup_url(*, doi: str, email: str) -> str:
    encoded_doi = quote(doi, safe="")
    return f"{UNPAYWALL_API_BASE_URL}/{encoded_doi}?{urlencode({'email': email})}"


def _record_metadata(record: dict[str, Any]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for key in ("doi", "title", "year", "is_oa", "oa_status"):
        value = record.get(key)
        if value is not None:
            metadata[key] = value
    return metadata


def _candidate_from_record(
    record: dict[str, Any],
    location: dict[str, Any],
    *,
    doi: str,
) -> RawCandidate:
    pdf_url = _first_string(
        location.get("url_for_pdf"),
        location.get("url"),
    )
    landing_page_url = _first_string(
        location.get("url_for_landing_page"),
        location.get("url"),
    )
    license_value = location.get("license")
    host_type = location.get("host_type")

    metadata = _record_metadata(record)
    metadata.update(
        {
            "landing_page_url": landing_page_url,
            "host_type": host_type,
            "license": license_value,
        }
    )

    return RawCandidate(
        title=_first_string(record.get("title"), f"Unpaywall record for {doi}"),
        url=pdf_url,
        source=SourceKind.UNPAYWALL,
        discovery_method=DiscoveryMethod.ENRICHMENT,
        query=doi,
        score=1.0,
        metadata=metadata,
    )


def _license_evidence(value: object) -> LicenseEvidence:
    license_value = value if isinstance(value, str) and value.strip() else "unknown"
    return LicenseEvidence(
        source="unpaywall",
        evidence_type="api_license",
        value=license_value,
        suggested_status=_suggested_status_for_license(license_value),
        confidence=0.9 if license_value != "unknown" else 0.3,
    )


def _suggested_status_for_license(value: str) -> LicenseStatus:
    normalized = value.strip().lower()
    if any(normalized.startswith(prefix) for prefix in PERMISSIVE_LICENSE_PREFIXES):
        return LicenseStatus.ALLOWED
    return LicenseStatus.UNKNOWN


def _first_string(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return ""
