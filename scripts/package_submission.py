#!/usr/bin/env python3
"""Create a verified COMP4431 source archive without private repository metadata."""

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
import zipfile


BRANCH = "codex/comp4431-assignment"
ROOT = Path(__file__).resolve().parents[1]
BLOCKED_PARTS = {
    ".git", ".github", ".codex", ".agents", ".claude", ".ssh", ".aws",
    "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".cache", "dist", "coverage", ".DS_Store", "__MACOSX",
}
ROOT_FILES = {
    ".dockerignore", ".gitignore", "CITATION.cff", "Dockerfile",
    "Dockerfile.dockerignore", "LICENSE", "NOTICE", "README.md",
    "THIRD_PARTY_NOTICES.md", "docker-compose.yml", "mcp.json.example",
}
SOURCE_TREES = {"backend", "frontend", "skills", "vendor", "LICENSES", "examples"}
SUBMISSION_DOCS = {
    "docs/FEATURES.md", "docs/DEMO.md", "docs/DEMO-VERIFICATION.md",
    "docs/DEVELOPMENT.md", "docs/PROTOCOL.md", "docs/INTELLIGENT-UI.md",
    "docs/MEMORY.md", "docs/CODEX-INTEGRATION.md", "docs/ZCODE-ADE-MIGRATION.md",
    "docs/reference/zcode-frontend-manifest.json",
    "docs/reference/zcode-frontend-provenance.md",
}
REQUIRED_FILES = {
    "README.md", "docs/FEATURES.md", "LICENSE", "LICENSES/README.md",
    "LICENSES/Apache-2.0.txt", "NOTICE", "THIRD_PARTY_NOTICES.md",
    "docker-compose.yml", "backend/.env.example", "backend/Dockerfile",
    "backend/requirements.txt", "backend/constraints.txt", "frontend/Dockerfile",
    "frontend/sandbox/Dockerfile", "frontend/package-lock.json",
    "docs/reference/zcode-frontend-manifest.json", "examples/demo/lensing.csv",
}
PRIVATE_MARKERS = (
    b"github.com/ericyerongjun/matopt", b"codex/comp4431-assignment",
    b"/Users/eric/", b"Apache-2.0-legacy.txt",
)


def included(name: str) -> bool:
    """Curate the source release; omit maintainer notes, traces and handoff files."""
    path = PurePosixPath(name)
    if name in ROOT_FILES or name in SUBMISSION_DOCS:
        return True
    if path.parts[0] in SOURCE_TREES:
        return True
    if name in {"evals/README.md", "evals/math_core.jsonl"}:
        return True
    if name.startswith("scripts/"):
        return name not in {"scripts/package_submission.py", "scripts/tests/test_package_submission.py"}
    return False


def submission_bytes(name: str, data: bytes) -> bytes:
    # Citation metadata remains useful without exposing the private repository URL.
    if name == "CITATION.cff":
        return b"".join(line for line in data.splitlines(keepends=True)
                        if not line.startswith(b"repository-code:"))
    return data


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
    if name.startswith("output/"):
        raise ValueError(f"Unexpected output artifact is tracked: {name}")


def validate_content(name: str, data: bytes, secrets: list[bytes], commits: set[bytes]) -> None:
    private_key = re.search(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{32,}", data)
    if any(secret in data for secret in secrets) or private_key:
        raise ValueError(f"Credential material detected in {name}; archive not published.")
    if any(marker in data for marker in PRIVATE_MARKERS):
        raise ValueError(f"Private repository metadata detected in {name}; archive not published.")
    # Check first-party commit references, not public upstream pins or file hashes.
    if b"\0" not in data:
        for match in re.finditer(rb"(?<![0-9a-f])[0-9a-f]{7,40}(?![0-9a-f])", data):
            value = match.group()
            if value in commits:
                raise ValueError(f"Internal commit reference detected in {name}; archive not published.")


def local_secrets() -> list[bytes]:
    """Use known local credential values only as exclusion checks; never print them."""
    secrets = []
    for env in (ROOT / "backend/.env", ROOT / ".env"):
        if not env.exists():
            continue
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
    commits = {sha[:length] for sha in git("rev-list", "--all").splitlines()
               for length in range(7, 41)}
    # A release date, not a Git commit timestamp, is exposed in the ZIP metadata.
    zip_time = (2026, 11, 17, 0, 0, 0)
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
                if member.isdir() or not included(member.name):
                    continue
                if not member.isfile():
                    raise ValueError(f"Symlink or special entry cannot be packaged: {member.name}")
                validate_path(member.name)
                stream = archive.extractfile(member)
                assert stream is not None
                data = submission_bytes(member.name, stream.read())
                validate_content(member.name, data, secrets, commits)
                info = zipfile.ZipInfo("MatOpt/" + member.name, zip_time)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o100000 | member.mode) << 16
                result.writestr(info, data)
                records[member.name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for required in REQUIRED_FILES:
                if required not in records:
                    raise ValueError(f"Required submission file is missing: {required}")
            manifest = {"schema_version": 1, "package": "COMP4431 Thursday MatOpt source",
                        "files": records}
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
    print(f"Private maintainer record only (not in ZIP): source {commit}")
    print(f"SHA-256: {checksum}")


if __name__ == "__main__":
    main()
