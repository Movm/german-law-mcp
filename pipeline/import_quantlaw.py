#!/usr/bin/env python3
"""Import one QuantLaw Gesetze-im-Internet snapshot into PostgreSQL.

The importer intentionally downloads a single tagged snapshot archive instead of
cloning the complete Git history. QuantLaw remains the provenance-preserving raw
archive; PostgreSQL is the canonical local representation used by this project.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

SOURCE_REPOSITORY = "https://github.com/QuantLaw/gesetze-im-internet"
DEFAULT_ARCHIVE_TEMPLATE = SOURCE_REPOSITORY + "/archive/refs/tags/{snapshot}.tar.gz"
WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Provision:
    doknr: str
    item_type: str
    name: str
    title: str | None
    body: str | None
    footnotes: str | None
    documentary_footnotes: str | None
    parent_doknr: str | None
    position: int


@dataclass(frozen=True)
class Law:
    doknr: str
    slug: str
    gii_slug: str
    abbreviation: str
    extra_abbreviations: list[str]
    first_published: str | None
    source_timestamp: str | None
    title_long: str
    title_short: str | None
    publication_info: list[dict[str, str]]
    status_info: list[dict[str, str]]
    source_url: str
    provisions: list[Provision]


def collapse(value: str | None) -> str:
    return WHITESPACE.sub(" ", value or "").strip()


def text_content(node: ET.Element | None) -> str | None:
    value = collapse(" ".join(node.itertext())) if node is not None else ""
    return value or None


def child_text(node: ET.Element | None, path: str) -> str | None:
    return text_content(node.find(path)) if node is not None else None


def slugify(value: str) -> str:
    value = value.lower().replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]", "_", value)).strip("_") or "gesetz"


def classify_item(doknr: str, body: str | None, name: str | None) -> str:
    if "NE" in doknr or (name and re.match(r"^(§|Art\.?|Artikel)\s*\d", name, re.I)):
        return "article"
    if "NG" in doknr:
        return "heading_article" if body else "heading"
    return "article" if body else "heading"


def find_parent(code: str | None, sections: dict[str, str]) -> str | None:
    if not code:
        return None
    for length in range(len(code) - 3, -1, -3):
        parent = sections.get(code[:length])
        if parent:
            return parent
    return None


def parse_law_xml(path: Path) -> Law:
    root = ET.parse(path).getroot()
    norms = root.findall("./norm")
    if not norms:
        raise ValueError(f"No <norm> elements in {path}")

    header = norms[0]
    metadata = header.find("metadaten")
    if metadata is None:
        raise ValueError(f"No law metadata in {path}")

    doknr = collapse(header.attrib.get("doknr")) or path.stem
    jurabk = [collapse(text_content(node)) for node in metadata.findall("jurabk")]
    amtabk = [collapse(text_content(node)) for node in metadata.findall("amtabk")]
    abbreviations = list(dict.fromkeys(value for value in amtabk + jurabk if value))
    abbreviation = abbreviations[0] if abbreviations else path.parent.name.upper()
    gii_slug = path.parent.name
    title_long = child_text(metadata, "langue") or child_text(metadata, "kurzue") or abbreviation
    title_short = child_text(metadata, "kurzue")

    publication_info = [
        {
            "periodical": child_text(node, "periodikum") or "",
            "reference": child_text(node, "zitstelle") or "",
        }
        for node in metadata.findall("fundstelle")
    ]
    status_info = [
        {
            "category": child_text(node, "standtyp") or "",
            "comment": child_text(node, "standkommentar") or "",
        }
        for node in metadata.findall("standangabe")
    ]

    sections: dict[str, str] = {}
    current_parent: str | None = None
    provisions: list[Provision] = []
    for position, norm in enumerate(norms[1:]):
        meta = norm.find("metadaten")
        if meta is None:
            continue
        provision_doknr = collapse(norm.attrib.get("doknr")) or f"{doknr}-{position}"
        name = child_text(meta, "enbez")
        hierarchy = meta.find("gliederungseinheit")
        hierarchy_code = child_text(hierarchy, "gliederungskennzahl")
        hierarchy_name = child_text(hierarchy, "gliederungsbez")
        title = child_text(meta, "titel") or child_text(hierarchy, "gliederungstitel")
        body = text_content(norm.find("./textdaten/text/Content")) or text_content(norm.find("./textdaten/text/TOC"))
        footnotes = text_content(norm.find("./textdaten/text/Footnotes"))
        documentary_footnotes = text_content(norm.find("./textdaten/fussnoten/Content"))
        item_type = classify_item(provision_doknr, body, name)

        if item_type == "article":
            parent_doknr = find_parent(hierarchy_code, sections) or current_parent
        else:
            name = name or hierarchy_name or "Gliederung"
            parent_doknr = find_parent(hierarchy_code, sections)
            if hierarchy_code:
                sections[hierarchy_code] = provision_doknr
            current_parent = provision_doknr

        if not name and not body:
            continue
        provisions.append(
            Provision(
                doknr=provision_doknr,
                item_type=item_type,
                name=name or "Norm",
                title=title,
                body=body,
                footnotes=footnotes,
                documentary_footnotes=documentary_footnotes,
                parent_doknr=parent_doknr,
                position=position,
            )
        )

    return Law(
        doknr=doknr,
        slug=slugify(abbreviation),
        gii_slug=gii_slug,
        abbreviation=abbreviation,
        extra_abbreviations=abbreviations[1:],
        first_published=child_text(metadata, "ausfertigung-datum"),
        source_timestamp=collapse(header.attrib.get("builddate")) or None,
        title_long=title_long,
        title_short=title_short,
        publication_info=publication_info,
        status_info=status_info,
        source_url=f"https://www.gesetze-im-internet.de/{urllib.parse.quote(gii_slug)}/index.html",
        provisions=provisions,
    )


def safe_extract(payload: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        destination_real = destination.resolve()
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if destination_real not in target.parents and target != destination_real:
                raise ValueError("Unsafe path in source archive")
        archive.extractall(destination)


def download_snapshot(snapshot: str, destination: Path, template: str) -> Path:
    url = template.format(snapshot=urllib.parse.quote(snapshot, safe=""))
    request = urllib.request.Request(url, headers={"User-Agent": "movm-german-law-mcp/0.1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        safe_extract(response.read(), destination)
    roots = list(destination.glob("gesetze-im-internet-*"))
    if len(roots) != 1:
        raise RuntimeError("Could not locate extracted QuantLaw snapshot")
    return roots[0]


def load_schema(connection: Any, schema_path: Path) -> None:
    connection.execute(schema_path.read_text(encoding="utf-8"))
    connection.commit()


def upsert_law(connection: Any, law: Law, snapshot: str) -> int:
    row = connection.execute(
        """
        INSERT INTO laws (
          doknr, slug, gii_slug, abbreviation, extra_abbreviations,
          first_published, source_timestamp, title_long, title_short,
          publication_info, status_info, source_url, source_snapshot, source_repository
        ) VALUES (
          %(doknr)s, %(slug)s, %(gii_slug)s, %(abbreviation)s, %(extra_abbreviations)s,
          %(first_published)s, %(source_timestamp)s, %(title_long)s, %(title_short)s,
          %(publication_info)s, %(status_info)s, %(source_url)s, %(snapshot)s, %(source_repository)s
        )
        ON CONFLICT (doknr) DO UPDATE SET
          slug = excluded.slug, gii_slug = excluded.gii_slug,
          abbreviation = excluded.abbreviation, extra_abbreviations = excluded.extra_abbreviations,
          first_published = excluded.first_published, source_timestamp = excluded.source_timestamp,
          title_long = excluded.title_long, title_short = excluded.title_short,
          publication_info = excluded.publication_info, status_info = excluded.status_info,
          source_url = excluded.source_url, source_snapshot = excluded.source_snapshot,
          source_repository = excluded.source_repository, imported_at = now()
        RETURNING id
        """,
        {
            **law.__dict__,
            "publication_info": json.dumps(law.publication_info),
            "status_info": json.dumps(law.status_info),
            "snapshot": snapshot,
            "source_repository": SOURCE_REPOSITORY,
        },
    ).fetchone()
    assert row
    law_id = int(row[0])
    connection.execute("DELETE FROM provisions WHERE law_id = %s", (law_id,))
    if law.provisions:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO provisions (
                  doknr, law_id, item_type, name, title, body, footnotes,
                  documentary_footnotes, parent_doknr, position
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        item.doknr, law_id, item.item_type, item.name, item.title,
                        item.body, item.footnotes, item.documentary_footnotes,
                        item.parent_doknr, item.position,
                    )
                    for item in law.provisions
                ],
            )
    return len(law.provisions)


def iter_xml_files(repo_root: Path) -> list[Path]:
    items = repo_root / "data" / "items"
    if not items.is_dir():
        raise FileNotFoundError(f"QuantLaw data/items directory not found below {repo_root}")
    return sorted(items.glob("*/*.xml"))


def run(args: argparse.Namespace) -> dict[str, object]:
    import psycopg

    workspace: Path | None = None
    repo_root = Path(args.repo_dir).resolve() if args.repo_dir else None
    if repo_root is None:
        workspace = Path(tempfile.mkdtemp(prefix="quantlaw-gii-"))
        repo_root = download_snapshot(args.snapshot, workspace, args.archive_template)

    errors: list[str] = []
    imported_laws = 0
    imported_provisions = 0
    try:
        with psycopg.connect(args.database_url) as connection:
            load_schema(connection, Path(__file__).with_name("schema.sql"))
            run_id = connection.execute(
                """
                INSERT INTO ingestion_runs (source_repository, source_snapshot, status)
                VALUES (%s, %s, 'running') RETURNING id
                """,
                (SOURCE_REPOSITORY, args.snapshot),
            ).fetchone()[0]
            connection.commit()

            files = iter_xml_files(repo_root)
            if args.limit is not None:
                files = files[: args.limit]
            for path in files:
                try:
                    law = parse_law_xml(path)
                    imported_provisions += upsert_law(connection, law, args.snapshot)
                    imported_laws += 1
                    connection.commit()
                except Exception as error:  # continue the corpus import, report samples
                    connection.rollback()
                    errors.append(f"{path}: {error}")

            connection.execute(
                """
                UPDATE ingestion_runs
                SET finished_at = now(), status = %s, imported_laws = %s,
                    imported_provisions = %s, error_count = %s, error_sample = %s
                WHERE id = %s
                """,
                (
                    "completed_with_errors" if errors else "completed",
                    imported_laws,
                    imported_provisions,
                    len(errors),
                    json.dumps(errors[:20]),
                    run_id,
                ),
            )
            connection.commit()
    finally:
        if workspace:
            shutil.rmtree(workspace, ignore_errors=True)

    return {
        "source": SOURCE_REPOSITORY,
        "snapshot": args.snapshot,
        "imported_laws": imported_laws,
        "imported_provisions": imported_provisions,
        "error_count": len(errors),
        "error_sample": errors[:20],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", "postgresql://germanlaw:germanlaw@localhost:5432/germanlaw"))
    parser.add_argument("--snapshot", default=os.environ.get("QUANTLAW_SNAPSHOT", dt.date.today().isoformat()))
    parser.add_argument("--repo-dir", default=os.environ.get("QUANTLAW_REPO_DIR"))
    parser.add_argument("--archive-template", default=os.environ.get("QUANTLAW_ARCHIVE_TEMPLATE", DEFAULT_ARCHIVE_TEMPLATE))
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2))
