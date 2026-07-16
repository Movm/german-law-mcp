#!/usr/bin/env python3
"""Export the canonical PostgreSQL corpus to the MCP's read-only SQLite model."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import unicodedata
from pathlib import Path

import psycopg


SQLITE_SCHEMA = """
PRAGMA journal_mode = DELETE;
PRAGMA foreign_keys = ON;
CREATE TABLE statutes (
  statute_id TEXT PRIMARY KEY, title TEXT NOT NULL, jurabk TEXT, amtabk TEXT,
  full_title TEXT, issue_date TEXT, source_url TEXT NOT NULL, xml_url TEXT NOT NULL,
  section_count INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE law_documents (
  id TEXT PRIMARY KEY, country TEXT NOT NULL, statute_id TEXT NOT NULL REFERENCES statutes(statute_id),
  section_ref TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, citation TEXT,
  source_url TEXT, effective_date TEXT, text_snippet TEXT, metadata_json TEXT,
  updated_at TEXT NOT NULL, UNIQUE(statute_id, section_ref)
);
CREATE INDEX idx_law_documents_statute ON law_documents(statute_id);
CREATE INDEX idx_law_documents_citation ON law_documents(citation);
CREATE VIRTUAL TABLE law_documents_fts USING fts5(
  title, citation, text_snippet, content='law_documents', content_rowid='rowid', tokenize='unicode61'
);
CREATE TABLE db_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def ascii_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower() or "section"


def build(args: argparse.Namespace) -> dict[str, object]:
    target = Path(args.output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    built_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

    with psycopg.connect(args.database_url) as source, sqlite3.connect(temporary) as output:
        output.executescript(SQLITE_SCHEMA)
        laws = source.execute(
            """
            SELECT id, gii_slug, abbreviation, extra_abbreviations, title_long,
                   title_short, first_published, source_url, source_snapshot
            FROM laws ORDER BY gii_slug
            """
        ).fetchall()
        document_count = 0
        for law_id, statute_id, abbreviation, extras, title_long, title_short, first_published, source_url, snapshot in laws:
            provisions = source.execute(
                """
                SELECT doknr, name, title, body, footnotes, documentary_footnotes
                FROM provisions WHERE law_id = %s AND item_type IN ('article', 'heading_article')
                ORDER BY position
                """,
                (law_id,),
            ).fetchall()
            output.execute(
                "INSERT INTO statutes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    statute_id, title_short or title_long, abbreviation,
                    (extras or [None])[0], title_long,
                    first_published.isoformat() if first_published else None,
                    source_url, SOURCE_XML_URL(statute_id), len(provisions), built_at,
                ),
            )
            seen: dict[str, int] = {}
            for doknr, name, title, body, footnotes, documentary_footnotes in provisions:
                seen[name] = seen.get(name, 0) + 1
                section_ref = name if seen[name] == 1 else f"{name} [{seen[name]}]"
                document_id = f"{statute_id}:{ascii_slug(name)}"
                if seen[name] > 1:
                    document_id += f":{ascii_slug(doknr)}"
                text = "\n\n".join(part for part in (body, footnotes, documentary_footnotes) if part)
                metadata = {
                    "source": "QuantLaw/gesetze-im-internet",
                    "source_repository": "https://github.com/QuantLaw/gesetze-im-internet",
                    "source_snapshot": snapshot.isoformat(),
                    "issue_date": first_published.isoformat() if first_published else None,
                    "statute_id": statute_id,
                    "norm_doknr": doknr,
                    "section_ref": name,
                }
                output.execute(
                    "INSERT INTO law_documents VALUES (?, 'de', ?, ?, 'statute', ?, ?, ?, ?, ?, ?, ?)",
                    (
                        document_id, statute_id, section_ref,
                        f"{title_long} - {title or name}", f"{name} {abbreviation}",
                        source_url, snapshot.isoformat(),
                        text, json.dumps(metadata, ensure_ascii=False), built_at,
                    ),
                )
                document_count += 1

        output.execute("INSERT INTO law_documents_fts(law_documents_fts) VALUES ('rebuild')")
        output.executemany(
            "INSERT INTO db_metadata VALUES (?, ?)",
            [
                ("tier", "community"),
                ("schema_version", "2"),
                ("built_at", built_at),
                ("builder", "Movm/german-law-mcp pipeline"),
                ("source", "QuantLaw/gesetze-im-internet"),
            ],
        )
        integrity = output.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        output.commit()

    temporary.replace(target)
    # The runtime image uses uid/gid 1001. @ansvar/mcp-sqlite persists its
    # in-memory WASM database and therefore needs write access to the file and
    # its directory even though every exposed MCP tool is read-only.
    target.chmod(0o664)
    try:
        os.chown(target, args.runtime_uid, args.runtime_gid)
        os.chown(target.parent, args.runtime_uid, args.runtime_gid)
    except PermissionError:
        # Local non-container exports may not be allowed to change ownership.
        pass
    return {"output": str(target), "laws": len(laws), "documents": document_count, "built_at": built_at}


def SOURCE_XML_URL(statute_id: str) -> str:
    return f"https://www.gesetze-im-internet.de/{statute_id}/xml.zip"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", "postgresql://germanlaw:germanlaw@localhost:5432/germanlaw"))
    parser.add_argument("--output", default=os.environ.get("SQLITE_OUTPUT", "data/database.db"))
    parser.add_argument("--runtime-uid", type=int, default=int(os.environ.get("MCP_RUNTIME_UID", "1001")))
    parser.add_argument("--runtime-gid", type=int, default=int(os.environ.get("MCP_RUNTIME_GID", "1001")))
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(build(parse_args()), ensure_ascii=False, indent=2))
