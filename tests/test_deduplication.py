import pytest

from bookhound.deduplication import compare_documents
from bookhound.models import Document

@pytest.mark.revised
def test_same_doi_merges_documents_with_high_confidence() -> None:
    result = compare_documents(
        Document(
            title="Machine Learning Notes",
            authors=["Ada Lovelace"],
            doi="10.1234/bookhound",
        ),
        Document(
            title="Updated Machine Learning Notes",
            authors=["Alan Turing"],
            doi="10.1234/bookhound",
        ),
    )

    assert result.should_merge is True
    assert result.confidence == 1.0
    assert result.reason == "same_doi"


@pytest.mark.revised
def test_same_isbn_merges_documents_with_high_confidence() -> None:
    result = compare_documents(
        Document(title="Statistics Textbook", isbn="9780000000001"),
        Document(title="Statistics Textbook Revised", isbn="9780000000001"),
    )

    assert result.should_merge is True
    assert result.confidence == 1.0
    assert result.reason == "same_isbn"


@pytest.mark.revised
def test_same_canonical_url_merges_candidates() -> None:
    result = compare_documents(
        Document(title="Open Access Report"),
        Document(title="Open Access Report"),
        left_canonical_url="https://example.org/report.pdf",
        right_canonical_url="https://example.org/report.pdf",
    )

    assert result.should_merge is True
    assert result.confidence == 0.95
    assert result.reason == "same_canonical_url"


@pytest.mark.revised
def test_similar_title_without_authors_does_not_merge_aggressively() -> None:
    result = compare_documents(
        Document(title="Introduction to Machine Learning"),
        Document(title="Introduction to Machine Learning Notes"),
    )

    assert result.should_merge is False
    assert result.confidence < 0.8
    assert result.reason == "insufficient_evidence"


@pytest.mark.revised
def test_same_title_authors_and_year_merges_as_fallback() -> None:
    result = compare_documents(
        Document(
            title="Introduction to Machine Learning",
            authors=["Ada Lovelace", "Alan Turing"],
            year=2026,
        ),
        Document(
            title="introduction to machine learning",
            authors=["Alan Turing", "Ada Lovelace"],
            year=2026,
        ),
    )

    assert result.should_merge is True
    assert result.confidence == 0.85
    assert result.reason == "same_title_authors_year"


@pytest.mark.revised
def test_same_hash_after_download_merges_documents_even_with_different_urls() -> None:
    result = compare_documents(
        Document(title="First discovered title"),
        Document(title="Second discovered title"),
        left_canonical_url="https://example.org/first.pdf",
        right_canonical_url="https://mirror.example.net/second.pdf",
        left_sha256="a" * 64,
        right_sha256="a" * 64,
    )

    assert result.should_merge is True
    assert result.confidence == 1.0
    assert result.reason == "same_sha256"


@pytest.mark.revised
def test_conflicting_strong_identifiers_do_not_merge() -> None:
    result = compare_documents(
        Document(title="Same Title", doi="10.1234/one", isbn="9780000000001"),
        Document(title="Same Title", doi="10.1234/two", isbn="9780000000002"),
    )

    assert result.should_merge is False
    assert result.confidence == 0.0
    assert result.reason == "conflicting_identifiers"
