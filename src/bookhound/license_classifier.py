from dataclasses import dataclass, field
from urllib.parse import urlsplit

from bookhound.models import LicenseDecision, LicenseEvidence, LicenseStatus


DEFAULT_PERMISSIVE_LICENSES = [
    "cc-by",
    "cc-by-sa",
    "cc0",
    "https://creativecommons.org/licenses/by/",
    "https://creativecommons.org/licenses/by-sa/",
    "https://creativecommons.org/publicdomain/zero/",
]

DENIED_SIGNALS = {
    "all rights reserved",
    "do not distribute",
    "login required",
    "paywall",
    "restricted",
    "subscriber",
    "subscription",
}


@dataclass(frozen=True)
class LicensePolicyConfig:
    permissive_licenses: list[str] = field(
        default_factory=lambda: list(DEFAULT_PERMISSIVE_LICENSES)
    )
    trusted_domains: list[str] = field(default_factory=list)


class LicenseClassifier:
    def __init__(self, config: LicensePolicyConfig | None = None) -> None:
        self.config = config or LicensePolicyConfig()

    def classify(
        self,
        *,
        document_url: str,
        evidence: list[LicenseEvidence],
    ) -> LicenseDecision:
        manual_evidence = _first_manual_authorization(evidence)
        if manual_evidence is not None:
            return LicenseDecision(
                status=LicenseStatus.MANUALLY_AUTHORIZED,
                reason="The user recorded explicit permission for this document.",
                evidence=[manual_evidence],
            )

        denied_evidence = _first_denied_evidence(evidence)
        if denied_evidence is not None:
            return LicenseDecision(
                status=LicenseStatus.DENIED,
                reason=(
                    "The available evidence indicates restricted access or a "
                    "paywall."
                ),
                evidence=[denied_evidence],
            )

        permissive_evidence = _first_permissive_license(
            evidence,
            self.config.permissive_licenses,
        )
        if permissive_evidence is not None:
            return LicenseDecision(
                status=LicenseStatus.ALLOWED,
                reason=_permissive_reason(permissive_evidence),
                evidence=[permissive_evidence],
            )

        trusted_domain = _matching_trusted_domain(
            document_url,
            self.config.trusted_domains,
        )
        if trusted_domain is not None:
            trusted_evidence = LicenseEvidence(
                source="license_policy",
                evidence_type="trusted_domain",
                value=trusted_domain,
                suggested_status=LicenseStatus.ALLOWED,
                confidence=0.7,
            )
            return LicenseDecision(
                status=LicenseStatus.ALLOWED,
                reason=(
                    "The document URL belongs to a configured trusted "
                    "repository domain."
                ),
                evidence=[trusted_evidence],
            )

        return LicenseDecision(
            status=LicenseStatus.UNKNOWN,
            reason="There is not enough evidence to decide the license status.",
            evidence=list(evidence),
        )


def _first_manual_authorization(
    evidence: list[LicenseEvidence],
) -> LicenseEvidence | None:
    for item in evidence:
        if item.suggested_status is LicenseStatus.MANUALLY_AUTHORIZED:
            return item
        if item.evidence_type == "manual_authorization":
            return item
    return None


def _first_denied_evidence(evidence: list[LicenseEvidence]) -> LicenseEvidence | None:
    for item in evidence:
        if item.suggested_status is LicenseStatus.DENIED:
            return item
        normalized_value = _normalize_signal(item.value)
        if any(signal in normalized_value for signal in DENIED_SIGNALS):
            return item
    return None


def _first_permissive_license(
    evidence: list[LicenseEvidence],
    permissive_licenses: list[str],
) -> LicenseEvidence | None:
    normalized_licenses = [
        _normalize_license(value)
        for value in permissive_licenses
        if value.strip()
    ]
    for item in evidence:
        if item.suggested_status is LicenseStatus.ALLOWED:
            return item

        normalized_value = _normalize_license(item.value)
        if any(license_value in normalized_value for license_value in normalized_licenses):
            return item

    return None


def _matching_trusted_domain(
    document_url: str,
    trusted_domains: list[str],
) -> str | None:
    parsed = urlsplit(document_url)
    hostname = parsed.hostname.lower() if parsed.hostname else ""

    for domain in trusted_domains:
        normalized_domain = domain.strip().lower()
        if not normalized_domain:
            continue
        if hostname == normalized_domain or hostname.endswith(f".{normalized_domain}"):
            return normalized_domain

    return None


def _permissive_reason(evidence: LicenseEvidence) -> str:
    if "creativecommons.org" in evidence.value.lower() or "cc-" in evidence.value.lower():
        return "The evidence declares a permissive Creative Commons license."
    return "The evidence declares a configured permissive license."


def _normalize_license(value: str) -> str:
    return value.strip().lower().rstrip("/")


def _normalize_signal(value: str) -> str:
    return " ".join(value.lower().split())
