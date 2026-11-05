#!/usr/bin/env python3
"""Convert other agents' commands and rules into Agent Skills + AGENTS.md (dry-run output folder)."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)


def parse(text: str) -> tuple[dict, str]:
    m = FM_RE.match(text)
    if not m:
        return {}, text.strip()
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, m.group(2).strip()


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "skill")[:64].rstrip("-")


def yaml_str(text: str) -> str:
    return json.dumps(" ".join(text.split())[:1024], ensure_ascii=False)


def write_skill(out: Path, name: str, description: str, body: str, manual: bool, source: Path, taken: set) -> str:
    base, n = name, 2
    while name in taken:
        name = f"{base}-{n}"[:64]
        n += 1
    taken.add(name)
    d = out / name
    d.mkdir(parents=True, exist_ok=True)
    front = [f"name: {name}", f"description: {yaml_str(description)}"]
    if manual:
        front.append("disable-model-invocation: true")
    front.append(f"metadata:\n  migrated-from: {json.dumps(str(source))}")
    (d / "SKILL.md").write_text("---\n" + "\n".join(front) + "\n---\n\n" + body.strip() + "\n", encoding="utf-8")
    return name


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=".")
    p.add_argument("--out", default="converted-skills")
    a = p.parse_args()
    root, out = Path(a.root).resolve(), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    taken: set[str] = set()
    report, agents = [], []

    commands = [*root.glob(".claude/commands/**/*.md"), *root.glob(".cursor/commands/*.md"), *root.glob(".codex/prompts/*.md")]
    for f in sorted(commands):
        meta, body = parse(f.read_text(encoding="utf-8", errors="replace"))
        rel = f.relative_to(root)
        sub = f.relative_to(root / ".claude" / "commands") if rel.parts[0] == ".claude" else Path(f.name)
        name = slug(str(sub.with_suffix("")).replace("/", "-"))
        desc = meta.get("description") or (body.splitlines()[0] if body else f"Command {name}")
        if re.search(r"\$ARGUMENTS|\$\d", body):
            body += ("\n\n## Arguments\nThe text typed after `/" + name + "` replaces `$ARGUMENTS` "
                     "(and `$1`, `$2`… are its whitespace-separated words).")
        desc = desc.rstrip().rstrip(".")
        made = write_skill(out, name, f"{desc}. Use for /{name}.", body, True, rel, taken)
        report.append(f"- command `{rel}` → skill `{made}`")

    for f in sorted(root.glob(".cursor/rules/**/*.mdc")):
        meta, body = parse(f.read_text(encoding="utf-8", errors="replace"))
        rel = f.relative_to(root)
        if meta.get("alwaysApply", "").lower() == "true":
            agents.append(f"## {f.stem}\n\n{body}")
            report.append(f"- always-apply rule `{rel}` → AGENTS.md")
            continue
        desc = meta.get("description") or ""
        globs = meta.get("globs") or ""
        if not desc and globs:
            desc = f"Rules for files matching {globs}."
        if not desc:
            desc = f"Project rule {f.stem}; load when the task concerns {f.stem.replace('-', ' ')}."
        made = write_skill(out, slug(f.stem), desc, body, False, rel, taken)
        report.append(f"- rule `{rel}` → skill `{made}`")

    for name in (".cursorrules", ".windsurfrules", ".github/copilot-instructions.md"):
        f = root / name
        if f.is_file():
            agents.append(f"## From {name}\n\n{f.read_text(encoding='utf-8', errors='replace').strip()}")
            report.append(f"- `{name}` → AGENTS.md")
    for f in sorted(root.glob(".windsurf/rules/*.md")):
        meta, body = parse(f.read_text(encoding="utf-8", errors="replace"))
        agents.append(f"## {f.stem}\n\n{body}")
        report.append(f"- `{f.relative_to(root)}` → AGENTS.md")

    if agents:
        (out / "AGENTS.md").write_text("# Migrated guidance\n\n" + "\n\n".join(agents) + "\n", encoding="utf-8")
    (out / "REPORT.md").write_text("# Migration report\n\n" + ("\n".join(report) or "Nothing found to convert.") + "\n",
                                   encoding="utf-8")
    print((out / "REPORT.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
