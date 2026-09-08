"""Dataset cards: a compact description of uploaded tabular files for the system prompt."""

from __future__ import annotations

import json
from pathlib import Path

from app.agent.workspace import Workspace

TABULAR_EXT = {".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".json", ".jsonl"}


def dataset_card(path: Path, max_rows: int = 200_000) -> str | None:
    """Return a Markdown card for a tabular file, or None if it isn't one."""
    ext = path.suffix.lower()
    if ext not in TABULAR_EXT:
        return None
    try:
        import pandas as pd

        if ext in (".csv", ".tsv"):
            df = pd.read_csv(path, sep="\t" if ext == ".tsv" else None, engine="python", nrows=max_rows)
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(path, nrows=max_rows)
        elif ext == ".parquet":
            df = pd.read_parquet(path)
        elif ext == ".jsonl":
            df = pd.read_json(path, lines=True, nrows=max_rows)
        else:
            df = pd.read_json(path)
            if not isinstance(df, pd.DataFrame):
                return None
    except Exception as exc:  # noqa: BLE001
        return f"(could not preview {path.name}: {type(exc).__name__}: {exc})"
    lines = [f"- shape: {df.shape[0]} rows × {df.shape[1]} columns" + (" (preview capped)" if df.shape[0] >= max_rows else "")]
    cols = []
    for name, dtype in list(df.dtypes.items())[:60]:
        missing = int(df[name].isna().sum())
        cols.append(f"{name} ({dtype}{', ' + str(missing) + ' missing' if missing else ''})")
    lines.append("- columns: " + "; ".join(cols) + (" …" if df.shape[1] > 60 else ""))
    try:
        head = df.head(3).to_string(max_cols=12, max_colwidth=24)
        lines.append("- first rows:\n```\n" + head + "\n```")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


def datasets_block(workspace: Workspace) -> str:
    """Cards for every tabular upload, cached by (size, mtime) under .matopt/cards.json."""
    uploads = workspace.root / "uploads"
    cache_file = workspace.root / ".matopt" / "cards.json"
    try:
        cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    except Exception:  # noqa: BLE001
        cache = {}
    blocks = []
    changed = False
    for p in sorted(uploads.glob("*")):
        if not p.is_file() or p.suffix.lower() not in TABULAR_EXT:
            continue
        st = p.stat()
        key = workspace.relpath(p)
        stamp = f"{st.st_size}:{st.st_mtime_ns}"
        entry = cache.get(key)
        if not entry or entry.get("stamp") != stamp:
            card = dataset_card(p)
            entry = {"stamp": stamp, "card": card}
            cache[key] = entry
            changed = True
        if entry.get("card"):
            blocks.append(f"\n## Dataset `{key}`\n{entry['card']}")
    if changed:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache))
    return "".join(blocks)
