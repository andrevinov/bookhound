from pathlib import Path
import os
import tomllib
from typing import Any

from pydantic import BaseModel, Field, SecretStr

from bookhound import __version__


DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_GLOBAL_RATE_LIMIT_PER_SECOND = 5.0
DEFAULT_PER_DOMAIN_RATE_LIMIT_PER_SECOND = 1.0


class GoogleSourceSettings(BaseModel):
    enabled: bool = False
    api_key: SecretStr | None = None
    search_engine_id: SecretStr | None = None


class UnpaywallSourceSettings(BaseModel):
    enabled: bool = False
    email: str | None = None


class ArxivSourceSettings(BaseModel):
    enabled: bool = True
    max_results: int = 20
    page_size: int = 10


class CommonCrawlSettings(BaseModel):
    enabled: bool = True
    result_limit: int = 1000
    crawl_indexes: list[str] = Field(default_factory=list)


class SeedCrawlerSettings(BaseModel):
    enabled: bool = True
    seed_urls: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    same_domain_only: bool = True
    max_depth: int = 1
    max_pages_per_seed: int = 50


class SitemapSettings(BaseModel):
    enabled: bool = True
    domain_roots: list[str] = Field(default_factory=list)
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    rate_limit_per_second: float = DEFAULT_PER_DOMAIN_RATE_LIMIT_PER_SECOND


class LinkExpansionSettings(BaseModel):
    enabled: bool = True
    max_depth: int = 1
    max_candidates: int = 100
    same_domain_only: bool = True


class SourceSettings(BaseModel):
    google: GoogleSourceSettings = Field(default_factory=GoogleSourceSettings)
    unpaywall: UnpaywallSourceSettings = Field(default_factory=UnpaywallSourceSettings)
    arxiv: ArxivSourceSettings = Field(default_factory=ArxivSourceSettings)
    common_crawl: CommonCrawlSettings = Field(default_factory=CommonCrawlSettings)
    seed_crawler: SeedCrawlerSettings = Field(default_factory=SeedCrawlerSettings)
    sitemap: SitemapSettings
    link_expansion: LinkExpansionSettings = Field(default_factory=LinkExpansionSettings)


class AppSettings(BaseModel):
    database_path: Path
    pdf_directory: Path
    user_agent: str
    request_timeout_seconds: float
    global_rate_limit_per_second: float
    per_domain_rate_limit_per_second: float
    sources: SourceSettings = Field(default_factory=SourceSettings)

    def public_dump(self) -> dict[str, Any]:
        return {
            "database_path": str(self.database_path),
            "pdf_directory": str(self.pdf_directory),
            "user_agent": self.user_agent,
            "request_timeout_seconds": self.request_timeout_seconds,
            "global_rate_limit_per_second": self.global_rate_limit_per_second,
            "per_domain_rate_limit_per_second": self.per_domain_rate_limit_per_second,
            "sources": {
                "google": {"enabled": self.sources.google.enabled},
                "unpaywall": {
                    "enabled": self.sources.unpaywall.enabled,
                    "email": self.sources.unpaywall.email,
                },
                "arxiv": self.sources.arxiv.model_dump(mode="json"),
                "common_crawl": self.sources.common_crawl.model_dump(mode="json"),
                "seed_crawler": self.sources.seed_crawler.model_dump(mode="json"),
                "sitemap": self.sources.sitemap.model_dump(mode="json"),
                "link_expansion": self.sources.link_expansion.model_dump(mode="json"),
            },
        }


def load_settings(
    config_path: str | Path | None = None,
    project_root: str | Path | None = None,
) -> AppSettings:
    root = Path(project_root) if project_root is not None else Path.cwd()
    root = root.expanduser().resolve()
    config = _load_config_file(config_path)

    database_path = _setting_value(
        "BOOKHOUND_DATABASE_PATH",
        config,
        ("paths", "database_path"),
        "bookhound.sqlite3",
    )
    pdf_directory = _setting_value(
        "BOOKHOUND_PDF_DIRECTORY",
        config,
        ("paths", "pdf_directory"),
        "pdfs",
    )
    user_agent = _setting_value(
        "BOOKHOUND_USER_AGENT",
        config,
        ("http", "user_agent"),
        f"Bookhound/{__version__}",
    )
    request_timeout_seconds = _setting_value(
        "BOOKHOUND_REQUEST_TIMEOUT_SECONDS",
        config,
        ("http", "request_timeout_seconds"),
        DEFAULT_REQUEST_TIMEOUT_SECONDS,
    )
    global_rate_limit_per_second = _setting_value(
        "BOOKHOUND_GLOBAL_RATE_LIMIT_PER_SECOND",
        config,
        ("rate_limits", "global_rate_limit_per_second"),
        DEFAULT_GLOBAL_RATE_LIMIT_PER_SECOND,
    )
    per_domain_rate_limit_per_second = _setting_value(
        "BOOKHOUND_PER_DOMAIN_RATE_LIMIT_PER_SECOND",
        config,
        ("rate_limits", "per_domain_rate_limit_per_second"),
        DEFAULT_PER_DOMAIN_RATE_LIMIT_PER_SECOND,
    )

    sources = _load_source_settings(
        config=config,
        request_timeout_seconds=float(request_timeout_seconds),
        per_domain_rate_limit_per_second=float(per_domain_rate_limit_per_second),
    )

    return AppSettings(
        database_path=_resolve_path(database_path, root),
        pdf_directory=_resolve_path(pdf_directory, root),
        user_agent=str(user_agent),
        request_timeout_seconds=float(request_timeout_seconds),
        global_rate_limit_per_second=float(global_rate_limit_per_second),
        per_domain_rate_limit_per_second=float(per_domain_rate_limit_per_second),
        sources=sources,
    )


def _load_config_file(config_path: str | Path | None) -> dict[str, Any]:
    if config_path is None:
        return {}

    path = Path(config_path).expanduser()
    if not path.exists():
        return {}

    with path.open("rb") as config_file:
        return tomllib.load(config_file)


def _setting_value(
    env_name: str,
    config: dict[str, Any],
    config_path: tuple[str, str],
    default: Any,
) -> Any:
    env_value = os.environ.get(env_name)
    if env_value is not None:
        return env_value

    section_name, key = config_path
    section = config.get(section_name, {})
    if isinstance(section, dict) and key in section:
        return section[key]

    return default


def _resolve_path(value: str | Path, project_root: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return project_root / path


def _load_source_settings(
    config: dict[str, Any],
    request_timeout_seconds: float,
    per_domain_rate_limit_per_second: float,
) -> SourceSettings:
    google_api_key = _source_value(
        "BOOKHOUND_GOOGLE_API_KEY",
        config,
        "google",
        "api_key",
    )
    google_search_engine_id = _source_value(
        "BOOKHOUND_GOOGLE_SEARCH_ENGINE_ID",
        config,
        "google",
        "search_engine_id",
    )
    unpaywall_email = _source_value(
        "BOOKHOUND_UNPAYWALL_EMAIL",
        config,
        "unpaywall",
        "email",
    )

    return SourceSettings(
        google=GoogleSourceSettings(
            enabled=bool(google_api_key and google_search_engine_id),
            api_key=SecretStr(google_api_key) if google_api_key else None,
            search_engine_id=SecretStr(google_search_engine_id)
            if google_search_engine_id
            else None,
        ),
        unpaywall=UnpaywallSourceSettings(
            enabled=bool(unpaywall_email),
            email=unpaywall_email,
        ),
        arxiv=ArxivSourceSettings(
            enabled=_source_bool(config, "arxiv", "enabled", True),
            max_results=int(
                _source_setting_value(
                    None,
                    config,
                    "arxiv",
                    "max_results",
                    20,
                )
            ),
            page_size=int(
                _source_setting_value(
                    None,
                    config,
                    "arxiv",
                    "page_size",
                    10,
                )
            ),
        ),
        common_crawl=CommonCrawlSettings(
            enabled=_source_bool(config, "common_crawl", "enabled", True),
            result_limit=int(
                _source_setting_value(
                    "BOOKHOUND_COMMON_CRAWL_RESULT_LIMIT",
                    config,
                    "common_crawl",
                    "result_limit",
                    1000,
                )
            ),
            crawl_indexes=list(
                _source_setting_value(
                    None,
                    config,
                    "common_crawl",
                    "crawl_indexes",
                    [],
                )
            ),
        ),
        seed_crawler=SeedCrawlerSettings(
            enabled=_source_bool(config, "seed_crawler", "enabled", True),
            seed_urls=list(_source_setting_value(None, config, "seed_crawler", "seed_urls", [])),
            allowed_domains=list(
                _source_setting_value(None, config, "seed_crawler", "allowed_domains", [])
            ),
            same_domain_only=_source_bool(config, "seed_crawler", "same_domain_only", True),
            max_depth=int(
                _source_setting_value(
                    "BOOKHOUND_SEED_CRAWLER_MAX_DEPTH",
                    config,
                    "seed_crawler",
                    "max_depth",
                    1,
                )
            ),
            max_pages_per_seed=int(
                _source_setting_value(
                    "BOOKHOUND_SEED_CRAWLER_MAX_PAGES_PER_SEED",
                    config,
                    "seed_crawler",
                    "max_pages_per_seed",
                    50,
                )
            ),
        ),
        sitemap=SitemapSettings(
            enabled=_source_bool(config, "sitemap", "enabled", True),
            domain_roots=list(
                _source_setting_value(None, config, "sitemap", "domain_roots", [])
            ),
            request_timeout_seconds=request_timeout_seconds,
            rate_limit_per_second=per_domain_rate_limit_per_second,
        ),
        link_expansion=LinkExpansionSettings(
            enabled=_source_bool(config, "link_expansion", "enabled", True),
            max_depth=int(
                _source_setting_value(
                    "BOOKHOUND_LINK_EXPANSION_MAX_DEPTH",
                    config,
                    "link_expansion",
                    "max_depth",
                    1,
                )
            ),
            max_candidates=int(
                _source_setting_value(
                    "BOOKHOUND_LINK_EXPANSION_MAX_CANDIDATES",
                    config,
                    "link_expansion",
                    "max_candidates",
                    100,
                )
            ),
            same_domain_only=_source_bool(config, "link_expansion", "same_domain_only", True),
        ),
    )


def _source_value(
    env_name: str,
    config: dict[str, Any],
    source_name: str,
    key: str,
) -> str | None:
    env_value = os.environ.get(env_name)
    if env_value is not None:
        return env_value

    sources = config.get("sources", {})
    if not isinstance(sources, dict):
        return None

    source = sources.get(source_name, {})
    if isinstance(source, dict):
        value = source.get(key)
        return str(value) if value is not None else None

    return None


def _source_setting_value(
    env_name: str | None,
    config: dict[str, Any],
    source_name: str,
    key: str,
    default: Any,
) -> Any:
    if env_name is not None:
        env_value = os.environ.get(env_name)
        if env_value is not None:
            return env_value

    source = _source_section(config, source_name)
    if key in source:
        return source[key]

    return default


def _source_bool(
    config: dict[str, Any],
    source_name: str,
    key: str,
    default: bool,
) -> bool:
    value = _source_setting_value(None, config, source_name, key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _source_section(config: dict[str, Any], source_name: str) -> dict[str, Any]:
    sources = config.get("sources", {})
    if not isinstance(sources, dict):
        return {}

    source = sources.get(source_name, {})
    if isinstance(source, dict):
        return source

    return {}
