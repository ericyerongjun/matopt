#!/usr/bin/env python3
"""Minimal OpenSpec helper: scaffold, check and archive spec-driven changes.

A dependency-free subset of the OpenSpec CLI (https://openspec.dev) for hosts
without Node. It reads and writes the same layout:

    openspec/specs/<capability>/spec.md
    openspec/changes/<change>/{.openspec.yaml,proposal.md,design.md,tasks.md,specs/<cap>/spec.md}
    openspec/changes/archive/YYYY-MM-DD-<change>/

Commands: init, new, list, status, validate, archive. Exit status 1 means a
check failed; messages go to stdout so an agent can read them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REQ_RE = re.compile(r"^### Requirement:\s*(.+?)\s*$", re.M)
SCENARIO_RE = re.compile(r"^#### Scenario:\s*(.+?)\s*$", re.M)
BAD_SCENARIO_RE = re.compile(r"^(#{1,3}|#{5,})\s*Scenario:", re.M)
SECTION_RE = re.compile(r"^## (.+?)\s*$", re.M)
TASK_RE = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s+(.*)$", re.M)
DELTA_SECTIONS = ("ADDED Requirements", "MODIFIED Requirements", "REMOVED Requirements", "RENAMED Requirements")
RENAME_RE = re.compile(r"-\s*FROM:\s*`?###\s*Requirement:\s*(.+?)`?\s*\n\s*-\s*TO:\s*`?###\s*Requirement:\s*(.+?)`?\s*$", re.M)

PROPOSAL = """## Why
<!-- The problem or opportunity, in one or two sentences. -->

## What Changes
<!-- Bullet list of behavior changes. Mark breaking ones **BREAKING**. -->

## Capabilities
- `{cap}`: <!-- new or modified capability -->

## Impact
<!-- Affected code, APIs, storage, users. -->

## Open Questions
<!-- Behavior the user still has to decide. Remove the section when empty. -->
"""
DESIGN = """## Context
<!-- Constraints and the current state. -->

## Decisions
<!-- Each decision with the alternative you rejected and why. -->

## Risks / Trade-offs
<!-- What could go wrong and the mitigation. -->
"""
TASKS = """## 1. Setup
- [ ] 1.1 <!-- first concrete task -->

## 2. Behavior
- [ ] 2.1 <!-- one task per requirement or scenario group -->

## 3. Verification
- [ ] 3.1 Check every scenario (tests and, for UI, in the browser)
"""
DELTA = """## ADDED Requirements

### Requirement: <!-- name -->
The system SHALL <!-- behavior -->.

#### Scenario: <!-- name -->
- **WHEN** <!-- condition -->
- **THEN** <!-- observable outcome -->
"""


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.errors

    def print(self, label: str) -> None:
        for e in self.errors:
            print(f"ERROR   {label}: {e}")
        for w in self.warnings:
            print(f"WARNING {label}: {w}")
        if self.ok():
            print(f"OK      {label}")


def root_dir(args) -> Path:
    return Path(args.root).resolve() / "openspec"


def change_dir(base: Path, name: str) -> Path:
    if not NAME_RE.match(name):
        sys.exit(f"Invalid change name {name!r}: use kebab-case (e.g. add-batch-import)")
    return base / "changes" / name


def split_requirements(text: str) -> dict[str, str]:
    """Map requirement name -> its block (header included), in document order."""
    blocks: dict[str, str] = {}
    matches = list(REQ_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        nxt = SECTION_RE.search(text, m.end())
        if nxt and nxt.start() < end:
            end = nxt.start()
        blocks[m.group(1).strip()] = text[m.start():end].rstrip() + "\n"
    return blocks


def sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    matches = list(SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[m.group(1).strip()] = text[m.end():end]
    return out


def check_requirements(text: str, report: Report, where: str, *, removed: bool = False) -> None:
    if BAD_SCENARIO_RE.search(text):
        report.errors.append(f"{where}: scenarios must use exactly four '#' ('#### Scenario:')")
    for name, block in split_requirements(text).items():
        if removed:
            continue
        body = REQ_RE.sub("", block, count=1)
        first = SCENARIO_RE.split(body)[0]
        if not re.search(r"\b(SHALL|MUST)\b", first):
            report.errors.append(f"{where}: requirement '{name}' must state SHALL or MUST")
        if not SCENARIO_RE.search(block):
            report.errors.append(f"{where}: requirement '{name}' has no '#### Scenario:'")
        for scen in re.split(r"^#### Scenario:", block, flags=re.M)[1:]:
            if "WHEN" not in scen or "THEN" not in scen:
                title = scen.strip().splitlines()[0] if scen.strip() else "?"
                report.warnings.append(f"{where}: scenario '{title}' should have WHEN and THEN bullets")
        if "<!--" in block:
            report.errors.append(f"{where}: requirement '{name}' still has template placeholders")


def validate_baseline(path: Path) -> Report:
    report = Report()
    text = path.read_text(encoding="utf-8")
    if "## Requirements" not in text:
        report.errors.append("missing '## Requirements' section")
    if not REQ_RE.search(text):
        report.warnings.append("no requirements yet")
    check_requirements(text, report, path.parent.name)
    return report


def validate_change(cdir: Path) -> Report:
    report = Report()
    if not cdir.is_dir():
        report.errors.append(f"no change folder {cdir}")
        return report
    for name in ("proposal.md", "tasks.md"):
        if not (cdir / name).is_file():
            report.errors.append(f"missing {name}")
    deltas = sorted((cdir / "specs").glob("*/spec.md"))
    if not deltas:
        report.errors.append("no spec delta under specs/<capability>/spec.md")
    for delta in deltas:
        cap = delta.parent.name
        text = delta.read_text(encoding="utf-8")
        found = sections(text)
        known = [s for s in found if s in DELTA_SECTIONS]
        if not known:
            report.errors.append(f"{cap}: delta needs one of: " + ", ".join(f"'## {s}'" for s in DELTA_SECTIONS))
        for title in found:
            if title not in DELTA_SECTIONS and title.endswith("Requirements"):
                report.errors.append(f"{cap}: unknown delta section '## {title}'")
        for title in ("ADDED Requirements", "MODIFIED Requirements"):
            if title in found:
                check_requirements(found[title], report, f"{cap} ({title.split()[0]})")
        if "RENAMED Requirements" in found and not RENAME_RE.search(found["RENAMED Requirements"]):
            report.errors.append(f"{cap}: RENAMED needs '- FROM: ### Requirement: Old' then '- TO: ### Requirement: New'")
        baseline = cdir.parents[1] / "specs" / cap / "spec.md"
        existing = split_requirements(baseline.read_text(encoding="utf-8")) if baseline.is_file() else {}
        pairs = [(m.group(1).strip(), m.group(2).strip()) for m in RENAME_RE.finditer(found.get("RENAMED Requirements", ""))]
        renamed = {new for _, new in pairs}
        sources = {old for old, _ in pairs}
        for old, new in pairs:
            if old not in existing:
                report.errors.append(f"{cap}: RENAMED requirement '{old}' is not in the baseline")
            # A destination may not overwrite a requirement that stays, nor be used twice.
            if new in existing and new not in sources:
                report.errors.append(f"{cap}: RENAMED destination '{new}' already exists in the baseline")
        for new in renamed:
            if sum(1 for _, n in pairs if n == new) > 1:
                report.errors.append(f"{cap}: two requirements are RENAMED to '{new}'")
        if len(sources) != len(pairs):
            report.errors.append(f"{cap}: a requirement is RENAMED more than once")
        existing = {**existing, **dict.fromkeys(renamed, "")}
        for title in ("MODIFIED Requirements", "REMOVED Requirements"):
            for name in split_requirements(found.get(title, "")):
                if name not in existing:
                    report.errors.append(f"{cap}: {title.split()[0]} requirement '{name}' is not in the baseline")
        for name in split_requirements(found.get("ADDED Requirements", "")):
            if name in existing:
                report.errors.append(f"{cap}: ADDED requirement '{name}' already exists; use MODIFIED")
    tasks = cdir / "tasks.md"
    if tasks.is_file() and not TASK_RE.search(tasks.read_text(encoding="utf-8")):
        report.errors.append("tasks.md has no '- [ ]' checkbox tasks")
    return report


def task_progress(cdir: Path) -> tuple[int, int, list[str]]:
    path = cdir / "tasks.md"
    if not path.is_file():
        return 0, 0, []
    rows = TASK_RE.findall(path.read_text(encoding="utf-8"))
    open_ = [t for mark, t in rows if mark == " "]
    return len(rows) - len(open_), len(rows), open_


def merge_delta(baseline_text: str | None, delta_text: str, cap: str) -> str:
    found = sections(delta_text)
    if baseline_text is None:
        baseline_text = f"# {cap} Specification\n\n## Purpose\nTBD: describe the purpose of {cap}.\n\n## Requirements\n"
    head, _, tail = baseline_text.partition("## Requirements")
    reqs = split_requirements(tail)
    after = ""
    nxt = SECTION_RE.search(tail)
    if nxt:
        after = tail[nxt.start():]
    renames = {m.group(1).strip(): m.group(2).strip() for m in RENAME_RE.finditer(found.get("RENAMED Requirements", ""))}
    for old, new in renames.items():
        if old in reqs:
            reqs = {(new if k == old else k): (REQ_RE.sub(f"### Requirement: {new}", v, count=1) if k == old else v)
                    for k, v in reqs.items()}
    for name in split_requirements(found.get("REMOVED Requirements", "")):
        reqs.pop(renames.get(name, name), None)
    for name, block in split_requirements(found.get("MODIFIED Requirements", "")).items():
        # OpenSpec applies RENAMED first; a MODIFIED block may use either name.
        target = renames.get(name, name)
        reqs[target] = REQ_RE.sub(f"### Requirement: {target}", block, count=1)
    for name, block in split_requirements(found.get("ADDED Requirements", "")).items():
        reqs[name] = block
    body = "\n".join(b.rstrip() + "\n" for b in reqs.values())
    return head.rstrip() + "\n\n## Requirements\n\n" + body + ("\n" + after if after else "")


def cmd_init(args) -> int:
    base = root_dir(args)
    for sub in ("specs", "changes/archive"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    print(f"Initialized {base}")
    return 0


def cmd_new(args) -> int:
    base = root_dir(args)
    cdir = change_dir(base, args.name)
    if cdir.exists():
        print(f"Change {args.name} already exists at {cdir}")
        return 1
    cap = args.capability or args.name
    if not NAME_RE.match(cap):
        sys.exit(f"Invalid capability name {cap!r}")
    (cdir / "specs" / cap).mkdir(parents=True)
    (base / "specs").mkdir(parents=True, exist_ok=True)
    (base / "changes" / "archive").mkdir(parents=True, exist_ok=True)
    (cdir / ".openspec.yaml").write_text(f"schema: spec-driven\ncreated: {dt.date.today().isoformat()}\n", encoding="utf-8")
    (cdir / "proposal.md").write_text(PROPOSAL.format(cap=cap), encoding="utf-8")
    (cdir / "design.md").write_text(DESIGN, encoding="utf-8")
    (cdir / "tasks.md").write_text(TASKS, encoding="utf-8")
    (cdir / "specs" / cap / "spec.md").write_text(DELTA, encoding="utf-8")
    print(f"Created {cdir.relative_to(base.parent)} (capability {cap}). Fill every file; replace each <!-- placeholder -->.")
    return 0


def cmd_list(args) -> int:
    base = root_dir(args)
    changes = sorted(p for p in (base / "changes").glob("*") if p.is_dir() and p.name != "archive")
    specs = sorted(p.parent.name for p in (base / "specs").glob("*/spec.md"))
    print("Changes:" if changes else "Changes: none")
    for c in changes:
        done, total, _ = task_progress(c)
        print(f"  {c.name}  {done}/{total} tasks")
    print("Specs: " + (", ".join(specs) if specs else "none"))
    archived = sorted(p.name for p in (base / "changes" / "archive").glob("*") if p.is_dir())
    if archived:
        print("Archived: " + ", ".join(archived))
    return 0


def cmd_status(args) -> int:
    cdir = change_dir(root_dir(args), args.name)
    if not cdir.is_dir():
        print(f"No change {args.name}")
        return 1
    done, total, open_ = task_progress(cdir)
    print(f"Change: {args.name}  Schema: spec-driven  Progress: {done}/{total} tasks complete")
    for t in open_:
        print(f"  [ ] {t}")
    proposal = (cdir / "proposal.md").read_text(encoding="utf-8") if (cdir / "proposal.md").is_file() else ""
    questions = sections(proposal).get("Open Questions", "").strip()
    if questions and "<!--" not in questions:
        print("Open questions:\n" + questions)
    return 0


def cmd_validate(args) -> int:
    base = root_dir(args)
    ok = True
    if args.specs or not args.name:
        for spec in sorted((base / "specs").glob("*/spec.md")):
            r = validate_baseline(spec)
            r.print(f"spec {spec.parent.name}")
            ok &= r.ok()
    if args.name:
        r = validate_change(change_dir(base, args.name))
        r.print(f"change {args.name}")
        ok &= r.ok()
    elif not args.specs:
        for c in sorted(p for p in (base / "changes").glob("*") if p.is_dir() and p.name != "archive"):
            r = validate_change(c)
            r.print(f"change {c.name}")
            ok &= r.ok()
    return 0 if ok else 1


def cmd_archive(args) -> int:
    base = root_dir(args)
    cdir = change_dir(base, args.name)
    report = validate_change(cdir)
    report.print(f"change {args.name}")
    if not report.ok():
        return 1
    done, total, open_ = task_progress(cdir)
    if open_ and not args.force:
        print(f"Refusing to archive: {len(open_)} of {total} tasks are unchecked (pass --force if the user agrees).")
        return 1
    synced = []
    for delta in sorted((cdir / "specs").glob("*/spec.md")):
        cap = delta.parent.name
        target = base / "specs" / cap / "spec.md"
        merged = merge_delta(target.read_text(encoding="utf-8") if target.is_file() else None,
                             delta.read_text(encoding="utf-8"), cap)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(merged, encoding="utf-8")
        r = validate_baseline(target)
        r.print(f"spec {cap}")
        synced.append(f"{cap} ({len(split_requirements(merged))} requirements)")
    dest = base / "changes" / "archive" / f"{dt.date.today().isoformat()}-{args.name}"
    if dest.exists():
        print(f"Archive target {dest} already exists")
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(cdir), str(dest))
    print(f"Archive complete. Change: {args.name}  Archived to: {dest.relative_to(base.parent)}")
    print(f"Tasks: {done}/{total}. Specs synced: {', '.join(synced) or 'none'}. Application code was not changed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", default=".", help="project root (default: current directory)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    n = sub.add_parser("new"); n.add_argument("name"); n.add_argument("--capability")
    sub.add_parser("list")
    s = sub.add_parser("status"); s.add_argument("name")
    v = sub.add_parser("validate"); v.add_argument("name", nargs="?"); v.add_argument("--specs", action="store_true")
    a = sub.add_parser("archive"); a.add_argument("name"); a.add_argument("--force", action="store_true")
    args = p.parse_args(argv)
    return {"init": cmd_init, "new": cmd_new, "list": cmd_list, "status": cmd_status,
            "validate": cmd_validate, "archive": cmd_archive}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
