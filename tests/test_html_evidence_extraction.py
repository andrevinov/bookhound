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
