from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

from bookhound.models import LicenseEvidence, LicenseStatus
from bookhound.url_normalization import is_direct_pdf_url


LICENSE_META_NAMES = {
    "citation_license",
    "dc.rights",
    "dcterms.license",
    "dcterms.rights",
    "license",
    "schema:license",
}
DOI_META_NAMES = {
    "citation_doi",
    "dc.identifier",
    "dc.identifier.doi",
    "doi",
}
TITLE_META_NAMES = {
    "citation_title",
    "dc.title",
    "dcterms.title",
}
AUTHOR_META_NAMES = {
    "citation_author",
    "dc.creator",
    "dcterms.creator",
}
DATE_META_NAMES = {
    "citation_publication_date",
    "citation_date",
    "dc.date",
    "dcterms.date",
}


@dataclass(frozen=True)
class HtmlEvidenceResult:
    evidence: list[LicenseEvidence] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


def extract_html_evidence(html: str, *, page_url: str) -> HtmlEvidenceResult:
    parser = _EvidenceHtmlParser()
    parser.feed(html)

    evidence = _extract_license_meta_evidence(parser.meta_tags)
    near_pdf_evidence = _extract_near_pdf_link_evidence(parser, page_url)
    if near_pdf_evidence is not None:
        evidence.append(near_pdf_evidence)

    return HtmlEvidenceResult(
        evidence=evidence,
        metadata=_extract_metadata(parser.meta_tags),
    )


class _EvidenceHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta_tags: list[dict[str, str]] = []
        self.links: list[str] = []
        self.text_chunks: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {
            name.lower(): value.strip()
            for name, value in attrs
            if value is not None
        }
        if tag.lower() == "meta":
            self.meta_tags.append(attributes)
        if tag.lower() == "a":
            href = attributes.get("href")
            if href:
                self.links.append(href)

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.text_chunks.append(text)

    @property
    def normalized_text(self) -> str:
        return " ".join(" ".join(self.text_chunks).split())


def _extract_license_meta_evidence(
    meta_tags: list[dict[str, str]],
) -> list[LicenseEvidence]:
    evidence: list[LicenseEvidence] = []
    for tag in meta_tags:
        meta_name = _meta_name(tag)
        content = tag.get("content", "").strip()
        if not content or meta_name not in LICENSE_META_NAMES:
            continue

        evidence.append(
            LicenseEvidence(
                source="html",
                evidence_type="license_meta",
                value=content,
                suggested_status=_license_status_from_text(content),
                confidence=0.8,
            )
        )
    return evidence


def _extract_near_pdf_link_evidence(
    parser: _EvidenceHtmlParser,
    page_url: str,
) -> LicenseEvidence | None:
    if not parser.normalized_text:
        return None
    if not _has_pdf_link(parser.links, page_url):
        return None
    if _license_status_from_text(parser.normalized_text) is not LicenseStatus.ALLOWED:
        return None

    return LicenseEvidence(
        source="html",
        evidence_type="near_pdf_link_text",
        value=parser.normalized_text,
        suggested_status=LicenseStatus.ALLOWED,
        confidence=0.6,
    )


def _extract_metadata(meta_tags: list[dict[str, str]]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    authors: list[str] = []

    for tag in meta_tags:
        meta_name = _meta_name(tag)
        content = tag.get("content", "").strip()
        if not content:
            continue

        if meta_name in DOI_META_NAMES and "doi" not in metadata:
            metadata["doi"] = _normalize_doi(content)
        elif meta_name in TITLE_META_NAMES and "title" not in metadata:
            metadata["title"] = content
        elif meta_name in AUTHOR_META_NAMES:
            authors.append(content)
        elif meta_name in DATE_META_NAMES and "date" not in metadata:
            metadata["date"] = content

    if authors:
        metadata["authors"] = authors

    return metadata


def _meta_name(tag: dict[str, str]) -> str:
    return (
        tag.get("name")
        or tag.get("property")
        or tag.get("itemprop")
        or ""
    ).strip().lower()


def _license_status_from_text(text: str) -> LicenseStatus:
    normalized = text.lower()
    if "creativecommons.org" in normalized or "creative commons" in normalized:
        return LicenseStatus.ALLOWED
    return LicenseStatus.UNKNOWN


def _has_pdf_link(links: list[str], page_url: str) -> bool:
    for href in links:
        absolute_url = urljoin(page_url, href)
        try:
            if is_direct_pdf_url(absolute_url):
                return True
        except ValueError:
            continue
    return False


def _normalize_doi(value: str) -> str:
    stripped = value.strip()
    if stripped.lower().startswith("doi:"):
        return stripped[4:].strip()
    return stripped
