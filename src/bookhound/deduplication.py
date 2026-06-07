from dataclasses import dataclass
import re

from bookhound.models import Document


@dataclass(frozen=True)
class DeduplicationResult:
    should_merge: bool
    confidence: float
    reason: str


def compare_documents(
    left: Document,
    right: Document,
    *,
    left_canonical_url: str | None = None,
    right_canonical_url: str | None = None,
    left_sha256: str | None = None,
    right_sha256: str | None = None,
) -> DeduplicationResult:
    if _has_conflicting_identifiers(left, right):
        return DeduplicationResult(
            should_merge=False,
            confidence=0.0,
            reason="conflicting_identifiers",
        )

    if left_sha256 and right_sha256 and left_sha256 == right_sha256:
        return DeduplicationResult(
            should_merge=True,
            confidence=1.0,
            reason="same_sha256",
        )

    if left.doi and right.doi and _normalize_identifier(left.doi) == _normalize_identifier(right.doi):
        return DeduplicationResult(
            should_merge=True,
            confidence=1.0,
            reason="same_doi",
        )

    if left.isbn and right.isbn and _normalize_identifier(left.isbn) == _normalize_identifier(right.isbn):
        return DeduplicationResult(
            should_merge=True,
            confidence=1.0,
            reason="same_isbn",
        )

    if left_canonical_url and right_canonical_url and left_canonical_url == right_canonical_url:
        return DeduplicationResult(
            should_merge=True,
            confidence=0.95,
            reason="same_canonical_url",
        )

    if _has_same_title_authors_year(left, right):
        return DeduplicationResult(
            should_merge=True,
            confidence=0.85,
            reason="same_title_authors_year",
        )

    return DeduplicationResult(
        should_merge=False,
        confidence=0.0,
        reason="insufficient_evidence",
    )


def _has_conflicting_identifiers(left: Document, right: Document) -> bool:
    return _conflicts(left.doi, right.doi) or _conflicts(left.isbn, right.isbn)


def _conflicts(left: str | None, right: str | None) -> bool:
    return bool(left and right and _normalize_identifier(left) != _normalize_identifier(right))


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[\s-]+", "", value).lower()


def _has_same_title_authors_year(left: Document, right: Document) -> bool:
    return (
        bool(left.authors)
        and bool(right.authors)
        and left.year is not None
        and right.year is not None
        and left.year == right.year
        and _normalize_title(left.title) == _normalize_title(right.title)
        and _normalize_authors(left.authors) == _normalize_authors(right.authors)
    )


def _normalize_title(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.lower()))


def _normalize_authors(authors: list[str]) -> tuple[str, ...]:
    return tuple(sorted(_normalize_title(author) for author in authors))
