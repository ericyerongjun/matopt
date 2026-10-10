#!/usr/bin/env python3
"""Check the native ZCode frontend snapshot without downloading upstream code.

    python3 scripts/vendor_zcode_frontend.py --check
    python3 scripts/vendor_zcode_frontend.py --check --source-repo /path/to/ZCode

The manifest records immutable upstream Git blobs and the reviewed local SHA-256
for every file. Exact copies, provenance-banner copies, and the stylesheet's two
scan paths also reconstruct and verify the upstream blob offline. Adapted files
are explicitly identified; their local hashes check the reviewed adapter version,
not equivalence to upstream. --source-repo additionally verifies every original
blob against the pinned commit, and checks any literal license excerpt.

This is a read-only gate. Changes require a new reviewed manifest; the checker
never silently regenerates copied files or blesses changed adapters.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/reference/zcode-frontend-manifest.json"
MODES = {
    "exact-copy", "banner-only-copy", "css-path-adapted", "whole-file-adapted",
    "maintained-render-subtree", "extraction", "license-excerpt", "host-adapter",
    "host-asset", "provenance-map",
}
EQUIVALENT_MODES = {"exact-copy", "banner-only-copy", "css-path-adapted"}
HEX256 = re.compile(r"[0-9a-f]{64}\Z")
HEX160 = re.compile(r"[0-9a-f]{40}\Z")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("file paths must be nonempty relative POSIX paths")
    path = PurePosixPath(value)
    if value == "." or path.is_absolute() or ".." in path.parts or str(path) != value or "\\" in value or any(character in value for character in "\r\n\0"):
        raise ValueError(f"invalid relative path: {value!r}")
    return value


def contained_path(root: Path, value: str) -> Path:
    path = root / relative_path(value)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"path leaves the repository: {value}")
    return path


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or not HEX160.fullmatch(manifest.get("revision", "")):
        raise ValueError("manifest needs schema_version 1 and an immutable 40-character revision")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("manifest has no files")
    seen: set[str] = set()
    sources: dict[str, tuple[str, str]] = {}
    for entry in files:
        name = relative_path(entry.get("path"))
        if name in seen:
            raise ValueError(f"duplicate target in manifest: {name}")
        seen.add(name)
        mode = entry.get("mode")
        if mode not in MODES or not HEX256.fullmatch(entry.get("expected_sha256", "")):
            raise ValueError(f"invalid mode or target hash: {name}")
        upstream = entry.get("sources", [])
        if not isinstance(upstream, list):
            raise ValueError(f"sources must be a list: {name}")
        if mode in EQUIVALENT_MODES and len(upstream) != 1:
            raise ValueError(f"source-equivalent copy needs exactly one source: {name}")
        if mode in {"whole-file-adapted", "maintained-render-subtree", "extraction", "license-excerpt"} and not upstream:
            raise ValueError(f"derived file needs upstream provenance: {name}")
        for source in upstream:
            source_name = relative_path(source.get("path"))
            digest = source.get("sha256", "")
            blob = source.get("git_blob", "")
            if not HEX256.fullmatch(digest) or not HEX160.fullmatch(blob):
                raise ValueError(f"invalid source hashes: {source_name}")
            pair = (digest, blob)
            if source_name in sources and sources[source_name] != pair:
                raise ValueError(f"inconsistent upstream hashes: {source_name}")
            sources[source_name] = pair
    coverage = manifest.get("coverage", {})
    roots = coverage.get("roots", [])
    if not isinstance(roots, list) or not roots:
        raise ValueError("manifest needs coverage roots")
    for name in roots:
        relative_path(name)
    for pattern in coverage.get("ignore", []):
        relative_path(pattern)
    return manifest


def original_bytes(entry: dict[str, Any], target: bytes) -> bytes:
    """Undo only the declared mechanical changes for source-equivalent files."""
    mode = entry["mode"]
    if mode == "exact-copy":
        return target
    if mode == "banner-only-copy":
        prefix = entry.get("provenance_banner", "").encode("utf-8")
        if not prefix or not target.startswith(prefix):
            raise ValueError("declared provenance banner is missing")
        return target[len(prefix):]
    if mode == "css-path-adapted":
        replacements = entry.get("path_replacements", [])
        if not replacements:
            raise ValueError("stylesheet has no declared scan-path replacements")
        result = target
        for replacement in reversed(replacements):
            local = replacement["local"].encode("utf-8")
            source = replacement["upstream"].encode("utf-8")
            if not local or result.count(local) != 1:
                raise ValueError("declared stylesheet scan paths must occur exactly once")
            result = result.replace(local, source, 1)
        return result
    raise ValueError(f"{mode} is not a source-equivalent file")


def read_git_blobs(repo: Path, revision: str, paths: list[str]) -> dict[str, tuple[str, bytes]]:
    """Read immutable blobs in one small Git subprocess, including binary assets."""
    requests = "".join(f"{revision}:{name}\n" for name in paths).encode("utf-8")
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch"], input=requests,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise ValueError(f"cannot read pinned source repository: {result.stderr.decode('utf-8', errors='replace').strip()}")
    output = result.stdout
    cursor = 0
    blobs: dict[str, tuple[str, bytes]] = {}
    for name in paths:
        end = output.find(b"\n", cursor)
        if end < 0:
            raise ValueError(f"incomplete Git response for {name}")
        header = output[cursor:end].decode("utf-8", errors="replace").split()
        if len(header) != 3 or header[1] != "blob" or not HEX160.fullmatch(header[0]):
            raise ValueError(f"missing pinned Git blob: {name}")
        size = int(header[2])
        cursor = end + 1
        content = output[cursor:cursor + size]
        cursor += size
        if len(content) != size or output[cursor:cursor + 1] != b"\n":
            raise ValueError(f"incomplete Git blob: {name}")
        cursor += 1
        blobs[name] = (header[0], content)
    if cursor != len(output):
        raise ValueError("unexpected trailing Git batch output")
    return blobs


def check(manifest: dict[str, Any], root: Path, source_repo: Path | None = None) -> list[str]:
    errors: list[str] = []
    entries = manifest["files"]
    expected = {entry["path"] for entry in entries}
    coverage = manifest["coverage"]
    ignores = coverage.get("ignore", [])
    for name in coverage["roots"]:
        directory = contained_path(root, name)
        if not directory.is_dir():
            errors.append(f"missing coverage directory: {name}")
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            local_name = path.relative_to(root).as_posix()
            if any(PurePosixPath(local_name).match(pattern) for pattern in ignores):
                continue
            if local_name not in expected:
                errors.append(f"unrecorded file: {local_name}")
    blobs: dict[str, tuple[str, bytes]] = {}
    if source_repo is not None:
        names = sorted({source["path"] for entry in entries for source in entry.get("sources", [])})
        blobs = read_git_blobs(source_repo, manifest["revision"], names)
        verified: set[str] = set()
        for entry in entries:
            for source in entry.get("sources", []):
                name = source["path"]
                if name in verified:
                    continue
                verified.add(name)
                blob, content = blobs[name]
                if blob != source["git_blob"] or sha256(content) != source["sha256"]:
                    errors.append(f"upstream blob/hash mismatch: {name}")
    for entry in entries:
        name = entry["path"]
        path = contained_path(root, name)
        if not path.is_file():
            errors.append(f"missing target: {name}")
            continue
        target = path.read_bytes()
        if sha256(target) != entry["expected_sha256"]:
            errors.append(f"reviewed target hash mismatch: {name}")
        if entry["mode"] in EQUIVALENT_MODES:
            try:
                content = original_bytes(entry, target)
                source = entry["sources"][0]
                if sha256(content) != source["sha256"] or git_blob_id(content) != source["git_blob"]:
                    errors.append(f"source-equivalence mismatch: {name}")
            except (KeyError, ValueError) as exc:
                errors.append(f"source-equivalence error: {name}: {exc}")
        if source_repo is not None and entry["mode"] == "license-excerpt":
            source = entry["sources"][0]
            span = entry["literal_excerpt"]
            content = blobs[source["path"]][1]
            if content[span["byte_start"]:span["byte_end"]] != target:
                errors.append(f"literal upstream license excerpt mismatch: {name}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", required=True, help="check the reviewed frontend snapshot; never write files")
    parser.add_argument("--source-repo", type=Path, help="also verify immutable original blobs in an existing ZCode Git checkout")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(MANIFEST)
        errors = check(manifest, ROOT, args.source_repo)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ZCode frontend check failed: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"FAIL {error}", file=sys.stderr)
        return 1
    counts = Counter(entry["mode"] for entry in manifest["files"])
    detail = ", ".join(f"{count} {mode}" for mode, count in sorted(counts.items()))
    verification = "with pinned Git sources" if args.source_repo else "offline"
    print(f"ZCode frontend: {len(manifest['files'])} files verified {verification} ({detail}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
