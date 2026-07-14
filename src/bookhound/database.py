from pathlib import Path
import sqlite3

from bookhound.models import CrawlJobStatus


SCHEMA_VERSION = 2


def initialize_database(path: str | Path) -> sqlite3.Connection:
    database_path = Path(path).expanduser()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    _apply_migrations(connection)
    return connection


def get_schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()
    return int(row[0])


def _apply_migrations(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )

    current_version = get_schema_version(connection)
    if current_version == 0:
        _apply_schema_v1(connection)
        _apply_schema_v2(connection)
        _record_schema_version(connection, SCHEMA_VERSION)
        connection.commit()
    elif current_version < 2:
        _apply_schema_v2(connection)
        _record_schema_version(connection, 2)
        connection.commit()


def _apply_schema_v1(connection: sqlite3.Connection) -> None:
    allowed_job_statuses = ", ".join(
        f"'{status.value}'" for status in CrawlJobStatus
    )
    schema = """
        CREATE TABLE IF NOT EXISTS queries (
            id INTEGER PRIMARY KEY,
            keyword TEXT NOT NULL,
            mode TEXT NOT NULL,
            variants_json TEXT NOT NULL DEFAULT '[]',
            parameters_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            config_json TEXT NOT NULL DEFAULT '{}',
            quota_state_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            authors_json TEXT NOT NULL DEFAULT '[]',
            doi TEXT UNIQUE,
            isbn TEXT UNIQUE,
            year INTEGER,
            subject TEXT,
            language TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS document_urls (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL UNIQUE,
            url_type TEXT NOT NULL,
            discovery_method TEXT,
            confidence REAL,
            http_status INTEGER,
            discovered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS license_evidence (
            id INTEGER PRIMARY KEY,
            document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
            document_url_id INTEGER REFERENCES document_urls(id) ON DELETE CASCADE,
            source TEXT NOT NULL,
            evidence_type TEXT NOT NULL,
            value TEXT NOT NULL,
            suggested_status TEXT NOT NULL CHECK (
                suggested_status IN (
                    'allowed',
                    'denied',
                    'unknown',
                    'manually_authorized'
                )
            ),
            confidence REAL,
            collected_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS crawl_jobs (
            id INTEGER PRIMARY KEY,
            keyword TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '__CRAWL_JOB_STATUS_DEFAULT__' CHECK (
                status IN (__CRAWL_JOB_STATUS_VALUES__)
            ),
            priority INTEGER NOT NULL DEFAULT 0,
            retries INTEGER NOT NULL DEFAULT 0,
            parameters_json TEXT NOT NULL DEFAULT '{}',
            next_run_at TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            document_url_id INTEGER NOT NULL REFERENCES document_urls(id) ON DELETE RESTRICT,
            license_evidence_id INTEGER REFERENCES license_evidence(id) ON DELETE SET NULL,
            local_path TEXT NOT NULL,
            sha256 TEXT UNIQUE,
            size_bytes INTEGER,
            status TEXT NOT NULL,
            error TEXT,
            downloaded_at TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            event_type TEXT NOT NULL,
            entity_type TEXT,
            entity_id INTEGER,
            message TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE INDEX IF NOT EXISTS idx_documents_doi ON documents (doi);
        CREATE INDEX IF NOT EXISTS idx_documents_isbn ON documents (isbn);
        CREATE INDEX IF NOT EXISTS idx_documents_updated_at ON documents (updated_at);
        CREATE INDEX IF NOT EXISTS idx_document_urls_canonical_url ON document_urls (canonical_url);
        CREATE INDEX IF NOT EXISTS idx_document_urls_document_id ON document_urls (document_id);
        CREATE INDEX IF NOT EXISTS idx_downloads_sha256 ON downloads (sha256);
        CREATE INDEX IF NOT EXISTS idx_downloads_document_id ON downloads (document_id);
        CREATE INDEX IF NOT EXISTS idx_queries_created_at ON queries (created_at);
        CREATE INDEX IF NOT EXISTS idx_events_created_at ON events (created_at);
        CREATE INDEX IF NOT EXISTS idx_license_evidence_document_id
            ON license_evidence (document_id);
        CREATE INDEX IF NOT EXISTS idx_crawl_jobs_next_run_at ON crawl_jobs (next_run_at);
        """
    connection.executescript(
        schema.replace(
            "__CRAWL_JOB_STATUS_DEFAULT__",
            CrawlJobStatus.PENDING.value,
        ).replace(
            "__CRAWL_JOB_STATUS_VALUES__",
            allowed_job_statuses,
        )
    )


def _apply_schema_v2(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_license_evidence_document_url_id
            ON license_evidence (document_url_id)
        """
    )


def _record_schema_version(connection: sqlite3.Connection, version: int) -> None:
    connection.execute(
        """
        INSERT INTO schema_migrations (version)
        VALUES (?)
        """,
        (version,),
    )
