from __future__ import annotations

from collections.abc import Iterable
import csv
import json
from pathlib import Path
import sqlite3


class ExportService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def export_csv(self, path: Path) -> None:
        with path.open("w", encoding="utf-8", newline="") as export_file:
            writer = csv.DictWriter(
                export_file,
                fieldnames=[
                    "title",
                    "doi",
                    "url",
                    "canonical_url",
                    "source",
                    "license_status",
                    "metadata",
                ],
            )
            writer.writeheader()
            for row in self._rows():
                writer.writerow(
                    {
                        **row,
                        "metadata": json.dumps(row["metadata"], sort_keys=True),
                    }
                )

    def export_jsonl(self, path: Path) -> None:
        with path.open("w", encoding="utf-8") as export_file:
            for row in self._rows():
                export_file.write(json.dumps(row, sort_keys=True) + "\n")

    def _rows(self) -> Iterable[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT
                documents.title,
                documents.doi,
                document_urls.url,
                document_urls.canonical_url,
                sources.name,
                documents.metadata_json,
                (
                    SELECT license_evidence.suggested_status
                    FROM license_evidence
                    WHERE license_evidence.document_id = documents.id
                    ORDER BY license_evidence.collected_at DESC, license_evidence.id DESC
                    LIMIT 1
                ) AS license_status
            FROM documents
            JOIN document_urls ON document_urls.document_id = documents.id
            JOIN sources ON sources.id = document_urls.source_id
            ORDER BY documents.id, document_urls.id
            """
        )
        for row in rows:
            yield {
                "title": str(row[0]),
                "doi": str(row[1]) if row[1] is not None else "",
                "url": str(row[2]),
                "canonical_url": str(row[3]),
                "source": str(row[4]),
                "license_status": str(row[6]) if row[6] is not None else "unknown",
                "metadata": _json_object(str(row[5])),
            }


def _json_object(value: str) -> dict[str, object]:
    loaded = json.loads(value) if value else {}
    if isinstance(loaded, dict):
        return loaded
    return {}
