from datetime import datetime, timezone
import json

import pytest

from bookhound.models import (
    Document,
    DocumentUrl,
    DownloadRecord,
    DownloadStatus,
    ExecutionMode,
    LicenseDecision,
    LicenseEvidence,
    LicenseStatus,
    RawCandidate,
    SearchQuery,
    DiscoveryMethod,
    SourceKind,
    SourceResult,
    UrlType,
)

@pytest.mark.revised
def test_domain_models_accept_valid_data() -> None:
    query = SearchQuery(
        keyword="machine learning",
        mode=ExecutionMode.SEARCH,
        variants=['"machine learning" filetype:pdf'],
        created_at=datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
    )
    candidate = RawCandidate(
        title="Machine Learning Notes",
        url="https://example.org/notes.pdf",
        source=SourceKind.COMMON_CRAWL,
        discovery_method=DiscoveryMethod.PUBLIC_INDEX,
        query=query.keyword,
        snippet="Lecture notes about machine learning.",
        score=0.87,
        discovered_at=datetime(2026, 6, 6, 12, 1, tzinfo=timezone.utc),
        metadata={"doi": "10.1234/example"},
    )
    document = Document(
        title="Machine Learning Notes",
        authors=["Ada Lovelace", "Alan Turing"],
        doi="10.1234/example",
        isbn=None,
        year=2026,
        language="en",
        metadata={"source_count": 1},
    )
    document_url = DocumentUrl(
        url="https://example.org/notes.pdf",
        canonical_url="https://example.org/notes.pdf",
        source=SourceKind.COMMON_CRAWL,
        discovery_method=DiscoveryMethod.PUBLIC_INDEX,
        url_type=UrlType.PDF,
        confidence=0.95,
        discovered_at=datetime(2026, 6, 6, 12, 2, tzinfo=timezone.utc),
    )
    evidence = LicenseEvidence(
        source="fake-source",
        evidence_type="metadata",
        value="https://creativecommons.org/licenses/by/4.0/",
        suggested_status=LicenseStatus.ALLOWED,
        confidence=0.9,
        collected_at=datetime(2026, 6, 6, 12, 3, tzinfo=timezone.utc),
    )
    decision = LicenseDecision(
        status=LicenseStatus.ALLOWED,
        reason="The source metadata declares a permissive Creative Commons license.",
        evidence=[evidence],
        decided_at=datetime(2026, 6, 6, 12, 4, tzinfo=timezone.utc),
    )
    download = DownloadRecord(
        url="https://example.org/notes.pdf",
        local_path="/tmp/bookhound/notes.pdf",
        status=DownloadStatus.DOWNLOADED,
        sha256="a" * 64,
        size_bytes=2048,
        license_decision=decision,
        downloaded_at=datetime(2026, 6, 6, 12, 5, tzinfo=timezone.utc),
    )
    source_result = SourceResult(
        source=SourceKind.COMMON_CRAWL,
        discovery_method=DiscoveryMethod.PUBLIC_INDEX,
        candidates=[candidate],
        errors=[],
        fetched_at=datetime(2026, 6, 6, 12, 6, tzinfo=timezone.utc),
    )

    assert query.mode is ExecutionMode.SEARCH
    assert candidate.metadata["doi"] == document.doi
    assert candidate.source is SourceKind.COMMON_CRAWL
    assert candidate.discovery_method is DiscoveryMethod.PUBLIC_INDEX
    assert document_url.url_type is UrlType.PDF
    assert document_url.source is SourceKind.COMMON_CRAWL
    assert decision.status is LicenseStatus.ALLOWED
    assert download.status is DownloadStatus.DOWNLOADED
    assert source_result.candidates == [candidate]
    assert source_result.discovery_method is DiscoveryMethod.PUBLIC_INDEX


@pytest.mark.revised
def test_domain_models_accept_expanded_discovery_sources() -> None:
    source_methods = [
        (SourceKind.GOOGLE, DiscoveryMethod.API),
        (SourceKind.ARXIV, DiscoveryMethod.API),
        (SourceKind.UNPAYWALL, DiscoveryMethod.ENRICHMENT),
        (SourceKind.COMMON_CRAWL, DiscoveryMethod.PUBLIC_INDEX),
        (SourceKind.SEED_CRAWLER, DiscoveryMethod.CRAWL),
        (SourceKind.SITEMAP, DiscoveryMethod.SITEMAP),
        (SourceKind.LINK_EXPANSION, DiscoveryMethod.LINK_EXPANSION),
    ]

    for source, discovery_method in source_methods:
        candidate = RawCandidate(
            title=f"{source.value} result",
            url=f"https://example.org/{source.value}.pdf",
            source=source,
            discovery_method=discovery_method,
            query="machine learning",
        )
        source_result = SourceResult(
            source=source,
            discovery_method=discovery_method,
            candidates=[candidate],
        )

        assert candidate.source is source
        assert candidate.discovery_method is discovery_method
        assert source_result.source is source
        assert source_result.discovery_method is discovery_method


@pytest.mark.revised
@pytest.mark.parametrize(
    ("model_type", "kwargs"),
    [
        (
            RawCandidate,
            {
                "title": "Empty URL",
                "url": "",
                "source": SourceKind.SEED_CRAWLER,
                "discovery_method": DiscoveryMethod.CRAWL,
                "query": "empty url",
            },
        ),
        (
            DocumentUrl,
            {
                "url": "",
                "canonical_url": "https://example.org/file.pdf",
                "source": SourceKind.SEED_CRAWLER,
                "discovery_method": DiscoveryMethod.CRAWL,
                "url_type": UrlType.PDF,
            },
        ),
        (
            DownloadRecord,
            {
                "url": "",
                "local_path": "/tmp/bookhound/file.pdf",
                "status": DownloadStatus.PENDING,
            },
        ),
    ],
)
def test_url_backed_models_reject_empty_urls(model_type: type, kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        model_type(**kwargs)


@pytest.mark.revised
@pytest.mark.parametrize(
    ("model_type", "kwargs"),
    [
        (SearchQuery, {"keyword": "biology", "mode": "invalid-mode"}),
        (
            DocumentUrl,
            {
                "url": "https://example.org/file.pdf",
                "canonical_url": "https://example.org/file.pdf",
                "source": SourceKind.SITEMAP,
                "discovery_method": DiscoveryMethod.SITEMAP,
                "url_type": "invalid-url-type",
            },
        ),
        (
            RawCandidate,
            {
                "title": "Invalid source",
                "url": "https://example.org/file.pdf",
                "source": "invalid-source",
                "discovery_method": DiscoveryMethod.API,
                "query": "invalid source",
            },
        ),
        (
            SourceResult,
            {
                "source": SourceKind.GOOGLE,
                "discovery_method": "invalid-discovery-method",
                "candidates": [],
            },
        ),
        (
            LicenseDecision,
            {
                "status": "invalid-license-status",
                "reason": "Invalid status should not be accepted.",
                "evidence": [],
            },
        ),
        (
            DownloadRecord,
            {
                "url": "https://example.org/file.pdf",
                "local_path": "/tmp/bookhound/file.pdf",
                "status": "invalid-download-status",
            },
        ),
    ],
)
def test_models_reject_invalid_status_values(model_type: type, kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        model_type(**kwargs)


@pytest.mark.revised
@pytest.mark.parametrize(
    ("model_type", "kwargs"),
    [
        (
            SearchQuery,
            {
                "keyword": "history",
                "mode": ExecutionMode.SEARCH,
                "created_at": "not-a-date",
            },
        ),
        (
            RawCandidate,
            {
                "title": "Bad date",
                "url": "https://example.org/bad-date.pdf",
                "source": SourceKind.LINK_EXPANSION,
                "discovery_method": DiscoveryMethod.LINK_EXPANSION,
                "query": "history",
                "discovered_at": "not-a-date",
            },
        ),
        (
            LicenseEvidence,
            {
                "source": "fake-source",
                "evidence_type": "metadata",
                "value": "cc-by",
                "suggested_status": LicenseStatus.ALLOWED,
                "collected_at": "not-a-date",
            },
        ),
    ],
)
def test_models_reject_malformed_dates(model_type: type, kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        model_type(**kwargs)


@pytest.mark.revised
def test_model_dump_preserves_expected_json_fields() -> None:
    evidence = LicenseEvidence(
        source="unpaywall",
        evidence_type="api",
        value="cc-by",
        suggested_status=LicenseStatus.ALLOWED,
        confidence=0.8,
        collected_at=datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
    )
    decision = LicenseDecision(
        status=LicenseStatus.ALLOWED,
        reason="Unpaywall reported a permissive license.",
        evidence=[evidence],
        decided_at=datetime(2026, 6, 6, 12, 1, tzinfo=timezone.utc),
    )

    dumped = decision.model_dump(mode="json")
    dumped_json = json.loads(decision.model_dump_json())

    assert dumped == dumped_json
    assert dumped["status"] == "allowed"
    assert dumped["reason"] == "Unpaywall reported a permissive license."
    assert dumped["evidence"][0]["source"] == "unpaywall"
    assert dumped["evidence"][0]["suggested_status"] == "allowed"
    assert dumped["decided_at"] == "2026-06-06T12:01:00Z"
