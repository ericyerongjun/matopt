#!/usr/bin/env python3
"""Create and verify a committed, credential-free COMP4431 source archive."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import time
import zipfile


BRANCH = "codex/comp4431-assignment"
ROOT = Path(__file__).resolve().parents[1]
HANDOFF = "output/doc/MatOpt_Presentation_Handoff.docx"
BLOCKED_PARTS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".cache", "dist", "coverage", ".DS_Store",
}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def validate_path(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"Unsafe archive path: {name}")
    if BLOCKED_PARTS.intersection(path.parts):
        raise ValueError(f"Runtime/private path is tracked: {name}")
    if path.name != ".env.example" and (
        path.name == ".env" or path.name.startswith(".env.") or path.name.endswith(".env")
    ):
        raise ValueError(f"Environment file is tracked: {name}")
    if path.name == "mcp.json" or path.suffix in {".sqlite3", ".sqlite", ".db", ".log"}:
        raise ValueError(f"Private configuration or runtime file is tracked: {name}")
    if name.startswith(("backend/data/", "backend/static/", "backend/lean/")):
        raise ValueError(f"Local runtime data is tracked: {name}")
    if name.startswith("output/") and name != HANDOFF:
        raise ValueError(f"Unexpected output artifact is tracked: {name}")


def local_secrets() -> list[bytes]:
    """Use known local credential values only as exclusion checks; never print them."""
    secrets = []
    env = ROOT / "backend/.env"
    if env.exists():
        for line in env.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.lstrip().startswith("#") and any(
                marker in key.upper() for marker in ("KEY", "TOKEN", "PASSWORD", "SECRET")
            ):
                value = value.strip().strip("\"'")
                if len(value) >= 12:
                    secrets.append(value.encode())
    return secrets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/submission/COMP4431_Thursday_MatOpt_Source.zip")
    args = parser.parse_args()
    if git("branch", "--show-current").decode().strip() != BRANCH:
        raise SystemExit(f"Switch to the local {BRANCH} branch before packaging.")
    if git("diff", "HEAD", "--name-only"):
        raise SystemExit("Commit intended tracked changes before packaging; HEAD is the archive source.")
    commit = git("rev-parse", "HEAD").decode().strip()
    base = git("merge-base", "HEAD", "main").decode().strip()
    stamp = int(git("show", "-s", "--format=%ct", "HEAD").strip())
    zip_time = time.gmtime(max(stamp, 315532800))[:6]
    secrets = local_secrets()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    records: dict[str, dict] = {}
    # git archive reads committed blobs, so ignored/untracked files cannot enter.
    source = io.BytesIO(git("archive", "--format=tar", "HEAD"))
    with tempfile.TemporaryDirectory(prefix="matopt-package-") as tmp:
        staged = Path(tmp) / "source.zip"
        with tarfile.open(fileobj=source) as archive, zipfile.ZipFile(
            staged, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as result:
            for member in sorted(archive.getmembers(), key=lambda entry: entry.name):
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ValueError(f"Symlink or special entry cannot be packaged: {member.name}")
                validate_path(member.name)
                stream = archive.extractfile(member)
                assert stream is not None
                data = stream.read()
                private_key = re.search(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{32,}", data)
                if any(secret in data for secret in secrets) or private_key:
                    raise ValueError(f"Credential material detected in {member.name}; archive not published.")
                info = zipfile.ZipInfo("MatOpt/" + member.name, zip_time)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o100000 | member.mode) << 16
                result.writestr(info, data)
                records[member.name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for required in ("README.md", "LICENSE", "LICENSES/Apache-2.0-legacy.txt", HANDOFF,
                             "docker-compose.yml", "backend/.env.example", "frontend/package-lock.json"):
                if required not in records:
                    raise ValueError(f"Required submission file is missing: {required}")
            manifest = {"source_commit": commit, "production_base": base, "local_branch": BRANCH,
                        "files": records, "presentation_pptx": "Separate deliverable; not included"}
            info = zipfile.ZipInfo("MatOpt/SUBMISSION_MANIFEST.json", zip_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            result.writestr(info, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        with zipfile.ZipFile(staged) as archive:
            if archive.testzip() is not None:
                raise ValueError("ZIP integrity check failed.")
            expected = {"MatOpt/" + name for name in records} | {"MatOpt/SUBMISSION_MANIFEST.json"}
            if set(archive.namelist()) != expected or len(archive.namelist()) != len(expected):
                raise ValueError("Archive entry mismatch or duplicate.")
            for name, record in records.items():
                data = archive.read("MatOpt/" + name)
                if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                    raise ValueError(f"Archive hash mismatch: {name}")
        # Copy to the destination filesystem before atomic replacement.
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_bytes(staged.read_bytes())
        os.replace(temporary, output)
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(f"{checksum}  {output.name}\n")
    print(f"Verified {len(records)} committed files; archive: {output}")
    print(f"Source: {commit}; production base: {base}")
    print(f"SHA-256: {checksum}")


if __name__ == "__main__":
    main()
