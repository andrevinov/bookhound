# Bookhound: architecture and implementation plan

## Overview

Bookhound will be a CLI application for discovering PDFs by topic or keyword,
storing URLs and metadata in a local database, and downloading files only when
download mode is active and the license policy allows it.

The idea of "searching the whole internet" will be implemented as a layered
discovery strategy: optional search APIs, public web indexes, academic
repositories, digital libraries, seed-based crawling, sitemap mining, and
link-graph expansion. The system should not depend on one monolithic scraper or
one commercial search provider. It should use independent discovery adapters,
each with its own limits, credentials, and usage rules.

Initial stack:

- Python for the CLI, scraping, APIs, and scheduled jobs.
- SQLite for local persistence on the home notebook/server.
- Execution through manual commands, cron, or a `systemd` user service/timer.

## CLI modes

The CLI must clearly separate discovery, persistence, and downloads.

- `bookhound search "keyword"`: searches and lists candidates in the terminal,
  without requiring persistence or downloads.
- `bookhound collect "keyword"`: searches and saves URLs/metadata to SQLite,
  without downloading PDFs.
- `bookhound download "keyword"`: downloads only previously collected or newly
  discovered documents that pass the license gate.
- `bookhound daemon`: runs scheduled jobs for configured keywords while
  respecting limits, locks, and the non-interactive license policy.
- `bookhound export`: exports results as CSV/JSONL and, later, BibTeX or RIS.

Core rule: listing and persisting metadata and URLs is always allowed; file
downloads depend on license classification.

Discovery is intentionally broad: Bookhound may list and store every candidate
it finds, regardless of license uncertainty, missing safety signals, unknown
source quality, or incomplete metadata. Those records are catalog entries, not
download approvals.

## Pipeline

The main flow should be:

```text
discover -> enrich -> classify_license -> persist -> optionally_download
```

Responsibilities:

- `discover`: queries sources and returns raw candidates with URL, title,
  snippet, source, and available metadata.
- `enrich`: normalizes URLs, identifies DOI/ISBN when present, fetches
  complementary metadata, and checks whether the URL appears to be a real PDF.
- `classify_license`: combines evidence from the source, landing page,
  metadata, DOI, and repositories to decide `allowed`, `denied`, or `unknown`.
- `persist`: saves documents, URLs, evidence, and events to SQLite.
- `optionally_download`: downloads only when the command/mode allows it and the
  license policy authorizes it or the user confirms it.

## Search strategies

### Discovery layers

Bookhound should treat broad web discovery as a combination of complementary
layers:

- quality layer: academic/open-access sources with structured metadata;
- scale layer: public indexes such as Common Crawl;
- targeted crawling layer: configured seed domains, sitemaps, and nearby links;
- optional search layer: commercial search APIs when credentials and quotas are
  available;
- enrichment layer: sources such as Unpaywall, OpenAlex, and Crossref to improve
  metadata and licensing confidence.

No single layer is expected to represent the entire web. The value of the
system comes from combining them, deduplicating results, and recording evidence.

### Optional web search APIs

Use configurable adapters for search engines with official APIs, such as Google
Programmable Search, when credentials are available.

Example queries:

- `"keyword" filetype:pdf`
- `"keyword" ext:pdf`
- `"keyword" "PDF"`
- `"keyword" site:edu filetype:pdf`
- `"keyword" site:gov filetype:pdf`
- variations in Portuguese, English, and topic synonyms.

These sources tend to provide good relevance, but they can have costs, quotas,
result limits, and terms that change over time. They should be treated as
optional accelerators, not as the system foundation.

### Common Crawl

Use Common Crawl as a large public index for discovering PDF URLs or pages that
link to PDFs.

Strategies:

- search for URLs ending in `.pdf`;
- filter by MIME type `application/pdf` when available;
- query recent crawls before older crawls;
- use URL, domain, path, title, and snippets/landing pages as relevance signals;
- avoid downloading full WARC files when an index query or the real file is
  enough.

Common Crawl increases scale, but it does not solve licensing automatically.
Results discovered through it must pass through the same metadata and license
pipeline.

### Seed-based crawling

Seed-based crawling starts from configured domains or URLs and discovers PDFs
within a narrow, controlled neighborhood.

Good seed types:

- universities and departmental publication pages;
- public agency domains;
- journals and conference sites;
- institutional repositories;
- digital libraries;
- curated lists of trusted open-access sources.

Pros:

- reduces dependency on commercial search APIs;
- can be highly precise when the seed list is good;
- works well for recurring home-server jobs;
- makes rate limits and politeness easier to control.

Cons:

- coverage depends heavily on seed quality;
- it will not discover the open web well without good starting points;
- HTML quality varies widely;
- license evidence still needs separate extraction and enrichment.

### Sitemap mining

Sitemap mining fetches `robots.txt`, `sitemap.xml`, and sitemap indexes from a
domain, then extracts candidate PDF URLs or landing pages.

Pros:

- lightweight and polite;
- good for large, organized sites;
- often avoids crawling unnecessary pages;
- easy to schedule repeatedly.

Cons:

- many sites do not include PDFs in sitemaps;
- sitemap freshness is inconsistent;
- sitemaps rarely include license information;
- URL relevance still needs scoring.

### Link-graph expansion

Link-graph expansion follows links near already discovered PDFs or landing
pages, usually within the same domain or within a trusted allowlist.

Pros:

- can discover whole collections from one good result;
- works well in repositories and publication indexes;
- naturally grows from known relevant material.

Cons:

- can drift into unrelated pages without strict limits;
- can create crawler loops or very large frontiers;
- needs depth, domain, URL-pattern, and page-count caps.

### Search engine HTML scraping

Scraping HTML result pages from search engines is not a planned v1 strategy.
It may look attractive because it avoids API keys, but it is fragile and often
conflicts with search-engine terms and anti-abuse systems.

Pros:

- can provide relevant initial URLs without API credentials;
- may feel closer to broad web search in small manual experiments.

Cons:

- high risk of captchas, IP blocks, and unstable behavior;
- page markup changes frequently;
- difficult to test and operate in daemon mode;
- may violate terms of service;
- poorly aligned with Bookhound's license-aware and respectful-crawling goals.

### Academic and open-access repositories

Use sources with structured metadata to improve quality and licensing signals:

- arXiv: articles and PDFs with its own API.
- OpenAlex: broad academic catalog with open-access signals.
- Crossref: DOI, bibliographic metadata, and license information when
  registered.
- Semantic Scholar: academic search and open PDF fields when available.
- Unpaywall: strong DOI, open-access status, PDF URL, and license signals.
- DOAJ/DOAB/OAPEN: open-access articles/books when useful APIs or dumps are
  available.

Even with broad web search as the product goal, these sources should be
prioritized because they reduce noise and provide better evidence for license
classification.

### Libraries and digital archives

Use Internet Archive and Open Library for bibliographic discovery, while always
respecting usage guidelines, cache expectations, and an identified `User-Agent`.

Not every discovered item should be downloaded automatically. The system should
save metadata and URLs, but apply the license gate before any download.

### Lightweight crawling

The custom crawler should be limited and respectful:

- follow only links close to discovered results;
- prioritize landing pages, sitemaps, and direct PDF links;
- respect `robots.txt` where applicable;
- use per-domain rate limits;
- use timeouts, retries with backoff, and depth limits;
- identify the client with a configurable `User-Agent` and email;
- never try to bypass paywalls, logins, captchas, or blocks.

## License policy

Classifications:

- `allowed`: enough evidence indicates the download is allowed.
- `denied`: evidence indicates restricted access, a paywall, a clear
  prohibition, or an incompatible license.
- `unknown`: there is not enough evidence.
- `manually_authorized`: the user has recorded explicit authorization from the
  rights holder, repository owner, or another responsible party.

Behavior:

- `search`: may list any candidate, regardless of license status.
- `collect`: may save any URL/metadata to the database, regardless of license
  status.
- interactive `download`: downloads `allowed` and `manually_authorized`, blocks
  `denied` by default, and asks the user for `unknown`.
- `daemon`: downloads `allowed` and `manually_authorized`, blocks `denied`, and
  leaves `unknown` pending for manual review.
- A document that was previously `unknown` or `denied` may become
  `manually_authorized` only after the user records explicit authorization and
  its evidence in the application.

Possible evidence:

- metadata from the source itself;
- DOI and records from Crossref/Unpaywall/OpenAlex/Semantic Scholar;
- license fields in HTML, schema.org, Dublin Core, Highwire, or meta tags;
- text close to the PDF link on the landing page;
- domain/repository known to be open access;
- user-recorded explicit authorization, including who granted it, when it was
  granted, what URL/domain/document it covers, and optional notes or reference;
- internal PDF metadata, only after an approved download.

Bookhound must record the evidence used for each decision. It must not present
the classification as definitive legal advice. Manual authorization overrides
license uncertainty only for the scope recorded by the user; it does not bypass
technical safeguards such as not executing downloaded files and not bypassing
paywalls, logins, captchas, robots policies, or access blocks.

## Conceptual data model

Initial SQLite tables:

- `queries`: keywords, parameters, execution time, and mode.
- `sources`: enabled adapters, public configuration, and quota state.
- `documents`: deduplicated document entity with title, authors, DOI, ISBN,
  year, subject, language, and overall status.
- `document_urls`: candidate URLs per document, URL type, source, confidence,
  HTTP status, and timestamps.
- `license_evidence`: collected evidence, origin, text/value, suggested
  classification, and confidence.
- `crawl_jobs`: scheduled jobs, status, priority, retries, and next run time.
- `downloads`: local path, hash, size, status, error, date, and associated
  license decision.
- `events`: important events for audit and debugging.

Deduplication:

- canonical URL;
- DOI or ISBN when present;
- PDF hash after download;
- partial digest when safe;
- title + authors + year as fallback.

## Home-server operation

Bookhound should be comfortable to run on a home notebook:

- SQLite database in WAL mode;
- configurable local PDF directory;
- rotating logs;
- global lock to avoid concurrent crawls;
- global and per-domain/source rate limits;
- resumable partial downloads when possible;
- HTTP cache to reduce repeated calls;
- daemon mode without interactive prompts.

Possible execution styles:

- manual terminal command;
- cron for specific keywords;
- `systemd --user` with a timer;
- later, a small local API or dashboard if the CLI is not enough.

## Suggested components

Likely Python packages:

- `typer` or `click` for the CLI;
- `httpx` for async HTTP;
- `aiosqlite` or SQLAlchemy for the database;
- `pydantic` for models/configuration;
- `rich` for terminal tables and prompts;
- `tenacity` for retries/backoff;
- `beautifulsoup4` or `selectolax` for HTML;
- `pypdf` for PDF metadata when download is allowed;
- `pytest`, `respx`, and recorded fixtures for tests.

## Tests and v1 acceptance criteria

Essential tests:

- each adapter returns normalized candidates from fake responses;
- `collect` saves metadata and never downloads PDFs;
- `download` downloads `allowed`;
- `download` blocks `denied`;
- `download` asks for `unknown` in the interactive CLI;
- `daemon` does not download `unknown`;
- deduplication merges different URLs for the same DOI/PDF;
- rate limits and retries work on network errors;
- CSV/JSONL export includes URLs, metadata, and license status.

V1 acceptance:

- a keyword returns candidates from more than one source;
- results are persisted in SQLite;
- every URL has a source and discovery date;
- every download decision has recorded evidence;
- PDFs are downloaded only in download mode;
- daemon mode runs unattended and does not download uncertain cases.

## Initial references

- Google Programmable Search JSON API:
  https://developers.google.com/custom-search/v1/introduction
- Common Crawl CDXJ Index:
  https://commoncrawl.org/cdxj-index
- arXiv API User Manual:
  https://info.arxiv.org/help/api/user-manual.html
- OpenAlex Developers:
  https://developers.openalex.org/
- Open Library APIs:
  https://openlibrary.org/developers/api
- Unpaywall API:
  https://unpaywall.org/products/api
- Unpaywall Data Format:
  https://unpaywall.org/data-format
