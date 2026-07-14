# Consolidated test module. See docs/audit-tests/03-centralize-test-files.md.
# Consolidated from test_html_links.py

from bookhound.html_links import HtmlLink, parse_links


def test_parse_links_extracts_href_and_normalized_anchor_text() -> None:
    html = """
    <html>
      <body>
        <a href="/reports/climate.pdf">
          Download
          climate report
        </a>
      </body>
    </html>
    """

    links = parse_links(html)

    assert links == [
        HtmlLink(
            href="/reports/climate.pdf",
            text="Download climate report",
        )
    ]


def test_parse_links_ignores_anchors_without_href() -> None:
    html = """
    <html>
      <body>
        <a>Missing target</a>
        <a href="/reports/open.pdf">Open report</a>
      </body>
    </html>
    """

    links = parse_links(html)

    assert links == [HtmlLink(href="/reports/open.pdf", text="Open report")]


def test_parse_links_keeps_empty_text_for_unlabeled_links() -> None:
    html = """
    <html>
      <body>
        <a href="/files/raw.pdf"></a>
      </body>
    </html>
    """

    links = parse_links(html)

    assert links == [HtmlLink(href="/files/raw.pdf", text="")]


# Consolidated from test_html_evidence_extraction.py

import pytest

from bookhound.html_evidence import extract_html_evidence
from bookhound.models import LicenseEvidence, LicenseStatus


@pytest.mark.revised
def test_html_license_meta_tag_produces_license_evidence() -> None:
    html = """
    <html>
      <head>
        <meta property="schema:license"
              content="https://creativecommons.org/licenses/by/4.0/">
      </head>
      <body>
        <h1>Open Climate Policy</h1>
      </body>
    </html>
    """

    result = extract_html_evidence(
        html,
        page_url="https://repository.example.org/reports/climate",
    )

    evidence = _single_evidence(result.evidence)
    assert evidence.source == "html"
    assert evidence.evidence_type == "license_meta"
    assert evidence.value == "https://creativecommons.org/licenses/by/4.0/"
    assert evidence.suggested_status is LicenseStatus.ALLOWED


@pytest.mark.revised
def test_pdf_link_near_creative_commons_text_produces_license_evidence() -> None:
    html = """
    <html>
      <body>
        <section>
          <p>
            This report is licensed under Creative Commons Attribution 4.0
            International.
          </p>
          <a href="/files/open-report.pdf">Download PDF</a>
        </section>
      </body>
    </html>
    """

    result = extract_html_evidence(
        html,
        page_url="https://repository.example.org/reports/open-report",
    )

    evidence = _single_evidence(result.evidence)
    assert evidence.source == "html"
    assert evidence.evidence_type == "near_pdf_link_text"
    assert "Creative Commons Attribution 4.0" in evidence.value
    assert evidence.suggested_status is LicenseStatus.ALLOWED


@pytest.mark.revised
def test_html_without_metadata_does_not_crash_and_returns_empty_results() -> None:
    html = """
    <html>
      <head><title>Plain page</title></head>
      <body><p>This is a plain informational page.</p></body>
    </html>
    """

    result = extract_html_evidence(
        html,
        page_url="https://example.org/plain",
    )

    assert result.evidence == []
    assert result.metadata == {}


@pytest.mark.revised
def test_doi_title_authors_and_date_are_extracted_from_meta_tags() -> None:
    html = """
    <html>
      <head>
        <meta name="citation_doi" content="10.1234/bookhound.2026">
        <meta name="citation_title" content="Open Access Field Guide">
        <meta name="citation_author" content="Ada Lovelace">
        <meta name="citation_author" content="Alan Turing">
        <meta name="citation_publication_date" content="2026/06/15">
      </head>
      <body></body>
    </html>
    """

    result = extract_html_evidence(
        html,
        page_url="https://journal.example.org/article/123",
    )

    assert result.evidence == []
    assert result.metadata == {
        "doi": "10.1234/bookhound.2026",
        "title": "Open Access Field Guide",
        "authors": ["Ada Lovelace", "Alan Turing"],
        "date": "2026/06/15",
    }


@pytest.mark.revised
def test_dublin_core_license_and_doi_meta_tags_are_supported() -> None:
    html = """
    <html>
      <head>
        <meta name="DC.identifier" content="doi:10.5678/bookhound.dc">
        <meta name="DC.rights"
              content="Creative Commons Attribution ShareAlike 4.0">
      </head>
      <body></body>
    </html>
    """

    result = extract_html_evidence(
        html,
        page_url="https://archive.example.org/items/dc-record",
    )

    evidence = _single_evidence(result.evidence)
    assert evidence.evidence_type == "license_meta"
    assert evidence.suggested_status is LicenseStatus.ALLOWED
    assert result.metadata["doi"] == "10.5678/bookhound.dc"


def _single_evidence(evidence: list[LicenseEvidence]) -> LicenseEvidence:
    assert len(evidence) == 1
    return evidence[0]


# Consolidated from test_license_classifier.py

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
