from dataclasses import dataclass, field
import re

from bookhound.models import RawCandidate, SourceKind
from bookhound.url_normalization import is_direct_pdf_url


@dataclass(frozen=True)
class RelevanceScoringConfig:
    source_trust: dict[SourceKind, float] = field(default_factory=dict)
    default_source_trust: float = 0.0
    adapter_score_weight: float = 0.70
    direct_pdf_boost: float = 0.08
    title_keyword_match_boost: float = 0.08
    snippet_keyword_match_boost: float = 0.04
    metadata_completeness_boost: float = 0.03


class RelevanceScorer:
    def __init__(self, config: RelevanceScoringConfig | None = None) -> None:
        self.config = config or RelevanceScoringConfig()

    def score(self, candidate: RawCandidate, *, keyword: str) -> RawCandidate:
        adapter_score = _adapter_score(candidate)
        final_score = adapter_score * self.config.adapter_score_weight
        signals = ["adapter_score"]

        source_trust = self.config.source_trust.get(
            candidate.source,
            self.config.default_source_trust,
        )
        if source_trust:
            final_score += source_trust
            signals.append("source_trust")

        if _is_direct_pdf(candidate.url):
            final_score += self.config.direct_pdf_boost
            signals.append("direct_pdf_url")

        keyword_terms = _keyword_terms(keyword)
        if _contains_all_terms(candidate.title, keyword_terms):
            final_score += self.config.title_keyword_match_boost
            signals.append("title_keyword_match")

        if candidate.snippet and _contains_all_terms(candidate.snippet, keyword_terms):
            final_score += self.config.snippet_keyword_match_boost
            signals.append("snippet_keyword_match")

        completeness_signals = _metadata_completeness_signals(candidate)
        if completeness_signals:
            final_score += self.config.metadata_completeness_boost
            signals.append("metadata_completeness")

        clamped_score = _clamp_score(final_score)
        metadata = {
            **candidate.metadata,
            "relevance_score": {
                "adapter_score": adapter_score,
                "source_trust": source_trust,
                "signals": signals,
                "metadata_signals": completeness_signals,
                "final_score": clamped_score,
            },
        }

        return candidate.model_copy(
            update={
                "score": clamped_score,
                "metadata": metadata,
            }
        )

    def rank(self, candidates: list[RawCandidate], *, keyword: str) -> list[RawCandidate]:
        scored_candidates = [
            self.score(candidate, keyword=keyword)
            for candidate in candidates
        ]
        return sorted(scored_candidates, key=_sort_key)


def _adapter_score(candidate: RawCandidate) -> float:
    if candidate.adapter_score is not None:
        return candidate.adapter_score
    if candidate.score is not None:
        return candidate.score
    return 0.0


def _is_direct_pdf(url: str) -> bool:
    try:
        return is_direct_pdf_url(url)
    except ValueError:
        return False


def _keyword_terms(keyword: str) -> list[str]:
    return re.findall(r"\w+", keyword.lower())


def _contains_all_terms(value: str, terms: list[str]) -> bool:
    if not terms:
        return False

    normalized_value = value.lower()
    return all(term in normalized_value for term in terms)


def _metadata_completeness_signals(candidate: RawCandidate) -> list[str]:
    return [
        key
        for key in ("doi", "isbn", "year")
        if candidate.metadata.get(key)
    ]


def _clamp_score(score: float) -> float:
    return max(0.0, min(1.0, round(score, 6)))


def _sort_key(candidate: RawCandidate) -> tuple[float, str, str]:
    score = candidate.score if candidate.score is not None else 0.0
    return (-score, candidate.source.value, candidate.url)
