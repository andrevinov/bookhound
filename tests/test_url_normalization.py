import pytest

from bookhound.url_normalization import canonicalize_url, is_direct_pdf_url

@pytest.mark.revised
@pytest.mark.parametrize(
    ("raw_url", "expected"),
    [
        (
            "HTTP://Example.ORG:80/reports/../reports/Report.PDF/",
            "http://example.org/reports/Report.PDF",
        ),
        (
            "https://Example.ORG:443/library//paper.pdf",
            "https://example.org/library/paper.pdf",
        ),
        (
            "https://example.org/a%20paper.pdf",
            "https://example.org/a%20paper.pdf",
        ),
        (
            "https://example.org",
            "https://example.org/",
        ),
    ],
)
def test_equivalent_urls_generate_same_canonical_form(raw_url: str, expected: str) -> None:
    assert canonicalize_url(raw_url) == expected


@pytest.mark.revised
def test_fragments_are_removed() -> None:
    assert (
        canonicalize_url("https://example.org/reports/paper.pdf#page=12")
        == "https://example.org/reports/paper.pdf"
    )


@pytest.mark.revised
def test_common_tracking_parameters_are_removed_by_default() -> None:
    assert (
        canonicalize_url(
            "https://example.org/paper.pdf?"
            "utm_source=newsletter&utm_medium=email&utm_campaign=launch&"
            "fbclid=abc123&gclid=xyz789"
        )
        == "https://example.org/paper.pdf"
    )


@pytest.mark.revised
def test_tracking_parameter_removal_can_be_disabled() -> None:
    assert (
        canonicalize_url(
            "https://example.org/paper.pdf?utm_source=newsletter",
            remove_tracking=False,
        )
        == "https://example.org/paper.pdf?utm_source=newsletter"
    )


@pytest.mark.revised
def test_urls_with_important_query_parameters_are_not_destroyed() -> None:
    assert (
        canonicalize_url(
            "https://repository.example.org/download?id=123&format=pdf&utm_source=newsletter"
        )
        == "https://repository.example.org/download?id=123&format=pdf"
    )


@pytest.mark.revised
@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/file.pdf",
        "https://example.org/file.pdf?download=1",
        "https://example.org/file.PDF#page=3",
    ],
)
def test_direct_pdf_urls_are_detected(url: str) -> None:
    assert is_direct_pdf_url(url) is True


@pytest.mark.revised
@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/download?id=123&format=pdf",
        "https://example.org/pdf-viewer",
        "https://example.org/file.pdf.html",
    ],
)
def test_non_direct_pdf_urls_are_not_detected_as_direct_pdfs(url: str) -> None:
    assert is_direct_pdf_url(url) is False


@pytest.mark.revised
@pytest.mark.parametrize("url", ["", "not a url", "ftp://example.org/file.pdf"])
def test_invalid_or_unsupported_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        canonicalize_url(url)
