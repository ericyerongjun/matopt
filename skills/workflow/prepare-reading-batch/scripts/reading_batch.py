#!/usr/bin/env python3
"""Check a reading batch: a JSON array of {title, url} objects.

Mirrors common importer rules: titles and URLs are trimmed, titles must be
non-empty, URLs must be absolute http(s), and a URL already in the batch (or
in --existing, a JSON array or {"resources": [...]}) is a duplicate. Prints a
new / duplicate / invalid classification; --fix rewrites the file with the
trimmed, valid, de-duplicated rows in their original order. Exit 1 if any
row is invalid or duplicate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit


def norm_url(url: str) -> str | None:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return parts.geturl()


def load_rows(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("resources", data.get("items"))
    if not isinstance(data, list):
        raise ValueError("expected a JSON array of {title, url} objects")
    return data


def classify(rows: list, existing: set[str]) -> dict[str, list]:
    out = {"new": [], "duplicate": [], "invalid": []}
    seen = set(existing)
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            out["invalid"].append((i, row, "not an object"))
            continue
        title = str(row.get("title") or "").strip()
        url = norm_url(str(row.get("url") or ""))
        if not title:
            out["invalid"].append((i, row, "empty title"))
        elif url is None:
            out["invalid"].append((i, row, "URL must be absolute http(s)"))
        elif url in seen:
            out["duplicate"].append((i, row, "URL already present"))
        else:
            seen.add(url)
            out["new"].append((i, {**row, "title": title, "url": url}, ""))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Check a reading batch JSON file")
    p.add_argument("batch")
    p.add_argument("--existing", help="JSON of already saved resources")
    p.add_argument("--fix", action="store_true", help="rewrite the batch with valid new rows only")
    a = p.parse_args()
    try:
        rows = load_rows(Path(a.batch))
        existing = set()
        if a.existing:
            existing = {u for u in (norm_url(str(r.get("url") or "")) for r in load_rows(Path(a.existing))
                                     if isinstance(r, dict)) if u}
    except (OSError, ValueError) as exc:
        print(f"ERROR {exc}")
        return 1
    result = classify(rows, existing)
    for kind in ("new", "duplicate", "invalid"):
        print(f"{kind}: {len(result[kind])}")
        for i, row, why in result[kind]:
            label = row.get("title") if isinstance(row, dict) else row
            print(f"  [{i}] {label!r}{' — ' + why if why else ''}")
    if a.fix:
        Path(a.batch).write_text(json.dumps([r for _, r, _ in result["new"]], ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
        print(f"Rewrote {a.batch} with {len(result['new'])} rows")
    return 0 if not (result["duplicate"] or result["invalid"]) else 1


if __name__ == "__main__":
    sys.exit(main())
