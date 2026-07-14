from dataclasses import dataclass, field
from typing import Protocol

from bookhound.models import (
    DownloadRecord,
    DownloadStatus,
    LicenseDecision,
    LicenseEvidence,
    LicenseStatus,
    PersistedDownloadCandidate,
    PersistedLicenseEvidence,
    RawCandidate,
)


class LicenseClassifierProtocol(Protocol):
    def classify(
        self,
        *,
        document_url: str,
        evidence: list[LicenseEvidence],
    ) -> LicenseDecision:
        raise NotImplementedError


class DownloadServiceProtocol(Protocol):
    def download(
        self,
        *,
        document_id: int,
        document_url_id: int,
        url: str,
        license_decision: LicenseDecision,
        license_evidence_id: int | None = None,
        interactive: bool = False,
    ) -> DownloadRecord:
        raise NotImplementedError


class UnknownLicensePrompt(Protocol):
    def confirm_unknown_license(self, decision: LicenseDecision) -> bool:
        raise NotImplementedError


@dataclass(frozen=True)
class DownloadFailure:
    url: str
    title: str
    document_id: int | None
    document_url_id: int | None
    error: str


@dataclass(frozen=True)
class DownloadSummary:
    downloaded: int = 0
    blocked: int = 0
    pending: int = 0
    failed: int = 0
    failures: list[DownloadFailure] = field(default_factory=list, compare=False)


class DownloadWorkflowService:
    def __init__(
        self,
        *,
        classifier: LicenseClassifierProtocol,
        service: DownloadServiceProtocol,
        prompt: UnknownLicensePrompt,
    ) -> None:
        self.classifier = classifier
        self.service = service
        self.prompt = prompt

    def run(
        self,
        candidates: list[PersistedDownloadCandidate | RawCandidate],
    ) -> DownloadSummary:
        summary = DownloadSummary()
        for candidate in candidates:
            raw_candidate = _raw_candidate(candidate)
            evidence = _candidate_license_evidence(candidate)
            decision = self.classifier.classify(
                document_url=raw_candidate.url,
                evidence=evidence,
            )
            license_evidence_id = _decision_license_evidence_id(
                decision,
                candidate,
            )
            if decision.status is LicenseStatus.DENIED:
                summary = _increment_download_summary(summary, blocked=1)
                continue
            if decision.status is LicenseStatus.UNKNOWN:
                if not self.prompt.confirm_unknown_license(decision):
                    summary = _increment_download_summary(summary, pending=1)
                    continue
                decision = decision.model_copy(
                    update={"unknown_license_confirmed": True}
                )
                interactive = True
            else:
                interactive = False

            try:
                record = self.service.download(
                    document_id=_candidate_document_id(candidate),
                    document_url_id=_candidate_document_url_id(candidate),
                    url=raw_candidate.url,
                    license_decision=decision,
                    license_evidence_id=license_evidence_id,
                    interactive=interactive,
                )
            except Exception as error:
                summary = _increment_download_summary(
                    summary,
                    failed=1,
                    failures=[_download_failure(candidate, error)],
                )
                continue

            summary = _summary_for_download_record(summary, record)

        return summary


def _raw_candidate(
    candidate: PersistedDownloadCandidate | RawCandidate,
) -> RawCandidate:
    if isinstance(candidate, PersistedDownloadCandidate):
        return candidate.candidate
    return candidate


def _candidate_license_evidence(
    candidate: PersistedDownloadCandidate | RawCandidate,
) -> list[LicenseEvidence]:
    if isinstance(candidate, PersistedDownloadCandidate):
        return [entry.evidence for entry in candidate.license_evidence]

    evidence_entries = _candidate_license_evidence_entries(candidate)
    return [
        entry["evidence"]
        for entry in evidence_entries
        if isinstance(entry.get("evidence"), LicenseEvidence)
    ]


def _candidate_document_id(
    candidate: PersistedDownloadCandidate | RawCandidate,
) -> int:
    if isinstance(candidate, PersistedDownloadCandidate):
        return candidate.document_id
    return int(candidate.metadata.get("document_id", 0))


def _candidate_document_url_id(
    candidate: PersistedDownloadCandidate | RawCandidate,
) -> int:
    if isinstance(candidate, PersistedDownloadCandidate):
        return candidate.document_url_id
    return int(candidate.metadata.get("document_url_id", 0))


def _candidate_license_evidence_entries(
    candidate: RawCandidate,
) -> list[dict[str, object]]:
    entries = candidate.metadata.get("license_evidence", [])
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _decision_license_evidence_id(
    decision: LicenseDecision,
    candidate: PersistedDownloadCandidate | RawCandidate,
) -> int | None:
    if not decision.evidence:
        return None

    if isinstance(candidate, PersistedDownloadCandidate):
        return _decision_persisted_license_evidence_id(
            decision,
            candidate.license_evidence,
        )

    decision_evidence = decision.evidence[0]
    evidence_entries = _candidate_license_evidence_entries(candidate)
    for entry in evidence_entries:
        evidence = entry.get("evidence")
        if evidence is decision_evidence or evidence == decision_evidence:
            evidence_id = entry.get("id")
            return int(evidence_id) if isinstance(evidence_id, int) else None

    return None


def _decision_persisted_license_evidence_id(
    decision: LicenseDecision,
    evidence_entries: list[PersistedLicenseEvidence],
) -> int | None:
    decision_evidence = decision.evidence[0]
    for entry in evidence_entries:
        if entry.evidence is decision_evidence or entry.evidence == decision_evidence:
            return entry.id
    return None


def _summary_for_download_record(
    summary: DownloadSummary,
    record: DownloadRecord,
) -> DownloadSummary:
    if record.status is DownloadStatus.DOWNLOADED:
        return _increment_download_summary(summary, downloaded=1)
    if record.status is DownloadStatus.BLOCKED:
        return _increment_download_summary(summary, blocked=1)
    if record.status is DownloadStatus.FAILED:
        return _increment_download_summary(summary, failed=1)
    return _increment_download_summary(summary, pending=1)


def _increment_download_summary(
    summary: DownloadSummary,
    *,
    downloaded: int = 0,
    blocked: int = 0,
    pending: int = 0,
    failed: int = 0,
    failures: list[DownloadFailure] | None = None,
) -> DownloadSummary:
    return DownloadSummary(
        downloaded=summary.downloaded + downloaded,
        blocked=summary.blocked + blocked,
        pending=summary.pending + pending,
        failed=summary.failed + failed,
        failures=[*summary.failures, *(failures or [])],
    )


def _download_failure(
    candidate: PersistedDownloadCandidate | RawCandidate,
    error: Exception,
) -> DownloadFailure:
    raw_candidate = _raw_candidate(candidate)
    document_id = _candidate_document_id(candidate)
    document_url_id = _candidate_document_url_id(candidate)
    return DownloadFailure(
        url=raw_candidate.url,
        title=raw_candidate.title,
        document_id=document_id if document_id > 0 else None,
        document_url_id=document_url_id if document_url_id > 0 else None,
        error=str(error),
    )
