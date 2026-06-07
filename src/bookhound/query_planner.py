from dataclasses import dataclass


@dataclass(frozen=True)
class QueryPlannerConfig:
    max_variants: int = 10
    include_filetype_pdf: bool = True
    include_ext_pdf: bool = True
    include_pdf_phrase: bool = True
    include_site_edu: bool = True
    include_site_gov: bool = True


@dataclass(frozen=True)
class PlannedQueryVariant:
    label: str
    query: str


@dataclass(frozen=True)
class QueryPlan:
    keyword: str
    variants: list[PlannedQueryVariant]


class QueryPlanner:
    def __init__(self, config: QueryPlannerConfig | None = None) -> None:
        self.config = config or QueryPlannerConfig()

    def plan_queries(self, keyword: str) -> QueryPlan:
        normalized_keyword = _normalize_keyword(keyword)
        quoted_keyword = f'"{normalized_keyword}"'
        variants = [
            PlannedQueryVariant("quoted", quoted_keyword),
        ]

        if self.config.include_filetype_pdf:
            variants.append(
                PlannedQueryVariant("filetype_pdf", f"{quoted_keyword} filetype:pdf")
            )
        if self.config.include_ext_pdf:
            variants.append(PlannedQueryVariant("ext_pdf", f"{quoted_keyword} ext:pdf"))
        if self.config.include_pdf_phrase:
            variants.append(PlannedQueryVariant("pdf_phrase", f'{quoted_keyword} "PDF"'))
        if self.config.include_site_edu:
            variants.append(
                PlannedQueryVariant("site_edu_pdf", f"{quoted_keyword} site:edu filetype:pdf")
            )
        if self.config.include_site_gov:
            variants.append(
                PlannedQueryVariant("site_gov_pdf", f"{quoted_keyword} site:gov filetype:pdf")
            )

        return QueryPlan(
            keyword=normalized_keyword,
            variants=_deduplicate_variants(variants)[: self.config.max_variants],
        )


def _normalize_keyword(keyword: str) -> str:
    stripped = keyword.strip()
    if len(stripped) >= 2 and stripped[0] == '"' and stripped[-1] == '"':
        stripped = stripped[1:-1]
    return " ".join(stripped.split())


def _deduplicate_variants(
    variants: list[PlannedQueryVariant],
) -> list[PlannedQueryVariant]:
    seen_queries: set[str] = set()
    deduplicated: list[PlannedQueryVariant] = []
    for variant in variants:
        if variant.query in seen_queries:
            continue
        seen_queries.add(variant.query)
        deduplicated.append(variant)
    return deduplicated
