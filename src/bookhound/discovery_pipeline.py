from dataclasses import dataclass, field
import logging
import time
from typing import Protocol

from bookhound.models import RawCandidate
from bookhound.query_planner import PlannedQueryVariant, QueryPlan, QueryPlanner
from bookhound.sources import SourceAdapter, run_source_search
from bookhound.url_normalization import canonicalize_url


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveryPipelineResult:
    query_plan: QueryPlan
    candidates: list[RawCandidate]
    errors: list[str]
    events: list[dict[str, object]] = field(default_factory=list)


class LinkExpander(Protocol):
    def expand(
        self,
        existing_candidates: list[RawCandidate],
        *,
        query: str,
    ) -> list[RawCandidate]:
        raise NotImplementedError


class DiscoveryPipeline:
    def __init__(
        self,
        sources: list[SourceAdapter],
        link_expander: LinkExpander | None = None,
        query_planner: QueryPlanner | None = None,
    ) -> None:
        self.sources = sources
        self.link_expander = link_expander
        self.query_planner = query_planner or QueryPlanner()

    def search(self, keyword: str) -> DiscoveryPipelineResult:
        started_at = time.perf_counter()
        query_plan = self.query_planner.plan_queries(keyword)
        candidates_by_canonical_url: dict[str, RawCandidate] = {}
        errors: list[str] = []
        events: list[dict[str, object]] = []
        raw_candidate_count = 0

        for variant in query_plan.variants:
            for source in self.sources:
                source_started_at = time.perf_counter()
                logger.debug(
                    "Source search started.",
                    extra={
                        "event": "discovery.source.started",
                        "keyword": query_plan.keyword,
                        "query": variant.query,
                        "query_variant_label": variant.label,
                        "source": source.source_name.value,
                        "discovery_method": source.discovery_method.value,
                    },
                )
                source_result = run_source_search(source, query=variant.query)
                raw_candidate_count += len(source_result.candidates)
                errors.extend(
                    f"{source_result.source.value}: {error}"
                    for error in source_result.errors
                )
                events.extend(source_result.events)
                for candidate in source_result.candidates:
                    _add_candidate(
                        candidates_by_canonical_url,
                        candidate,
                        variant,
                    )
                logger.debug(
                    "Source search completed.",
                    extra={
                        "event": "discovery.source.completed",
                        "keyword": query_plan.keyword,
                        "query": variant.query,
                        "query_variant_label": variant.label,
                        "source": source_result.source.value,
                        "discovery_method": source_result.discovery_method.value,
                        "candidate_count": len(source_result.candidates),
                        "error_count": len(source_result.errors),
                        "event_count": len(source_result.events),
                        "duration_ms": _duration_ms(source_started_at),
                    },
                )

            if self.link_expander is not None:
                expanded_candidates = self.link_expander.expand(
                    list(candidates_by_canonical_url.values()),
                    query=variant.query,
                )
                raw_candidate_count += len(expanded_candidates)
                for candidate in expanded_candidates:
                    _add_candidate(
                        candidates_by_canonical_url,
                        candidate,
                        variant,
                    )

        candidates = sorted(
            candidates_by_canonical_url.values(),
            key=_candidate_sort_key,
        )
        logger.info(
            "Discovery pipeline completed.",
            extra={
                "event": "discovery.pipeline.completed",
                "keyword": query_plan.keyword,
                "variant_count": len(query_plan.variants),
                "source_count": len(self.sources),
                "raw_candidate_count": raw_candidate_count,
                "candidate_count": len(candidates),
                "error_count": len(errors),
                "event_count": len(events),
                "duration_ms": _duration_ms(started_at),
            },
        )
        return DiscoveryPipelineResult(
            query_plan=query_plan,
            candidates=candidates,
            errors=errors,
            events=events,
        )


def _add_candidate(
    candidates_by_canonical_url: dict[str, RawCandidate],
    candidate: RawCandidate,
    variant: PlannedQueryVariant,
) -> None:
    enriched_candidate = _enrich_candidate(candidate, variant)
    canonical_url = enriched_candidate.metadata["canonical_url"]
    existing_candidate = candidates_by_canonical_url.get(canonical_url)
    if existing_candidate is None:
        candidates_by_canonical_url[canonical_url] = enriched_candidate
        return

    candidates_by_canonical_url[canonical_url] = _merge_candidates(
        existing_candidate,
        enriched_candidate,
    )


def _enrich_candidate(
    candidate: RawCandidate,
    variant: PlannedQueryVariant,
) -> RawCandidate:
    occurrence = _source_occurrence(candidate, variant)
    metadata = {
        **candidate.metadata,
        "canonical_url": canonicalize_url(candidate.url),
        "query_variant_label": variant.label,
        "merged_count": 1,
        "source_occurrences": [occurrence],
    }

    return candidate.model_copy(update={"metadata": metadata})


def _merge_candidates(left: RawCandidate, right: RawCandidate) -> RawCandidate:
    preferred_candidate = _preferred_candidate(left, right)
    merged_occurrences = [
        *left.metadata.get("source_occurrences", []),
        *right.metadata.get("source_occurrences", []),
    ]
    merged_count = int(left.metadata.get("merged_count", 1)) + int(
        right.metadata.get("merged_count", 1)
    )
    metadata = {
        **preferred_candidate.metadata,
        "canonical_url": left.metadata["canonical_url"],
        "merged_count": merged_count,
        "source_occurrences": merged_occurrences,
    }

    return preferred_candidate.model_copy(update={"metadata": metadata})


def _preferred_candidate(left: RawCandidate, right: RawCandidate) -> RawCandidate:
    if _candidate_sort_key(right) < _candidate_sort_key(left):
        return right
    return left


def _candidate_sort_key(candidate: RawCandidate) -> tuple[float, str, str]:
    score = candidate.score if candidate.score is not None else 0.0
    return (-score, candidate.source.value, candidate.url)


def _source_occurrence(
    candidate: RawCandidate,
    variant: PlannedQueryVariant,
) -> dict[str, str]:
    return {
        "source": candidate.source.value,
        "discovery_method": candidate.discovery_method.value,
        "query_variant_label": variant.label,
        "query": candidate.query,
    }


def _duration_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))
