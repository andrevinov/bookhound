import pytest

from bookhound.license_classifier import LicenseClassifier, LicensePolicyConfig
from bookhound.models import LicenseEvidence, LicenseStatus


@pytest.mark.revised
def test_permissive_creative_commons_license_becomes_allowed() -> None:
    classifier = LicenseClassifier()
    evidence = _evidence(
        source="landing_page",
        evidence_type="license_url",
        value="https://creativecommons.org/licenses/by/4.0/",
    )

    decision = classifier.classify(
        document_url="https://example.org/report.pdf",
        evidence=[evidence],
    )

    assert decision.status is LicenseStatus.ALLOWED
    assert decision.evidence == [evidence]
    assert "Creative Commons" in decision.reason


@pytest.mark.revised
def test_configured_permissive_license_value_becomes_allowed() -> None:
    classifier = LicenseClassifier(
        LicensePolicyConfig(
            permissive_licenses=[
                "https://licenses.example.org/reuse-with-attribution"
            ],
        )
    )
    evidence = _evidence(
        source="repository_metadata",
        evidence_type="license_url",
        value="https://licenses.example.org/reuse-with-attribution",
    )

    decision = classifier.classify(
        document_url="https://example.org/custom-license.pdf",
        evidence=[evidence],
    )

    assert decision.status is LicenseStatus.ALLOWED
    assert decision.evidence == [evidence]


@pytest.mark.revised
def test_paywall_or_restricted_access_evidence_becomes_denied() -> None:
    classifier = LicenseClassifier()
    evidence = _evidence(
        source="landing_page",
        evidence_type="access_notice",
        value="PDF access is restricted to subscribers behind a paywall.",
    )

    decision = classifier.classify(
        document_url="https://publisher.example.org/restricted.pdf",
        evidence=[evidence],
    )

    assert decision.status is LicenseStatus.DENIED
    assert decision.evidence == [evidence]
    assert "restricted" in decision.reason.lower()


@pytest.mark.revised
def test_missing_evidence_becomes_unknown() -> None:
    classifier = LicenseClassifier()

    decision = classifier.classify(
        document_url="https://example.org/unknown.pdf",
        evidence=[],
    )

    assert decision.status is LicenseStatus.UNKNOWN
    assert decision.evidence == []
    assert "not enough evidence" in decision.reason.lower()


@pytest.mark.revised
def test_user_recorded_explicit_permission_becomes_manually_authorized() -> None:
    classifier = LicenseClassifier()
    evidence = _evidence(
        source="user",
        evidence_type="manual_authorization",
        value=(
            "Repository owner granted explicit permission by email on "
            "2026-06-07 for https://example.org/manual.pdf."
        ),
        suggested_status=LicenseStatus.MANUALLY_AUTHORIZED,
        confidence=1.0,
    )

    decision = classifier.classify(
        document_url="https://example.org/manual.pdf",
        evidence=[evidence],
    )

    assert decision.status is LicenseStatus.MANUALLY_AUTHORIZED
    assert decision.evidence == [evidence]
    assert "explicit permission" in decision.reason.lower()


@pytest.mark.revised
def test_configured_trusted_repository_domain_becomes_allowed() -> None:
    classifier = LicenseClassifier(
        LicensePolicyConfig(trusted_domains=["open-repository.example.org"])
    )

    decision = classifier.classify(
        document_url="https://open-repository.example.org/books/report.pdf",
        evidence=[],
    )

    assert decision.status is LicenseStatus.ALLOWED
    assert len(decision.evidence) == 1
    assert decision.evidence[0].source == "license_policy"
    assert decision.evidence[0].evidence_type == "trusted_domain"
    assert decision.evidence[0].value == "open-repository.example.org"
    assert decision.evidence[0].suggested_status is LicenseStatus.ALLOWED


@pytest.mark.revised
def test_decision_contains_reason_and_evidence_used() -> None:
    classifier = LicenseClassifier()
    evidence = _evidence(
        source="unpaywall",
        evidence_type="api_license",
        value="cc-by",
        confidence=0.9,
    )

    decision = classifier.classify(
        document_url="https://example.org/evidence.pdf",
        evidence=[evidence],
    )

    assert decision.reason
    assert decision.evidence == [evidence]


def _evidence(
    *,
    source: str,
    evidence_type: str,
    value: str,
    suggested_status: LicenseStatus = LicenseStatus.UNKNOWN,
    confidence: float = 0.8,
) -> LicenseEvidence:
    return LicenseEvidence(
        source=source,
        evidence_type=evidence_type,
        value=value,
        suggested_status=suggested_status,
        confidence=confidence,
    )
