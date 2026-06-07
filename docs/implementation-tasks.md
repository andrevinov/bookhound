# Bookhound: implementation task breakdown

## Summary

I would split Bookhound v1 into 25 incremental tasks. Each task should deliver
a small piece that can support the next layer and be tested with isolated unit
tests.

The order below avoids starting with external integrations. First we create the
internal contract, persistence, deduplication, and a pipeline with fake sources.
Then we add real sources, license classification, public-index discovery,
targeted crawling strategies, downloads, daemon operation, and export.

## Roadmap principles

- Each task should be reviewable and testable on its own.
- Every external integration should have tests based on fixtures or mocks, with
  no real network dependency.
- `collect` must never download PDFs.
- `download` must never bypass the license decision.
- The daemon must be non-interactive and conservative.
- Source adapters must be pluggable and independent.
- Commercial search APIs are optional accelerators, not the foundation for broad
  web discovery.

## Tasks

### 1. Python project scaffold

Goal: create the minimal package, CLI, and test foundation.

Deliverables:

- `pyproject.toml` with initial dependencies.
- `bookhound` package.
- `bookhound` CLI entry point.
- `tests/` structure.
- `pytest` configuration.

Unit tests:

- Package import works.
- CLI responds to `--help`.
- Package version is exposed correctly.

### 2. Domain models

Goal: define the internal types shared by all layers.

Deliverables:

- Models for `SearchQuery`, `RawCandidate`, `Document`, `DocumentUrl`,
  `LicenseEvidence`, `LicenseDecision`, `DownloadRecord`, and `SourceResult`.
- Enums for execution mode, URL type, license status, and download status.
- Enums for source kind and discovery method, including Google, arXiv,
  Unpaywall, Common Crawl, seed crawler, sitemap, and link expansion.
- Basic validation for required fields.

Unit tests:

- Models accept valid data.
- Models reject empty URLs, invalid statuses, and malformed dates.
- Dict/JSON serialization preserves expected fields.

### 3. Application configuration

Goal: centralize paths, optional credentials, limits, and mode behavior.

Deliverables:

- Configuration loader from file and environment variables.
- Defaults for the SQLite database, PDF directory, user agent, timeouts, and
  rate limits.
- Separation between public configuration and secrets.

Unit tests:

- Defaults load without a config file.
- Environment variables override defaults.
- Relative paths are resolved predictably.
- Missing credentials disable optional API-backed adapters without breaking the
  application.

### 4. SQLite schema and initial migrations

Goal: create reproducible local persistence.

Deliverables:

- SQLite initializer in WAL mode.
- Tables `queries`, `sources`, `documents`, `document_urls`,
  `license_evidence`, `crawl_jobs`, `downloads`, and `events`.
- Indexes for canonical URL, DOI, ISBN, hash, and timestamps.
- Simple versioned migration mechanism.

Unit tests:

- A new database creates all tables.
- Migrations are idempotent.
- Constraints prevent obvious duplicates.
- WAL mode is enabled when supported.

### 5. Data repositories

Goal: encapsulate SQLite read/write operations.

Deliverables:

- Repositories for documents, URLs, queries, evidence, downloads, and events.
- Upsert operations for documents and URLs.
- Atomic transactions for saving collection results.

Unit tests:

- Document upsert does not duplicate DOI.
- URL upsert updates metadata without losing relevant history.
- Saving document + URL + evidence happens in one transaction.
- An error in the middle of a transaction triggers rollback.

### 6. URL normalization

Goal: reduce duplicates before persistence.

Deliverables:

- URL canonicalizer.
- Optional removal of tracking parameters.
- Normalization of host, scheme, fragments, and trailing slashes.
- Initial detection of direct PDF URLs.

Unit tests:

- Equivalent URLs generate the same canonical form.
- Fragments are removed.
- Common tracking parameters are removed.
- URLs with important query parameters are not destroyed.

### 7. Document deduplication

Goal: decide when different candidates represent the same document.

Deliverables:

- Deduplication by DOI, ISBN, canonical URL, hash, and title/authors/year
  fallback.
- Deduplication confidence score.
- Merge reason recording.

Unit tests:

- Same DOI merges documents.
- Same canonical URL merges candidates.
- Similar title without authors does not merge aggressively.
- Same hash after download merges documents even with different URLs.

### 8. Source adapter contract

Goal: create one interface for all discovery sources.

Deliverables:

- `SourceAdapter` interface/base class.
- Methods for `search`, `enabled`, `source_name`, and `rate_limit_key`.
- In-memory fake source for tests.
- Standard types for source, quota, and availability errors.

Unit tests:

- Fake source returns normalized candidates.
- Disabled adapter does not run.
- Source errors are represented without taking down the entire pipeline.

### 9. Query planner

Goal: expand a keyword into source-specific searches.

Deliverables:

- Generation of variants such as `filetype:pdf`, `ext:pdf`, `site:edu`,
  `site:gov`, and quoted terms.
- Maximum variant limit.
- Recording of the variants used in `queries`.

Unit tests:

- Simple keyword generates expected variants.
- Keyword with quotes does not duplicate quotes incorrectly.
- Variant limit is respected.
- Variants can be enabled/disabled through configuration.

### 10. Shared HTTP client

Goal: standardize external calls.

Deliverables:

- Wrapper around `httpx`.
- Timeout, retry with backoff, configurable user agent, and default headers.
- Rate limit by source/domain.
- Simple cache for GET responses when allowed.

Unit tests:

- User agent is sent.
- Timeout produces a typed error.
- Retry happens for transient errors.
- Rate limit is applied per key.
- Cache avoids a second call for the same cacheable URL.

### 11. Discovery pipeline with fake source

Goal: connect query planner, adapters, normalization, and deduplication without
network access.

Deliverables:

- `DiscoveryPipeline` orchestrator.
- Execution of multiple sources.
- Aggregation of partial errors.
- Results ordered by score/source.

Unit tests:

- Pipeline returns candidates from multiple fake sources.
- One failing source does not prevent the others from running.
- Duplicate candidates are merged.
- Result includes source and query variant used.

### 12. `search` CLI

Goal: make discovery visible in the terminal without mandatory persistence.

Deliverables:

- `bookhound search "keyword"` command.
- Table output with title, URL, source, score, and preliminary status.
- `--json` option.
- Option to limit results.

Unit tests:

- Command calls the pipeline.
- Table output contains the main fields.
- `--json` returns parseable JSON.
- Result limit is respected.

### 13. `collect` CLI

Goal: save results to the database without downloading files.

Deliverables:

- `bookhound collect "keyword"` command.
- Persistence of query, documents, URLs, sources, and events.
- Final summary with counts for new, updated, and duplicate records.

Unit tests:

- `collect` saves candidates to SQLite.
- `collect` does not call the downloader.
- Running twice does not duplicate equivalent documents.
- Collection events are recorded.

### 14. License classifier v1

Goal: implement the first conservative license policy.

Deliverables:

- Rules for `allowed`, `denied`, and `unknown`.
- Configurable list of known permissive licenses.
- Configurable list of trusted domains/repositories.
- Evidence and reason recording.

Unit tests:

- Permissive Creative Commons license becomes `allowed`.
- Paywall or restricted access becomes `denied`.
- Missing evidence becomes `unknown`.
- Decision contains reason and evidence.

### 15. HTML evidence extraction

Goal: collect license signals from landing pages.

Deliverables:

- Parser for common meta tags: schema.org, Dublin Core, Highwire, and citation.
- Search for text close to PDF links.
- Extraction of DOI, title, authors, and date when present.

Unit tests:

- HTML with license meta tag produces correct evidence.
- PDF link near Creative Commons text produces evidence.
- HTML without metadata does not crash and returns an empty list.
- DOI in a meta tag is extracted.

### 16. arXiv adapter

Goal: add the first real source with predictable metadata.

Deliverables:

- Keyword search through the arXiv API.
- Conversion from entries to internal candidates.
- Landing page and PDF URL.
- Pagination and configured interval support.

Unit tests:

- arXiv Atom fixture becomes candidates.
- PDF URL is derived correctly.
- Pagination uses `start` and `max_results`.
- HTTP error becomes a typed source error.

### 17. Unpaywall enrichment adapter

Goal: enrich DOI-backed documents with OA status, PDF URL, and license.

Deliverables:

- DOI lookup.
- Extraction of `best_oa_location`, PDF URL, landing page, host type, and
  license.
- Evidence for the classifier.

Unit tests:

- Fixture with `best_oa_location` produces URL and evidence.
- Record without OA location does not produce a false `allowed`.
- Null license becomes `unknown`.
- Required email in configuration is validated.

### 18. Google web search adapter

Goal: connect Google Programmable Search when credentials and quota are
available.

Deliverables:

- Adapter for Google Programmable Search.
- Query variants from the planner.
- Conversion of snippets/results into candidates.
- Graceful disabling without an API key.

Unit tests:

- Google JSON fixture becomes candidates.
- Missing credential marks adapter as disabled.
- Sent query preserves the planned variant.
- Quota error becomes a typed error and does not take down the pipeline.

### 19. Common Crawl adapter

Goal: add broad discovery through a public index.

Deliverables:

- Query against the configured index.
- Filter by `.pdf` and MIME type when available.
- Conversion of CDXJ lines into candidates.
- Result limits per crawl.

Unit tests:

- CDXJ fixture becomes candidates.
- Non-PDF entries are filtered when configured.
- Crawls are queried in configured order.
- Malformed line is ignored with an error event.

### 20. Seed-based crawler adapter

Goal: discover PDFs from configured seed URLs and trusted domains without using
commercial search APIs.

Deliverables:

- Configurable seed list with per-seed limits.
- Same-domain crawling with depth, page-count, and URL-pattern caps.
- Extraction of PDF links and candidate landing pages.
- Respect for robots policy, timeouts, and per-domain rate limits.

Unit tests:

- Seed HTML fixture yields direct PDF and landing-page candidates.
- Off-domain links are ignored unless explicitly allowed.
- Depth and page-count limits stop expansion.
- Robots-disallowed URLs are skipped and recorded as skipped events.

### 21. Sitemap mining adapter

Goal: discover candidate URLs from `robots.txt`, `sitemap.xml`, and sitemap
indexes.

Deliverables:

- Sitemap discovery from a domain root.
- Parsing for sitemap indexes and URL sets.
- Filtering for PDF URLs and likely document landing pages.
- Timestamp and source metadata from sitemap entries when available.

Unit tests:

- `robots.txt` fixture points to sitemap URLs.
- Sitemap index fixture expands to child sitemaps.
- URL set fixture yields PDF candidates.
- Malformed sitemap entries are ignored with an error event.

### 22. Link-graph expansion

Goal: expand discovery from already relevant PDFs or landing pages while
keeping the crawl frontier bounded.

Deliverables:

- Frontier builder from existing candidates.
- Same-domain or allowlisted expansion policy.
- Link scoring based on proximity, URL shape, and anchor text.
- Loop prevention and duplicate URL suppression.

Unit tests:

- Relevant landing page produces nearby PDF candidates.
- Already-seen URLs are not requeued.
- Expansion stays within configured domain policy.
- Frontier stops at configured depth and candidate limits.

### 23. Downloader with license gate

Goal: download PDFs only when allowed.

Deliverables:

- Download service with mandatory `LicenseDecision` check.
- Atomic write to a temporary file and final rename.
- Hash and size calculation.
- `downloads` record.
- Confirmation support for `unknown` when interactive.

Unit tests:

- `allowed` downloads and records the file.
- `denied` does not download.
- Interactive `unknown` calls the prompt.
- Non-interactive `unknown` does not download.
- Interrupted download is not recorded as success.

### 24. `download` CLI

Goal: expose license-controlled downloads through the CLI.

Deliverables:

- `bookhound download "keyword"` command for search/collect/download.
- Option to download only previously collected documents.
- Prompt for `unknown` cases.
- Summary of downloaded, blocked, pending, and failed items.

Unit tests:

- Command downloads only allowed candidates.
- Previously-collected-only option does not call external discovery.
- `unknown` prompt respects the user response.
- Final summary shows correct counts.

### 25. Jobs, daemon, and export

Goal: support continuous home-server operation and data output.

Deliverables:

- Simple CRUD for keyword jobs.
- Non-interactive `daemon` runner with a global lock.
- CSV and JSONL export.
- Execution logs/events.

Unit tests:

- Pending job is selected for execution.
- Lock prevents two concurrent executions.
- Daemon does not download `unknown`.
- CSV/JSONL export includes metadata, URLs, and license status.

## Suggested milestones

### Milestone 1: testable local foundation

Tasks 1 to 7.

Result: importable project, database creation, stable models, URL
normalization, and deduplication working without network access.

### Milestone 2: discovery and collection without network

Tasks 8 to 13.

Result: `search` and `collect` CLIs working with a fake source, persistence,
and pipeline tests.

### Milestone 3: license and evidence

Tasks 14 and 15.

Result: tested v1 license policy with recordable evidence.

### Milestone 4: real sources and broad discovery

Tasks 16 to 22.

Result: discovery through arXiv, Unpaywall, optional Google web search, Common
Crawl, seed-based crawling, sitemap mining, and link-graph expansion, always
tested with fixtures.

### Milestone 5: download and operation

Tasks 23 to 25.

Result: license-safe downloads, complete v1 CLI, local daemon, and export.

## Definition of done for each task

- Code implemented within the task scope.
- Unit tests cover the happy path and at least one relevant error.
- No real network calls in unit tests.
- No behavior changes outside the task scope.
- Documentation updated when the task changes a command, schema, or policy.
